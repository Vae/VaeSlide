import json
import os
import logging
from typing import Optional

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap, QPainter, QImage
import numpy as np

from PIL import Image

from src.AssetLoader import AssetLoader
from src.AssetManager import AssetManager
from src.MediaCache import MediaCache
from src.Tile import MetaTile, Tile, Weight

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

            weight = Weight(10000.0)
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
    """
    An image tile. The scaled image loads in the background on the
    AssetLoader's worker threads. Until it arrives, the tile takes up its
    slot in the layout but draws nothing.
    """

    def __init__(self, meta_tile: MetaTile, x: float, h: int, asset_loader: AssetLoader):
        self._masking_enabled = False
        super().__init__(meta_tile=meta_tile, x=x, h=h)
        self._asset_loader = asset_loader
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