
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter, QImage
import numpy as np
from src.Tile import MetaTile, Tile

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".xpm"}

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

class TileImage(Tile):
    """Holds the pixmap for a tile and provides a generic draw method."""

    def __init__(self, meta_tile: MetaTile, x: float, h: int, pixmap: QPixmap):
        self._masking_enabled = False
        super().__init__(meta_tile=meta_tile, x=x, h=h)
        if self._masking_enabled:
            self._pixmap = _make_white_transparent(pixmap)
        else:
            self._pixmap = pixmap

    def pixmap(self) -> QPixmap:
        return self._pixmap

    def get_width(self) -> int:
        return self._pixmap.width()

    @property
    def right(self) -> float:
        return self.x + self.get_width()

    def set_height(self, target_h: int):
        if self._masking_enabled:
            self._pixmap = _make_white_transparent(
                self.meta_tile.resource_loader.get_pixmap(self.meta_tile.resource_filename).scaledToHeight(target_h, Qt.SmoothTransformation)
            )
        else:
            self.meta_tile.resource_loader.get_pixmap(self.meta_tile.resource_filename).scaledToHeight(target_h, Qt.SmoothTransformation)

    def draw(self, painter: QPainter) -> None:
        """Draw this tile image at position (x, y) on the given painter."""
        painter.drawPixmap(int(self.x), 0, self._pixmap)