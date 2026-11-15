import json
import os
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter, QImage
import numpy as np

from PIL import Image

from src.AssetManager import AssetManager
from src.MediaCache import MediaCache
from src.Tile import MetaTile, Tile, Weight

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".xpm"}
logger = logging.getLogger(__name__)

def _make_white_transparent(pixmap: QPixmap) -> QPixmap:
    if pixmap.isNull():
        return pixmap

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    if width == 0 or height == 0:
        return pixmap

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

    return QPixmap.fromImage(image)

class TileImageAssetManager(AssetManager):
    """Manages loading and caching of tile assets."""
    def load_assets(self, media_cache: MediaCache, path_to_assets: str) -> list[MetaTile]:
        path_to_images = path_to_assets
        meta_tiles = []
        if not os.path.isdir(path_to_images):
            logger.warning("Asset folder '%s' not found. Place .png + .json files there and re-run.", path_to_images)
            return

        png_files = sorted(
            f for f in os.listdir(path_to_images)
            if f.lower().endswith(".png") or f.lower().endswith(".jpg") or f.lower().endswith(".jpeg")
        )

        for png_name in png_files:
            base, _ = os.path.splitext(png_name)
            png_path = os.path.join(path_to_images, png_name).replace('\\', '/')
            json_path = os.path.join(path_to_images, base + ".json").replace('\\', '/')

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
                cached = media_cache.get_dimensions(png_path)
                if cached:
                    width, height = cached
                else:
                    with Image.open(png_path) as img:
                        width, height = img.size
                    media_cache.add_dimensions(png_path, width, height)

            metaTile = MetaTile(weight=weight, type="image", json_filename=json_path, resource_filename=png_path, width=width, height=height)
            meta_tiles.append(metaTile)

        if not meta_tiles:
            logging.error("No valid assets loaded. A black screen will be displayed.")

        logging.info(f"Loaded {len(meta_tiles)} assets from '{path_to_images}'.")
        media_cache.save_cache()
        return meta_tiles


class TileImage(Tile):
    """Holds the pixmap for a tile and provides a generic draw method."""

    def __init__(self, meta_tile: MetaTile, x: float, h: int, pixmap: QPixmap):
        self._masking_enabled = False
        super().__init__(meta_tile=meta_tile, x=x, h=h)
        if self._masking_enabled:
            self._pixmap = _make_white_transparent(pixmap)
        else:
            self._pixmap = pixmap

    def set_height(self, target_h: int):
        self._height = target_h
        if self._masking_enabled:
            self._pixmap = _make_white_transparent(
                self.meta_tile.resource_loader.get_pixmap(self.meta_tile.resource_filename).scaledToHeight(target_h, Qt.SmoothTransformation)
            )
        else:
            self._pixmap = self.meta_tile.resource_loader.get_pixmap(self.meta_tile.resource_filename).scaledToHeight(target_h, Qt.SmoothTransformation)

    def draw(self, painter: QPainter) -> None:
        """Draw this tile image at position (x, y) on the given painter."""
        painter.drawPixmap(int(self.x), 0, self._pixmap)