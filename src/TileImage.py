import json
import os
import logging
from typing import Optional

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap, QPainter, QImage
import numpy as np

from PIL import Image

from src.AssetScanner import AssetScanner
from src.MediaCache import MediaCache
from src.Tile import MetaTile, Tile, TileContext, Weight

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".xpm"}
logger = logging.getLogger(__name__)

def _make_white_transparent(source: QImage) -> QImage:
    """Make pure-white pixels transparent. Safe to run on a worker thread."""
    if source.isNull():
        return source

    image = source.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    if width == 0 or height == 0:
        return source

    stride = image.bytesPerLine()
    if stride == width * 4:
        data = np.frombuffer(image.bits(), dtype=np.uint8, count=height * width * 4)
        pixels = data.reshape(height, width, 4)

        white = (
            (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
        )
        pixels[white, 3] = 0
    else:
        for y in range(height):
            for x in range(width):
                pixel = image.pixel(x, y)
                r = (pixel >> 16) & 0xFF
                g = (pixel >> 8) & 0xFF
                b = pixel & 0xFF
                if r == 255 and g == 255 and b == 255:
                    image.setPixel(x, y, 0x00FFFFFF)

    return image

def _image_size(path: str, media_cache: MediaCache) -> tuple[int, int]:
    """Image dimensions from the media cache, or by opening the file with Pillow."""
    cached = media_cache.get_dimensions(path)
    if cached:
        return cached
    with Image.open(path) as img:
        width, height = img.size
    media_cache.add_dimensions(path, width, height)
    return width, height


def _positive_int(value) -> Optional[int]:
    """`value` as an int if it is a positive number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


class TileImageScanner(AssetScanner):
    """Builds image MetaTiles from a folder, using optional <name>.json sidecars."""

    def scan(self, media_cache: MediaCache, path: Optional[str]) -> list[MetaTile]:
        if not path or not os.path.isdir(path):
            logger.warning("Image folder '%s' not found.", path)
            return []

        image_files = sorted(
            f for f in os.listdir(path)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        )

        meta_tiles = []
        for image_name in image_files:
            base, _ = os.path.splitext(image_name)
            image_path = os.path.join(path, image_name).replace('\\', '/')
            json_path = os.path.join(path, base + ".json").replace('\\', '/')

            weight = Weight(10000.0)
            width = height = None
            if os.path.isfile(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as fh:
                        meta = json.load(fh)
                    weight = Weight(meta.get("weight", 1))
                    width = _positive_int(meta.get("width"))
                    height = _positive_int(meta.get("height"))
                except (json.JSONDecodeError, ValueError, OSError, AttributeError) as exc:
                    logger.warning("Bad JSON for %s.json: %s; defaulting weight to 1", base, exc)
                    weight = Weight(1)
            if width is None or height is None:
                # No sidecar, or the sidecar has no usable width/height
                try:
                    width, height = _image_size(image_path, media_cache)
                except OSError as exc:
                    logger.warning("Skipping %s: could not read its size (%s)", image_path, exc)
                    continue

            meta_tiles.append(MetaTile(weight=weight, type=TileImage.TYPE, json_filename=json_path,
                                       resource_filename=image_path, width=width, height=height))

        if not meta_tiles:
            logger.warning("No images found in '%s'.", path)
        logger.info("Loaded %d images from '%s'.", len(meta_tiles), path)
        media_cache.save_cache()
        return meta_tiles


class TileImage(Tile):
    """
    An image tile. The scaled image loads in the background on the
    AssetLoader's worker threads. Until it arrives, the tile takes up its
    slot in the layout but draws nothing.
    """

    TYPE = "image"
    SCANNER = TileImageScanner

    def __init__(self, meta_tile: MetaTile, x: float, h: int, ctx: TileContext):
        self._masking_enabled = False
        super().__init__(meta_tile, x, h, ctx)
        self._asset_loader = ctx.asset_loader
        self._pixmap: Optional[QPixmap] = None
        self._removed = False
        self._request_pixmap()

    def _request_pixmap(self) -> None:
        requested_h = self._height
        self._asset_loader.request_scaled_image(
            self.meta_tile.resource_filename,
            requested_h,
            lambda image: self._on_image_loaded(image, requested_h),
            transform=_make_white_transparent if self._masking_enabled else None,
        )

    def _on_image_loaded(self, image: Optional[QImage], requested_h: int) -> None:
        """Runs on the GUI thread (via AssetLoader.process_completed)."""
        if self._removed or requested_h != self._height:
            return  # tile is gone, or a newer request for another height is pending
        if image is None:
            self.load_failed = True
            return
        self._pixmap = QPixmap.fromImage(image)

    def is_ready(self) -> bool:
        return self._pixmap is not None

    def set_height(self, target_h: int):
        if target_h == self._height and self._pixmap is not None:
            return
        self._height = target_h
        self._request_pixmap()  # keep drawing the old pixmap, stretched, until it arrives

    def draw(self, painter: QPainter) -> None:
        """Draw this tile image at position (x, y) on the given painter."""
        if self._pixmap is None:
            return
        if self._pixmap.height() == self._height:
            painter.drawPixmap(int(self.x), 0, self._pixmap)
        else:
            painter.drawPixmap(QRect(int(self.x), 0, self.width, self._height), self._pixmap)

    def removed(self):
        self._removed = True
        self._pixmap = None