from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, List, Optional

from PySide6.QtGui import QPainter

from src import TileShaders

if TYPE_CHECKING:
    from src.AssetLoader import AssetLoader
    from src.TileShaders import TileShader
    from src.Transitions import ActiveTransition


@dataclass(eq=False)  # compared and hashed by identity, so it can key per-strip weight tables
class MetaTile:
    """
    One asset in the selection pool. Shared by every strip and never modified;
    each strip's AssetPicker tracks its own current weight.
    """
    weight: float  # starting selection weight
    type: str

    width: int
    height: int

    resource_filename: str
    json_filename: str


@dataclass(frozen=True)
class TileContext:
    """Shared services handed to every tile constructor by its strip."""
    asset_loader: Optional["AssetLoader"] = None  # background image loading (TileImage)


class Tile:
    """
    A single tile being scrolled across the screen.

    Subclasses set TYPE (the MetaTile.type they handle) and SCANNER (their
    AssetScanner class), and are listed in src/TileTypes.py.
    """
    TYPE: ClassVar[str] = ""
    SCANNER: ClassVar[Optional[type]] = None  # AssetScanner subclass that builds this type's MetaTiles

    x: float  # left-edge position in pixels (can be fractional)
    meta_tile: MetaTile  # just in case

    def __init__(self, meta_tile: MetaTile, x: float, h: int, ctx: TileContext):
        self.x = x
        self.meta_tile = meta_tile
        self._height = h
        # time.monotonic() when the tile last came on screen; None while off screen
        self.visible_since: float | None = None
        # Random on-screen seconds before a swap, picked by the strip each time the clock starts
        self.swap_after: float | None = None
        # Set when the tile's asset could not be loaded; it will never become ready
        self.load_failed = False
        # Set while this tile is transitioning in over the tile it replaced (src/Transitions.py)
        self.transition: Optional["ActiveTransition"] = None
        # GPU effects drawn over this tile, in order (src/TileShaders.py); assigned by the strip
        self.shaders: List["TileShader"] = []

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

    def swap_seconds(self, default: tuple[float, float]) -> tuple[float, float]:
        """
        (min, max) on-screen seconds before this tile is swapped; the strip
        picks a random time in this range. max <= 0 means never.
        `default` is the strip's top-level "tile swap seconds". Subclasses
        can override it with a value from their own settings section.
        """
        return default

    def can_swap(self) -> bool:
        """False while the tile must not be swapped out yet (e.g. a video mid-playthrough)."""
        return True

    def draw(self, painter: QPainter):
        """Draw the tile's own content. Subclasses override this; everyone else calls paint()."""
        pass

    def content_key(self) -> object:
        """
        A value that changes whenever draw() would draw different pixels
        (apart from moving with x). Shaders keep the tile's texture while it
        stays the same instead of redrawing it every frame. None (the
        default) means "unknown": the texture is redrawn every frame.
        """
        return None

    def paint(self, painter: QPainter) -> None:
        """Draw the tile with its shader effects (plain draw() when it has none or GL is off)."""
        TileShaders.draw(painter, self)

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