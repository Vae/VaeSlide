#!/usr/bin/env python3
"""
Endless Scroll Screensaver
==========================
A PySide6-based screensaver featuring a seamless horizontal image scroll.
Images are loaded from the ./assets folder. Each .png has a paired .json
containing a "weight" field that controls how frequently it appears.

Usage:
    python screensaver.py             # fullscreen across all monitors
    python screensaver.py --window    # windowed mode for testing
    python screensaver.py --speed 2.0 # override "global speed" from settings.json
"""

import json
import argparse
import logging
import shutil
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QColor, QSurfaceFormat
from PySide6.QtCore import QTimer, Qt

from src.ResourceLoader import ResourceLoader
from src.AssetLoader import AssetLoader
from src.Tile import Weight, MetaTile
from src.MediaCache import MediaCache
from src.ScrollLayer import ScrollLayer, LayerConfig, parse_layer_configs
from src.TileImage import TileImageAssetManager
from src.TileVideo import TileVideoAssetManager
from src.Settings import Settings, get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

import faulthandler, sys, threading, logging, os
from PySide6 import QtCore

# Dumps the Python stack of ALL threads on SIGSEGV/SIGABRT/etc.
_crash_log = open("crash.log", "a", buffering=1)
faulthandler.enable(file=_crash_log, all_threads=True)

# Optional: dump all stacks every 60s, which helps if it hangs rather than crashes
# faulthandler.dump_traceback_later(60, repeat=True, file=_crash_log)

# Make sure uncaught exceptions in any thread are logged
sys.excepthook = lambda *a: logging.critical("Uncaught", exc_info=a)
threading.excepthook = lambda args: logging.critical(
    "Uncaught in thread %s", args.thread, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

# Route Qt's own warnings (which often precede a crash) into your log
def _qt_handler(mode, ctx, msg):
    logging.warning("Qt[%s] %s (%s:%s)", mode.name, msg, ctx.file, ctx.line)

QtCore.qInstallMessageHandler(_qt_handler)

# Verbose multimedia logging
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=true")
os.environ["QT_LOGGING_RULES"] = "*.debug=false"

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
        gap_px = self._settings.get_number("tile gap", int)
        swap_seconds = self._settings.get_number("tile swap seconds")
        swap_edge_margin = self._settings.get_number("tile swap edge margin")
        self._resourceLoader = ResourceLoader()
        # Image decoding and scaling run on this pool so the GUI thread never blocks on disk
        self._asset_loader = AssetLoader(self._resourceLoader,
                                         self._settings.get_number("asset loader threads", int))
        # Covers every way out (window close, any-key quit) so queued loads are dropped
        QApplication.instance().aboutToQuit.connect(self._asset_loader.shutdown)
        self._mediaCache = MediaCache(os.path.join(os.path.dirname(__file__), "..", ".media_cache.json"))

        # Master list; each layer receives its own copy in build_layers()
        self._meta_tiles: List[MetaTile] = []

        self._layers: List[ScrollLayer] = [
            ScrollLayer(cfg, self._asset_loader, gap_px, swap_seconds, swap_edge_margin)
            for cfg in (layers or load_layer_configs())
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

        self._video_files = []

        self._TileImageAssetManager = TileImageAssetManager()
        self._TileVideoAssetManager = TileVideoAssetManager()


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
                print(f"Clicked tile: {tile.meta_tile.resource_filename}")
                return
            if (tile and event.button() == Qt.MiddleButton
                    and self._settings.get("archive on middle click")
                    and Path(tile.meta_tile.resource_filename).is_file()):
                self._archive(tile.meta_tile.resource_filename)
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

    def _add_meta_tiles(self, meta_tiles: Optional[list[MetaTile]]):
        self._meta_tiles.extend(meta_tiles or [])

    def load_images(self, path: str):
        self._add_meta_tiles(self._TileImageAssetManager.load_assets(self._mediaCache, path))

    def load_videos(self, path: str):
        self._add_meta_tiles(self._TileVideoAssetManager.load_assets(self._mediaCache, path))

class VaeScroll:
    def __init__(self):
        self.media_cache = MediaCache()
        self.resource_loader = ResourceLoader()
        #self.endless_scroll_widget = EndlessScrollWidget(self.media_cache, self.resource_loader)


# ======================================================================
# Entry point
# ======================================================================
def main() -> None:
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

    # Load paths from settings.json
    assets_path = os.path.join(os.path.dirname("."), "assets.json")
    try:
        with open(assets_path, "r", encoding="utf-8") as f:
            assets_settings = json.load(f)
    except Exception as e:
        logger.error(f"Could not load settings.json: {e}")
        assets_settings = {"image_paths": [], "video_paths": []}

    for img_path in assets_settings.get("image_paths", []):
        if Path(img_path).is_dir and not img_path.startswith("#"):
            widget.load_images(img_path.replace('\\', '/'))

    for vid_path in assets_settings.get("video_paths", []):
        if Path(vid_path).is_dir and not vid_path.startswith("#"):
            widget.load_videos(vid_path.replace('\\', '/'))

    widget.build_layers()
    widget.show()

    print("Slideshow running; press any key to exit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
