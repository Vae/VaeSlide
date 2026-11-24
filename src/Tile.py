from dataclasses import dataclass, field
from typing import List

from PySide6.QtGui import QPainter


@dataclass
class Weight:
    """A weight value for an image."""
    value: float = 1.0

@dataclass
class MetaTile:
    """A loaded image with its display weight."""
    weight: Weight
    type: str

    width: int
    height: int

    resource_filename: str
    json_filename: str


class Tile:
    """A single image tile being scrolled across the screen."""
    x: float  # left-edge position in pixels (can be fractional)
    meta_tile: MetaTile  # just in case

    def __init__(self, x: float, h: int, meta_tile: MetaTile):
        self.x = x
        self.meta_tile = meta_tile
        self._height = h
        # time.monotonic() when the tile last came on screen; None while off screen
        self.visible_since: float | None = None
        # Set when the tile's asset could not be loaded; it will never become ready
        self.load_failed = False

    def is_ready(self) -> bool:
        """True once the tile has content to draw (assets load asynchronously)."""
        return True

    def draw(self, painter: QPainter):
        pass

    def hit_test(self, x: float, y: float) -> bool:
        """Check if the given (x, y) coordinates are within the tile."""
        return self.x <= x <= self.right and 0 <= y <= self.height

    @property
    def width(self) -> int:
        return int(self.meta_tile.width * self._height / self.meta_tile.height)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def height(self) -> int:
        return self._height

    def set_height(self, target_h: int):
        self._height = target_h

    def removed(self):
        pass