from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter
from src.Tile import MetaTile, Tile

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".xpm"}

class TileImage(Tile):
    """Holds the pixmap for a tile and provides a generic draw method."""

    def __init__(self,  pixmap: QPixmap, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pixmap = pixmap

    @property
    def pixmap(self) -> QPixmap:
        return self._pixmap
    @property
    def width(self) -> int:
        return self.pixmap.width()
    @property
    def right(self) -> float:
        return self.x + self.width

    def set_height(self, target_h: int):
        self._pixmap = self.meta_tile.resource_loader.get_pixmap(self.meta_tile.image_filename).scaledToHeight(target_h, Qt.SmoothTransformation)

    def draw(self, painter: QPainter) -> None:
        """Draw this tile image at position (x, y) on the given painter."""
        painter.drawPixmap(int(self.x), 0, self._pixmap)
