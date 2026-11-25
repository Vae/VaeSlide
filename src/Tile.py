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

    def describe(self) -> str:
        """One-line description, logged when the tile is left-clicked."""
        return self.meta_tile.resource_filename

    def tick(self, now: float, visible: bool) -> None:
        """
        Called once per frame by the strip. `now` is time.monotonic(), and
        `visible` is True when any part of the tile is on screen. Animated tiles override it.
        """
        pass

    def is_ready(self) -> bool:
        """True once the tile has content to draw (assets load asynchronously)."""
        return True

    @classmethod
    def can_select(cls, replacing: "Tile | None" = None) -> bool:
        """
        Hard limit: False if no new tile of this type may be created right now.
        `replacing` is a tile that a swap is about to remove, so it shouldn't
        count against a cap.
        """
        return True

    @classmethod
    def selection_weight_multiplier(cls) -> float:
        """
        Multiplier applied to the weight of every asset of this tile type when
        picking the next asset. Lets a type adjust its odds from live state,
        e.g. how many tiles of that type are currently loaded.
        """
        return 1.0

    def swap_seconds(self, default: float) -> float:
        """
        On-screen seconds before this tile is swapped; <= 0 means never.
        `default` is the strip's top-level "tile swap seconds". Subclasses
        can override it with a value from their own settings section.
        """
        return default

    def can_swap(self) -> bool:
        """False while the tile must not be swapped out yet (e.g. a video mid-playthrough)."""
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