import logging
import random
from dataclasses import replace
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter

from src.ResourceLoader import ResourceLoader
from src.Tile import MetaTile, Tile, Weight
from src.TileImage import TileImage
from src.TileVideo import TileVideo

logger = logging.getLogger(__name__)


class TileLayout:
    """
    Lays out one horizontal strip of tiles and scrolls it endlessly.

    Owns its own copy of the meta tiles (and their weights), so several
    layouts can run side by side without affecting each other.
    """

    def __init__(self, resource_loader: ResourceLoader, gap_px: int = -1):
        self._resource_loader = resource_loader
        self._gap_px = gap_px
        self._meta_tiles: List[MetaTile] = []
        self._tiles: List[Tile] = []
        self._width = 0
        self._height = 0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def set_meta_tiles(self, meta_tiles: List[MetaTile]) -> None:
        """Take private copies of the meta tiles (weights are mutated on pick)."""
        self._meta_tiles = [
            replace(mt, weight=Weight(mt.weight.value)) for mt in meta_tiles
        ]
        self._tiles.clear()
        self.cull_and_fill()

    @property
    def tiles(self) -> List[Tile]:
        return self._tiles

    # ------------------------------------------------------------------
    # Tile creation
    # ------------------------------------------------------------------
    def _weighted_pick(self) -> MetaTile:
        weights = [mt.weight.value for mt in self._meta_tiles]
        mt = random.choices(self._meta_tiles, weights=weights, k=1)[0]
        mt.weight.value -= 1
        return mt

    def _spawn_tile(self, x: float) -> Optional[Tile]:
        if not self._meta_tiles or self._height <= 0:
            return None
        meta_tile = self._weighted_pick()
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
        """Drop tiles that left the screen and top up the right side."""
        if self._width <= 0 or self._height <= 0:
            return

        for lost_tile in [t for t in self._tiles if t.right <= 0]:
            lost_tile.removed()
            self._tiles.remove(lost_tile)

        #self._tiles = [t for t in self._tiles if t.right > 0]

        buffer = self._height
        rightmost = max((t.right for t in self._tiles), default=0.0)
        while rightmost < self._width + buffer:
            tile = self._spawn_tile(rightmost)
            if tile is None:
                break
            self._tiles.append(tile)
            rightmost += max(1, tile.width + self._gap_px)

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
        for tile in self._tiles:
            tile.x -= dx
        self.cull_and_fill()

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