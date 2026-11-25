"""
AssetPicker: decides which asset a strip shows next.

Each strip has its own picker, so each layer has independent odds. The
MetaTiles themselves are shared between strips and never modified; the
picker keeps its own current weight for each one.
"""

import math
import random
from typing import Dict, Iterable, List, Optional, Tuple

from src.Tile import MetaTile, Tile
from src.TileTypes import TILE_TYPES

AspectKey = Tuple[int, int]


def aspect_key(meta_tile: MetaTile) -> Optional[AspectKey]:
    """Exact aspect ratio as a reduced (w, h) fraction, e.g. 1920x1080 -> (16, 9)."""
    try:
        w, h = int(meta_tile.width), int(meta_tile.height)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    g = math.gcd(w, h)
    return w // g, h // g


class AssetPicker:
    """
    Weighted random choice of assets, where every pick halves the picked
    asset's weight so recent assets are less likely to come up again.

    Each tile type (looked up in TILE_TYPES) can also veto its assets with
    can_select() (e.g. the video cap) and scale their odds with
    selection_weight_multiplier() (e.g. fewer videos when many are loaded).
    """

    def __init__(self, assets: Iterable[MetaTile] = ()):
        self._assets: List[MetaTile] = []
        self._weights: Dict[MetaTile, float] = {}
        self._by_aspect: Dict[AspectKey, List[MetaTile]] = {}
        self.set_assets(assets)

    def set_assets(self, assets: Iterable[MetaTile]) -> None:
        """Replace the pool; every asset starts at its MetaTile.weight."""
        self._assets = list(assets)
        self._weights = {mt: float(mt.weight) for mt in self._assets}
        self._by_aspect = {}
        for mt in self._assets:
            key = aspect_key(mt)
            if key is not None:
                self._by_aspect.setdefault(key, []).append(mt)

    def __bool__(self) -> bool:
        return bool(self._assets)

    def weight(self, meta_tile: MetaTile) -> float:
        """Current weight of an asset in this picker (0 if it isn't in the pool)."""
        return self._weights.get(meta_tile, 0.0)

    def disable(self, meta_tile: MetaTile) -> None:
        """Stop picking an asset, e.g. after it failed to load. Only a uniform fallback can still pick it."""
        if meta_tile in self._weights:
            self._weights[meta_tile] = 0.0

    def alternatives(self, meta_tile: MetaTile) -> List[MetaTile]:
        """Other assets with exactly the same aspect ratio (a different file), for in-place swaps."""
        return [
            mt for mt in self._by_aspect.get(aspect_key(meta_tile), [])
            if mt is not meta_tile and mt.resource_filename != meta_tile.resource_filename
        ]

    def pick(self, candidates: Optional[List[MetaTile]] = None,
             replacing: Optional[Tile] = None) -> Optional[MetaTile]:
        """
        Weighted random pick from `candidates` (default: the whole pool).
        Returns None if every candidate's tile type is unavailable right now
        (e.g. the video cap is reached). `replacing` is the tile a swap will
        remove, so it doesn't count toward caps.
        """
        candidates = self._assets if candidates is None else candidates
        types = {mt.type for mt in candidates}

        # Hard limits first: drop types that can't be selected right now.
        # Types missing from TILE_TYPES aren't filtered.
        blocked = {t for t in types if t in TILE_TYPES and not TILE_TYPES[t].can_select(replacing)}
        candidates = [mt for mt in candidates if mt.type not in blocked]
        if not candidates:
            return None

        # Each tile type can scale its assets' odds from live state
        multipliers = {t: TILE_TYPES[t].selection_weight_multiplier() for t in types - blocked if t in TILE_TYPES}
        weights = [self.weight(mt) * multipliers.get(mt.type, 1.0) for mt in candidates]
        if sum(weights) > 0:
            mt = random.choices(candidates, weights=weights, k=1)[0]
        else:
            # All weights are 0: failed loads, float underflow after a long run
            # of halving, or a 0 multiplier. random.choices would raise, so pick uniformly.
            mt = random.choice(candidates)
        if mt in self._weights:
            self._weights[mt] /= 2
        return mt
