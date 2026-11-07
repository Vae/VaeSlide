from dataclasses import dataclass
from typing import List

from PySide6.QtGui import QPainter

from src.ResourceLoader import ResourceLoader
from src.Tile import MetaTile
from src.HorizontalStrip import TileLayout


@dataclass(frozen=True)
class LayerConfig:
    """
    speed_factor: multiplier applied to the base scroll speed.
    overlap:      fraction of the screen height this layer overlaps the
                  previous (further back) layer. Ignored for the first layer.
    """
    speed_factor: float
    overlap: float = 0.0


class ScrollLayer:
    """One parallax layer: a TileLayout, a vertical offset and a speed."""

    def __init__(self, config: LayerConfig, resource_loader: ResourceLoader, gap_px: int = -1):
        self.config = config
        self.layout = TileLayout(resource_loader, gap_px)
        self.y = 0

    def set_meta_tiles(self, meta_tiles: List[MetaTile]) -> None:
        self.layout.set_meta_tiles(meta_tiles)

    def set_geometry(self, y: int, width: int, height: int) -> None:
        self.y = y
        self.layout.resize(width, height)

    def tick(self, base_speed: float) -> None:
        self.layout.scroll(base_speed * self.config.speed_factor)

    def draw(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(0, self.y)
        self.layout.draw(painter)
        painter.restore()