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

from moviepy import VideoFileClip

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPixmap, QPainter, QColor, QSurfaceFormat
from PySide6.QtCore import QTimer, Qt, QUrl

from PIL import Image

from src.ResourceLoader import ResourceLoader
from src.Tile import Weight, MetaTile, Tile
from src.TileImage import TileImage
from src.TileVideo import TileVideo

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
        #self.load_assets("assets")
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

        self._video_files = []

    @staticmethod
    def get_video_dim(file_path: str):
        clip = VideoFileClip(file_path)

        # Get dimensions using .size (returns tuple) or .w and .h
        width, height = clip.size

        return width, height

    def load_videos(self, video_dir: str):
        self._video_files = sorted(
            f for f in os.listdir(video_dir)
            if f.lower().endswith(".mp4") or f.lower().endswith(".avi") or f.lower().endswith(".mov")
        )

        for video_file in self._video_files:
            #full_path = os.path.join(video_dir, video_file)
            full_path = video_dir + "/" + video_file
            width, height = EndlessScrollWidget.get_video_dim(full_path)
            metaTile = MetaTile(weight=Weight(50.0), type="video", json_filename="", image_filename=full_path, width=width,
                                height=height, resource_loader=self._resourceLoader)
            self._meta_tiles.append(metaTile)

        logger.info(f"Loaded {len(self._video_files)} video files from {video_dir}")


    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------
    def load_assets(self, assets_dir: str) -> None:
        """Scan ./assets for .png files, read paired .json for weight."""
        if not os.path.isdir(assets_dir):
            logger.warning("Asset folder '%s' not found. Place .png + .json files there and re-run.", assets_dir)
            return

        png_files = sorted(
            f for f in os.listdir(assets_dir)
            if f.lower().endswith(".png") or f.lower().endswith(".jpg") or f.lower().endswith(".jpeg")
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

            metaTile = MetaTile(weight=weight, type="image", json_filename=json_path, image_filename=png_path, width=width, height=height, resource_loader=self._resourceLoader)
            self._meta_tiles.append(metaTile)

        if not self._meta_tiles:
            logging.error("No valid assets loaded. A black screen will be displayed.")

        random.shuffle(self._meta_tiles)

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
        mt.weight.value -= 1
        return mt

    def _spawn_tile(self, x: float) -> Optional[Tile]:
        meta_tile = self._weighted_pick()
        if meta_tile.type == "image":
            pixmap = self._resourceLoader.get_pixmap(meta_tile.image_filename)
            if pixmap:
                scaled = self._resize_pixmap(pixmap)
                return TileImage(meta_tile=meta_tile, x=x, h=self.height(),  pixmap=scaled)
        if meta_tile.type == "video":
            tile = TileVideo(video_filename=meta_tile.image_filename, x=x, h=self.height(), meta_tile=meta_tile)
            return tile
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
            tile_width = tile.get_width()
            x += tile_width + self.GAP_PX

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
            tile_width = tile.get_width()
            rightmost += tile_width + self.GAP_PX

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        #if not self._meta_tiles:
        #    return

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
            tile.draw(painter)

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
            original = self._resourceLoader.get_pixmap(tile.meta_tile.image_filename)
            if original and original.height() != target_h:
                #tile.pixmap = original.scaledToHeight(target_h, Qt.SmoothTransformation)
                tile.set_height(target_h)

        if not self._tiles:
            self._seed_tiles()
            self.update()
            return

        # Re-layout tiles sequentially to respect GAP_PX
        self._tiles.sort(key=lambda t: t.x)
        x = self._tiles[0].x  # preserve scroll position of leftmost tile
        for tile in self._tiles:
            tile.x = x
            x += tile.get_width() + self.GAP_PX
            tile.set_height(target_h)

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
    default_speed = 10.0

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
    #widget.load_assets("secret_assets")
    widget.load_assets("C:/Users/vae/Desktop/pix/copy/slideshow")
    widget.load_videos("C:/Users/vae/Desktop/pix/copy/slidevid")
    widget.show()

    print("Screensaver running; click / press any key to exit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
