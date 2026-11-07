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
from PySide6.QtGui import QPainter, QColor, QSurfaceFormat
from PySide6.QtCore import QTimer, Qt

from PIL import Image

from src.ResourceLoader import ResourceLoader
from src.Tile import Weight, MetaTile
from src.ScrollLayer import ScrollLayer, LayerConfig

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class EndlessScrollWidget(QWidget):
    """
    The main screensaver widget.

    Draws several horizontally scrolling layers at different speeds to
    create a parallax effect. Each layer has its own tiles and overlaps
    the previous layer by a percentage of the screen height.
    """

    GAP_PX = -1  # gap between tiles (0 = seamless)

    # Back to front. overlap = fraction of screen height covered by the previous layer.
    DEFAULT_LAYERS = [
        LayerConfig(speed_factor=0.25, overlap=0.0),
        LayerConfig(speed_factor=0.6,  overlap=0.20),
        LayerConfig(speed_factor=1.0,  overlap=0.30),
    ]

    def __init__(self, fullscreen: bool, speed: float, layers: Optional[List[LayerConfig]] = None):
        super().__init__()

        self._fullscreen = fullscreen
        self._speed = speed
        self._resourceLoader = ResourceLoader()
        self._video_files = []

        # Master list; each layer receives its own copy in build_layers()
        self._meta_tiles: List[MetaTile] = []

        self._layers: List[ScrollLayer] = [
            ScrollLayer(cfg, self._resourceLoader, self.GAP_PX)
            for cfg in (layers or self.DEFAULT_LAYERS)
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
        for layer in self._layers:
            layer.tick(self._speed)
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
    default_speed = 1.0

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
    widget.build_layers()
    widget.show()

    print("Screensaver running; click / press any key to exit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
