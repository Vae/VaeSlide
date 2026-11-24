import logging
import math
import random
import time
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter

from src.ResourceLoader import ResourceLoader
from src.Tile import MetaTile, Tile, Weight
from src.TileImage import TileImage
from src.TileVideo import TileVideo

logger = logging.getLogger(__name__)


def _aspect_key(meta_tile: MetaTile) -> Optional[Tuple[int, int]]:
    """Exact aspect ratio as a reduced (w, h) fraction, e.g. 1920x1080 -> (16, 9)."""
    try:
        w, h = int(meta_tile.width), int(meta_tile.height)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    g = math.gcd(w, h)
    return w // g, h // g


class HorizontalStrip:
    """
    Lays out one horizontal strip of tiles and scrolls it endlessly.

    Owns its own copy of the meta tiles (and their weights), so several
    layouts can run side by side without affecting each other.
    """

    def __init__(self, resource_loader: ResourceLoader, gap_px: int = -1, swap_seconds: float = 0.0):
        """
        swap_seconds: a tile that has been on screen this long is swapped for
                      another asset with the same aspect ratio. <= 0 disables.
        """
        self._resource_loader = resource_loader
        self._gap_px = gap_px
        self._swap_seconds = swap_seconds
        self._meta_tiles: List[MetaTile] = []
        # Meta tiles grouped by reduced aspect ratio (w, h), used for swapping
        self._by_aspect: Dict[Tuple[int, int], List[MetaTile]] = {}
        self._tiles: List[Tile] = []
        self._width = 0
        self._height = 0
        # Sign of the last scroll: >0 tiles move left, <0 tiles move right
        self._direction = 1.0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def set_meta_tiles(self, meta_tiles: List[MetaTile]) -> None:
        """Take private copies of the meta tiles (weights are mutated on pick)."""
        self._meta_tiles = [
            replace(mt, weight=Weight(mt.weight.value)) for mt in meta_tiles
        ]
        self._by_aspect = {}
        for mt in self._meta_tiles:
            key = _aspect_key(mt)
            if key is not None:
                self._by_aspect.setdefault(key, []).append(mt)
        self._tiles.clear()
        self.cull_and_fill()

    @property
    def tiles(self) -> List[Tile]:
        return self._tiles

    # ------------------------------------------------------------------
    # Tile creation
    # ------------------------------------------------------------------
    @staticmethod
    def _weighted_pick(candidates: List[MetaTile]) -> MetaTile:
        weights = [mt.weight.value for mt in candidates]
        mt = random.choices(candidates, weights=weights, k=1)[0]
        mt.weight.value /= 2
        return mt

    def _spawn_tile(self, x: float) -> Optional[Tile]:
        if not self._meta_tiles or self._height <= 0:
            return None
        return self._create_tile(self._weighted_pick(self._meta_tiles), x)

    def _create_tile(self, meta_tile: MetaTile, x: float) -> Optional[Tile]:
        if meta_tile.type == "image":
            pixmap = self._resource_loader.get_pixmap(meta_tile.resource_filename)
            if pixmap:
                scaled = pixmap.scaledToHeight(self._height, Qt.SmoothTransformation)
                return TileImage(meta_tile=meta_tile, x=x, h=self._height, pixmap=scaled)
        if meta_tile.type == "video":
            return TileVideo(video_filename=meta_tile.resource_filename, x=x,
                             h=self._height, meta_tile=meta_tile)
        return None

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

        self._tiles is kept sorted by x.
        """
        if self._width <= 0 or self._height <= 0:
            return

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
        self._swap_stale_tiles()

    # ------------------------------------------------------------------
    # Asset swapping
    # ------------------------------------------------------------------
    def _swap_stale_tiles(self) -> None:
        """
        Replace tiles that have been visible for longer than swap_seconds with
        a different asset of the same aspect ratio, so neighbours don't move.
        """
        if self._swap_seconds <= 0:
            return

        now = time.monotonic()
        for i, tile in enumerate(self._tiles):
            if tile.right <= 0 or tile.x >= self._width:
                tile.visible_since = None  # off screen: restart the clock when it comes back
                continue
            if tile.visible_since is None:
                tile.visible_since = now
                continue
            if now - tile.visible_since < self._swap_seconds:
                continue

            # Restart the clock either way, so a tile with no match isn't re-checked every frame
            tile.visible_since = now

            old_meta = tile.meta_tile
            candidates = [
                mt for mt in self._by_aspect.get(_aspect_key(old_meta), [])
                if mt is not old_meta and mt.resource_filename != old_meta.resource_filename
            ]
            if not candidates:
                continue

            new_tile = self._create_tile(self._weighted_pick(candidates), tile.x)
            if new_tile is None:
                continue
            new_tile.visible_since = now
            tile.removed()
            self._tiles[i] = new_tile
            logger.debug("Swapped tile %s -> %s",
                         old_meta.resource_filename, new_tile.meta_tile.resource_filename)

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