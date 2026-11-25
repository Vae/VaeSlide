#!/usr/bin/env python3
"""
Endless Scroll Screensaver
==========================
A PySide6-based screensaver featuring seamless horizontal parallax scrolling.
Asset folders come from assets.json and settings from settings.json.

Usage (from the repo root):
    python -m src.VaeScroll             # fullscreen across all monitors
    python -m src.VaeScroll --window    # windowed mode for testing
    python -m src.VaeScroll --speed 2.0 # override "global speed" from settings.json
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QColor, QSurfaceFormat
from PySide6.QtCore import QTimer, Qt

from src import Diagnostics
from src.AssetLoader import AssetLoader
from src.AssetScanner import read_asset_sources
from src.ImageCache import ImageCache
from src.MediaCache import MediaCache
from src.ScrollLayer import ScrollLayer, LayerConfig, parse_layer_configs
from src.Settings import Settings, get_settings
from src.Tile import MetaTile, TileContext
from src.TileTypes import TILE_TYPES

logger = logging.getLogger(__name__)

def load_layer_configs() -> List[LayerConfig]:
    """Layer configs from settings.json, falling back to the built-in defaults."""
    configs = parse_layer_configs(get_settings().get("layers"))
    if not configs:
        logger.error("No valid layers in settings.json; using default layers.")
        configs = parse_layer_configs(Settings.DEFAULTS["layers"])
    return configs


class EndlessScrollWidget(QWidget):
    """
    The main screensaver widget.

    Draws several horizontally scrolling layers at different speeds to
    create a parallax effect. Each layer has its own tiles and overlaps
    the previous layer by a percentage of the screen height.
    """

    def __init__(self, fullscreen: bool, speed_scale: Optional[float] = None,
                 layers: Optional[List[LayerConfig]] = None):
        super().__init__()

        self._fullscreen = fullscreen
        self._settings = get_settings()
        # Multiplier on every layer's speed; an explicit argument overrides settings.json
        self._speed_scale = speed_scale if speed_scale is not None else self._settings.get_number("global speed")
        self._image_cache = ImageCache()
        # Image decoding and scaling run on this pool so the GUI thread never blocks on disk
        self._asset_loader = AssetLoader(self._image_cache,
                                         self._settings.get_number("asset loader threads", int))
        # Covers every way out (window close, any-key quit) so queued loads are dropped
        QApplication.instance().aboutToQuit.connect(self._asset_loader.shutdown)
        self._mediaCache = MediaCache(os.path.join(os.path.dirname(__file__), "..", ".media_cache.json"))

        # Master list; each layer receives its own copy in build_layers()
        self._meta_tiles: List[MetaTile] = []

        # Gap and swap settings come from strip_config() in HorizontalStrip.py
        tile_ctx = TileContext(asset_loader=self._asset_loader)
        self._layers: List[ScrollLayer] = [
            ScrollLayer(cfg, tile_ctx) for cfg in (layers or load_layer_configs())
        ]

        # Background color visible in gaps (if any)
        self._bg_color = QColor("#0a0a0a")

        self._configure_window()

        # Animation timer (~60 fps)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        # Make the widget react to any input
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Window / monitor setup
    # ------------------------------------------------------------------
    def _configure_window(self) -> None:
        if self._fullscreen:
            screens = QApplication.screens()
            if screens:
                # virtualGeometry covers the union of all monitors
                vg = screens[0].virtualGeometry()
                self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
                self.setGeometry(vg)
            else:
                self.showFullScreen()
        else:
            self.setWindowTitle("Endless Scroll — Test Mode")
            self.resize(1280, 720)

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------
    def build_layers(self) -> None:
        """Give every layer its own tiles. Call after loading assets/videos."""
        for layer in self._layers:
            layer.set_meta_tiles(self._meta_tiles)
        self._layout_layers()

    def _layout_layers(self) -> None:
        """
        Stack the layers vertically so that layer i overlaps layer i-1 by
        overlap_i * screen height, and the whole stack exactly fills the screen.
        """
        screen_w = self.width()
        screen_h = self.height()
        if screen_w <= 0 or screen_h <= 0 or not self._layers:
            return

        count = len(self._layers)
        total_overlap = sum(l.config.overlap for l in self._layers[1:])
        band_h = max(1, int(screen_h * (1.0 + total_overlap) / count))

        y = 0.0
        for i, layer in enumerate(self._layers):
            if i > 0:
                y += band_h - layer.config.overlap * screen_h
            layer.set_geometry(int(y), screen_w, band_h)

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        # Hand finished background loads to their tiles before moving anything
        self._asset_loader.process_completed()
        for layer in self._layers:
            layer.tick(self._speed_scale)
        self.update()  # trigger repaint

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self._bg_color)

        # Back to front
        for layer in self._layers:
            layer.draw(painter)

        painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Re-scale and re-flow every layer when the window is resized."""
        super().resizeEvent(event)
        self._layout_layers()

    # ------------------------------------------------------------------
    # Close on any input (screensaver behaviour)
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Hit test all layers from front to back
        for layer in reversed(self._layers):
            tile = layer.hit_test(event.position().x(), event.position().y())
            if tile and event.button() == Qt.LeftButton:
                logger.info("Clicked tile: %s", tile.describe())
                return
            if (tile and event.button() == Qt.MiddleButton
                    and self._settings.get("archive on middle click")):
                if Path(tile.meta_tile.resource_filename).is_file():
                    self._archive(tile.meta_tile.resource_filename)
                else:
                    # e.g. procedural maze tiles have no file; don't fall through to quitting
                    logger.info("Tile %s has no file to archive.", tile.meta_tile.resource_filename)
                return

        self._shutdown()

    def _archive(self, filename: str) -> None:
        """Move an asset into the archive directory configured in settings.json."""
        archive_dir = self._settings.get("archive directory")
        if not archive_dir:
            logger.warning("Cannot archive %s: no \"archive directory\" set in settings.json.", filename)
            return
        try:
            # Without this, moving into a missing directory would rename the file instead
            os.makedirs(archive_dir, exist_ok=True)
            shutil.move(filename, archive_dir)
            logger.info("Archived asset: %s -> %s", filename, archive_dir)
        except (OSError, shutil.Error) as e:
            logger.error("Failed to archive %s to %s: %s", filename, archive_dir, e)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Wheel up speeds up the layer under the cursor; wheel down slows it down."""
        notches = event.angleDelta().y() / 120.0  # 120 = one standard wheel notch
        if notches == 0:
            return

        y = event.position().y()
        # Front to back, so the visible layer wins where layers overlap
        for index in reversed(range(len(self._layers))):
            layer = self._layers[index]
            if layer.contains_y(y):
                old_speed = layer.speed
                layer.adjust_speed(notches * self._settings.get_number("wheel speed step"))
                logger.info("Layer %d speed: %.2f -> %.2f px/frame (x%.2f global speed)",
                            index, old_speed, layer.speed, self._speed_scale)
                return

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def _shutdown(self) -> None:
        self._timer.stop()
        QApplication.quit()

    # ------------------------------------------------------------------
    # Asset loading (call before build_layers)
    # ------------------------------------------------------------------
    def load_assets(self, tile_type: str, path: Optional[str] = None) -> None:
        """Add the MetaTiles of one tile type, scanning `path` if that type needs a folder."""
        cls = TILE_TYPES.get(tile_type)
        if cls is None or cls.SCANNER is None:
            logger.error("Unknown tile type %r (known: %s); skipping %s.", tile_type, list(TILE_TYPES), path)
            return
        self._meta_tiles.extend(cls.SCANNER().scan(self._mediaCache, path))

    def load_procedural_assets(self) -> None:
        """Add the entries of every tile type that needs no folder (e.g. mazes)."""
        for tile_type, cls in TILE_TYPES.items():
            if cls.SCANNER is not None and not cls.SCANNER.NEEDS_PATH:
                self.load_assets(tile_type)


# ======================================================================
# Entry point
# ======================================================================
def main() -> None:
    Diagnostics.install()

    parser = argparse.ArgumentParser(description="VaeSlide")
    parser.add_argument("--window", action="store_true",
                        help="Run in a window instead of fullscreen (for testing)")
    parser.add_argument("--speed", type=float, default=None,
                        help="Override \"global speed\" from settings.json (multiplier on every layer's speed)")
    args = parser.parse_args()

    fullscreen = not args.window

    # Temp for testing: force non-fullscreen
    fullscreen = False

    surface_format = QSurfaceFormat()
    surface_format.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface_format)

    # Force no-decoration for fullscreen
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    widget = EndlessScrollWidget(fullscreen=fullscreen, speed_scale=args.speed)

    # Asset folders from assets.json (missing and "#"-disabled folders are skipped)
    for tile_type, path in read_asset_sources():
        widget.load_assets(tile_type, path)
    widget.load_procedural_assets()

    widget.build_layers()
    widget.show()

    print("Slideshow running; press any key to exit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
