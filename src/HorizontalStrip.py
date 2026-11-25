import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtGui import QPainter

from src.AssetPicker import AssetPicker
from src.Settings import get_settings, section_config
from src.Tile import MetaTile, Tile, TileContext
from src.TileSwapper import TileSwapper
from src.TileTypes import create_tile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StripConfig:
    """Top-level settings.json values that control every strip."""
    gap_px: int                        # px between tiles (0 = seamless, negative = overlap)
    swap_seconds: tuple[float, float]  # (min, max) on-screen seconds before a swap; max <= 0 disables
    swap_edge_margin: float            # px; tiles closer than this to a screen edge aren't swapped


@section_config
def strip_config() -> StripConfig:
    """Validated strip settings, read once."""
    settings = get_settings()
    return StripConfig(
        gap_px=settings.get_number("tile gap", int),
        swap_seconds=settings.get_range("tile swap seconds"),
        swap_edge_margin=settings.get_number("tile swap edge margin"),
    )


class HorizontalStrip:
    """
    Lays out one horizontal strip of tiles and scrolls it endlessly.

    The strip only handles placement: filling, culling, resizing and
    scrolling. It delegates the other two jobs:
      - picker  (AssetPicker): which asset comes next. Each strip has its
                own, so layers have independent odds.
      - swapper (TileSwapper): when an on-screen tile swaps to another asset.
    """

    def __init__(self, ctx: TileContext, config: Optional[StripConfig] = None):
        """
        ctx:    passed to every tile this strip creates.
        config: gap and swap settings; defaults to strip_config() from settings.json.
        """
        config = config or strip_config()
        self._ctx = ctx
        self._gap_px = config.gap_px
        self.picker = AssetPicker()
        self.swapper = TileSwapper(self.picker, self._create_tile,
                                   config.swap_seconds, config.swap_edge_margin)
        self._tiles: List[Tile] = []  # kept sorted by x
        self._width = 0
        self._height = 0
        # Sign of the last scroll: >0 tiles move left, <0 tiles move right
        self._direction = 1.0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def set_meta_tiles(self, meta_tiles: List[MetaTile]) -> None:
        """Use a new asset pool, discarding every current tile."""
        self.picker.set_assets(meta_tiles)
        self.removed()
        self.cull_and_fill()

    @property
    def tiles(self) -> List[Tile]:
        return self._tiles

    # ------------------------------------------------------------------
    # Tile creation
    # ------------------------------------------------------------------
    def _spawn_tile(self, x: float) -> Optional[Tile]:
        if not self.picker or self._height <= 0:
            return None
        meta_tile = self.picker.pick()
        if meta_tile is None:
            # Only capped types left (e.g. a video-only asset set at the video cap).
            # The fill loop stops; it retries next frame once a tile is freed.
            return None
        return self._create_tile(meta_tile, x)

    def _create_tile(self, meta_tile: MetaTile, x: float) -> Optional[Tile]:
        # Returns immediately; images load on the asset_loader threads
        return create_tile(meta_tile, x, self._height, self._ctx)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def cull_and_fill(self) -> None:
        """
        Drop tiles that are entirely off screen and fill both edges.

        Tiles enter from the leading edge: the right edge when scrolling left
        (positive speed), the left edge when scrolling right (negative speed).
        A buffer of one strip-height is pre-filled past the leading edge so new
        tiles are ready before they come into view; the trailing edge gets no
        buffer, so tiles are dropped as soon as they leave the screen.
        """
        if self._width <= 0 or self._height <= 0:
            return

        # Stop picking assets that failed to load (e.g. a file that was archived)
        for tile in self._tiles:
            if tile.load_failed:
                self.picker.disable(tile.meta_tile)

        buffer = self._height
        scrolling_right = self._direction < 0
        left_limit = -buffer if scrolling_right else 0.0
        right_limit = self._width + (0.0 if scrolling_right else buffer)

        # Cull tiles fully outside [left_limit, right_limit]
        kept = []
        for tile in self._tiles:
            if tile.right <= left_limit or tile.x >= right_limit:
                tile.removed()
            else:
                kept.append(tile)
        self._tiles = kept

        # Fill the right side
        next_x = self._tiles[-1].right + self._gap_px if self._tiles else 0.0
        while next_x < right_limit:
            tile = self._spawn_tile(next_x)
            if tile is None:
                break
            self._tiles.append(tile)
            next_x += max(1, tile.width + self._gap_px)

        # Fill the left side. A new tile's right edge sits at first.x - gap, so only
        # add one if that edge is inside the limit, otherwise it would be culled
        # next frame and re-spawned every frame.
        while self._tiles and self._tiles[0].x - self._gap_px > left_limit:
            tile = self._spawn_tile(0.0)
            if tile is None:
                break
            tile.x = self._tiles[0].x - self._gap_px - max(1, tile.width)
            self._tiles.insert(0, tile)

    def resize(self, width: int, height: int) -> None:
        """Re-scale and re-flow all tiles for a new viewport size."""
        self._width = width
        self._height = height

        # Pending replacements were requested at the old height; drop them
        self.swapper.cancel_all()

        if not self._tiles:
            self.cull_and_fill()
            return

        # Re-layout sequentially to respect the gap, keeping the scroll position
        self._tiles.sort(key=lambda t: t.x)
        x = self._tiles[0].x
        for tile in self._tiles:
            tile.set_height(height)
            tile.x = x
            x += tile.width + self._gap_px

        self.cull_and_fill()

    # ------------------------------------------------------------------
    # Animation / painting
    # ------------------------------------------------------------------
    def scroll(self, dx: float) -> None:
        """Move every tile left by dx pixels (a negative dx moves them right)."""
        if dx != 0:
            self._direction = dx
        for tile in self._tiles:
            tile.x -= dx
        self.cull_and_fill()
        now = time.monotonic()
        for tile in self._tiles:
            tile.tick(now, tile.right > 0 and tile.x < self._width)
        self.swapper.update(self._tiles, self._width, now)

    def hit_test(self, x: float, y: float) -> Optional[Tile]:
        """Check if the given (x, y) coordinates are within any tile in this layout."""
        for tile in self._tiles:
            if tile.hit_test(x, y):
                return tile
        return None

    def draw(self, painter: QPainter) -> None:
        for tile in self._tiles:
            tile.draw(painter)

    def removed(self):
        for tile in self._tiles:
            tile.removed()
        self._tiles = []
        self.swapper.cancel_all()
