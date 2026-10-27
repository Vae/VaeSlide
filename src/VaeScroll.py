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
    python screensaver.py --speed 2.0 # custom scroll speed (px/frame)
"""

import sys
import os
import json
import random
import argparse
import logging
from typing import List, Optional

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPixmap, QPainter, QColor, QSurfaceFormat
from PySide6.QtCore import QTimer, Qt

from PIL import Image

from src.ResourceLoader import ResourceLoader
from src.Tile import Weight, MetaTile, Tile

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

class EndlessScrollWidget(QWidget):
    """
    The main screensaver widget.

    Maintains a list of image tiles that scroll from right to left.
    Tiles that exit the left edge are recycled; new tiles are spawned
    on the right edge to keep the screen full — creating an endless scroll.
    """

    GAP_PX = -1  # gap between tiles (0 = seamless)

    def __init__(self, fullscreen: bool, speed: float):
        super().__init__()

        self._fullscreen = fullscreen
        self._speed = speed
        self._tiles: List[Tile] = []
        self._resourceLoader = ResourceLoader()

        #two containers, same content. Combine?
        self._meta_tiles: List[MetaTile] = []

        # Background colour visible in gaps (if any)
        self._bg_color = QColor("#0a0a0a")

        # Setup
        # self._assets = self._load_assets(["assets"])
        self.load_assets("assets")
        self._configure_window()
        self._seed_tiles()

        # Animation timer (~60 fps)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        # Make the widget react to any input
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------
    def load_assets(self, path: str) -> None:
        """Scan ./assets for .png files, read paired .json for weight."""
        assets_dir = path
        if not os.path.isdir(assets_dir):
            logger.warning("Asset folder '%s' not found. Place .png + .json files there and re-run.", assets_dir)
            return

        png_files = sorted(
            f for f in os.listdir(assets_dir)
            if f.lower().endswith(".png")
        )

        for png_name in png_files:
            base, _ = os.path.splitext(png_name)
            png_path = os.path.join(assets_dir, png_name)
            json_path = os.path.join(assets_dir, base + ".json")

            weight = Weight(1.0)
            width = 0
            height = 0
            if os.path.isfile(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as fh:
                        meta = json.load(fh)
                    weight = Weight(meta.get("weight", 1))
                    width = meta.get("width", None)
                    height = meta.get("height", None)
                except (json.JSONDecodeError, ValueError, OSError) as exc:
                    logging.warning(f"Bad JSON for {base}.json: {exc} — defaulting weight to 1")
            else: # fallback requires opening the image and getting the dimensions
                with Image.open(png_path) as img:
                    width, height = img.size

            metaTile = MetaTile(weight=weight, json_filename=json_path, image_filename=png_path, width=width, height=height)
            self._meta_tiles.append(metaTile)

        if not self._meta_tiles:
            logging.error("No valid assets loaded. A black screen will be displayed.")

        logging.info(f"Loaded {len(self._meta_tiles)} assets from '{assets_dir}'.")

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
    # Tile management
    # ------------------------------------------------------------------
    def _target_height(self) -> int:
        """Height that images should be scaled to (the visible screen height)."""
        h = self.height()
        if h > 0:
            return h
        # Widget may not have a size yet (before show)
        return QApplication.primaryScreen().virtualGeometry().height()

    def _target_width(self) -> int:
        w = self.width()
        if w > 0:
            return w
        return QApplication.primaryScreen().virtualGeometry().width()

    def _resize_pixmap(self, pixmap: QPixmap) -> QPixmap:
        """Scale pixmap to target height, preserving aspect ratio."""
        target_h = self._target_height()
        if pixmap.height() == target_h:
            return pixmap
        return pixmap.scaledToHeight(target_h, Qt.SmoothTransformation)

    def _weighted_pick(self) -> MetaTile:
        """Select a random image according to its weight."""
        weights = [tile.weight.value for tile in self._meta_tiles]
        mt = random.choices(self._meta_tiles, weights=weights, k=1)[0]
        return mt

    def _spawn_tile(self, x: float) -> Optional[Tile]:
        metaTile = self._weighted_pick()
        pixmap = self._resourceLoader.get_pixmap(metaTile.image_filename)
        if pixmap:
            scaled = self._resize_pixmap(pixmap)
            return Tile(pixmap=scaled, x=x, metaTile=metaTile)
        return None

    def _seed_tiles(self) -> None:
        """Pre-populate tiles so the screen is full on first paint."""
        if not self._meta_tiles:
            return

        target_w = self._target_width()
        x = 0.0
        while x < target_w + self._target_height():  # buffer beyond edge
            tile = self._spawn_tile(x)
            if tile is None:
                break
            self._tiles.append(tile)
            x += tile.width + self.GAP_PX

    def _cull_and_fill(self):
        # Cull tiles fully off-screen to the left
        self._tiles = [t for t in self._tiles if t.right > 0]

        # Fill the right side until we're past the visible area (+ buffer)
        target_w = self._target_width()
        buffer = self._target_height()
        if self._tiles:
            rightmost = max(t.right for t in self._tiles)
        else:
            rightmost = 0.0
        while rightmost < target_w + buffer:
            tile = self._spawn_tile(rightmost)
            if tile is None:
                break
            self._tiles.append(tile)
            rightmost += tile.width + self.GAP_PX

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if not self._meta_tiles:
            return

        # 1. Shift every tile left
        for tile in self._tiles:
            tile.x -= self._speed

        self._cull_and_fill()
        self.update()  # trigger repaint

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self._bg_color)

        for tile in self._tiles:
            px = int(tile.x)
            painter.drawPixmap(px, 0, tile.pixmap)

        self._cull_and_fill()
        self.update()
        painter.end()

    # ------------------------------------------------------------------
    # Window / monitor setup
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Re-scale all tiles when the window is resized."""
        super().resizeEvent(event)
        target_h = self._target_height()
        for tile in self._tiles:
            original = self._resourceLoader.get_pixmap(tile.metaTile.image_filename)
            if original and original.height() != target_h:
                tile.pixmap = original.scaledToHeight(target_h, Qt.SmoothTransformation)

        if not self._tiles:
            self._seed_tiles()
            self.update()
            return

        # Re-layout tiles sequentially to respect GAP_PX
        self._tiles.sort(key=lambda t: t.x)
        x = self._tiles[0].x  # preserve scroll position of leftmost tile
        for tile in self._tiles:
            tile.x = x
            x += tile.width + self.GAP_PX

        self._cull_and_fill()

    # ------------------------------------------------------------------
    # Close on any input (screensaver behaviour)
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    #def mouseMoveEvent(self, event) -> None:  # noqa: N802
    #    self._shutdown()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def _shutdown(self) -> None:
        self._timer.stop()
        QApplication.quit()


# ======================================================================
# Entry point
# ======================================================================

def main() -> None:
    default_speed = 0.5

    parser = argparse.ArgumentParser(description="VaeSlide")
    parser.add_argument("--window", action="store_true",
                        help="Run in a window instead of fullscreen (for testing)")
    parser.add_argument("--speed", type=float, default=default_speed,
                        help=f"Scroll speed in pixels per frame (default: {default_speed})")
    args = parser.parse_args()

    fullscreen = not args.window
    #testing:
    fullscreen = False

    surface_format = QSurfaceFormat()
    surface_format.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface_format)

    # Force no-decoration for fullscreen
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    widget = EndlessScrollWidget(fullscreen=fullscreen, speed=args.speed)
    widget.load_assets("secret_assets")
    widget.show()

    print("Screensaver running; click / press any key to exit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
