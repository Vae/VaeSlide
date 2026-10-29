from dataclasses import dataclass, field
from typing import List

from PySide6.QtGui import QPixmap, QPainter

from src.ResourceLoader import ResourceLoader


@dataclass
class Weight:
    """A weight value for an image."""
    value: float = 1.0

@dataclass
class MetaTile:
    """A loaded image with its display weight."""
    width: int
    height: int
    weight: Weight
    image_filename: str
    json_filename: str
    resource_loader: ResourceLoader


@dataclass
class Tile:
    """A single image tile being scrolled across the screen."""
    x: float  # left-edge position in pixels (can be fractional)
    meta_tile: MetaTile  # just in case

    def draw(self, painter: QPainter):
        pass

    @property
    def width(self) -> int:
        return 1000

    @property
    def right(self) -> float:
        return 1000

    def set_height(self, target_h: int):
        pass