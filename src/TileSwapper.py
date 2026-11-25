"""
TileSwapper: decides when an on-screen tile swaps to another asset, and
puts the replacement in place once it has loaded.
"""

import logging
import random
from typing import Callable, Dict, List, Optional

from src.AssetPicker import AssetPicker
from src.Tile import MetaTile, Tile

logger = logging.getLogger(__name__)

# Makes a new tile for an asset at an x position (the strip's create function)
CreateTile = Callable[[MetaTile, float], Optional[Tile]]


class TileSwapper:
    """
    Swaps tiles in place for a different asset with the exact same aspect
    ratio, so the tiles next to them don't move.

    A tile swaps once it has been on screen for a random time drawn from
    `swap_seconds` (per tile type, via Tile.swap_seconds), it is at least
    `edge_margin` px from both screen edges, and Tile.can_swap() allows it.
    The replacement loads in the background; the old tile stays until the
    replacement is ready, so the slot never flashes blank.
    """

    def __init__(self, picker: AssetPicker, create_tile: CreateTile,
                 swap_seconds: tuple[float, float], edge_margin: float):
        self._picker = picker
        self._create_tile = create_tile
        self._swap_seconds = swap_seconds
        self._edge_margin = edge_margin
        # Swaps waiting for the replacement's asset to load: old tile -> new tile
        self.pending: Dict[Tile, Tile] = {}

    @staticmethod
    def start_clock(tile: Tile, now: float, swap_min: float, swap_max: float) -> None:
        """Start the tile's swap clock with a fresh random delay in [swap_min, swap_max]."""
        tile.visible_since = now
        tile.swap_after = random.uniform(max(0.0, swap_min), swap_max)

    def update(self, tiles: List[Tile], screen_width: float, now: float) -> None:
        """
        Once per frame. `tiles` is the strip's list; finished swaps replace
        entries in it in place.
        """
        self._complete_pending(tiles)
        for tile in tiles:
            if tile not in self.pending and self._is_due(tile, screen_width, now):
                self._request_swap(tile)

    def cancel_all(self) -> None:
        """Drop every pending replacement (e.g. after a resize or when the strip is cleared)."""
        for new_tile in self.pending.values():
            new_tile.removed()
        self.pending.clear()

    # ------------------------------------------------------------------
    def _is_due(self, tile: Tile, screen_width: float, now: float) -> bool:
        """Advance the tile's swap clock; True if it should swap now."""
        # Per tile type: videos use "tile video" -> "tile swap seconds"
        swap_min, swap_max = tile.swap_seconds(self._swap_seconds)
        if swap_max <= 0:
            return False
        if tile.right <= 0 or tile.x >= screen_width:
            tile.visible_since = None  # off screen: restart the clock when it comes back
            return False
        if tile.visible_since is None:
            self.start_clock(tile, now, swap_min, swap_max)
            return False
        if now - tile.visible_since < tile.swap_after:
            return False
        # Don't swap tiles that are near or past an edge. The clock keeps
        # running, so an entering tile swaps once it is far enough in.
        if tile.x < self._edge_margin or tile.right > screen_width - self._edge_margin:
            return False
        # e.g. videos must finish a full playthrough first; the clock keeps
        # running so the swap happens as soon as the video finishes
        if not tile.can_swap():
            return False
        # Restart the clock either way, so a tile with no match isn't re-checked every frame
        self.start_clock(tile, now, swap_min, swap_max)
        return True

    def _request_swap(self, tile: Tile) -> None:
        candidates = self._picker.alternatives(tile.meta_tile)
        if not candidates:
            return  # no other asset has this aspect ratio: keep the tile
        new_meta = self._picker.pick(candidates, replacing=tile)
        if new_meta is None:
            return
        new_tile = self._create_tile(new_meta, tile.x)
        if new_tile is not None:
            self.pending[tile] = new_tile

    def _complete_pending(self, tiles: List[Tile]) -> None:
        """Put replacement tiles in place once their assets have loaded."""
        if not self.pending:
            return
        index_of = {id(t): i for i, t in enumerate(tiles)}
        for old_tile, new_tile in list(self.pending.items()):
            i = index_of.get(id(old_tile))
            if i is None or new_tile.load_failed:
                # Old tile was culled before the replacement loaded, or the load failed
                if new_tile.load_failed:
                    self._picker.disable(new_tile.meta_tile)
                new_tile.removed()
                del self.pending[old_tile]
                continue
            if not new_tile.is_ready():
                continue
            new_tile.x = old_tile.x  # the old tile kept scrolling while we waited
            new_tile.visible_since = None  # next frame starts its clock with a fresh random delay
            old_tile.removed()
            tiles[i] = new_tile
            del self.pending[old_tile]
            logger.debug("Swapped tile %s -> %s",
                         old_tile.meta_tile.resource_filename, new_tile.meta_tile.resource_filename)
