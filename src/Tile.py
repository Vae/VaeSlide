from dataclasses import dataclass, field
from typing import List

from PySide6.QtGui import QPixmap


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

@dataclass
class Tile:
    """A single image tile being scrolled across the screen."""
    pixmap: QPixmap
    x: float  # left-edge position in pixels (can be fractional)
    metaTile: MetaTile  # just in case

    #subimages that have actually spawned
    #subimages: List[TileOverlay] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.pixmap.width()

    @property
    def right(self) -> float:
        return self.x + self.width
