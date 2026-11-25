"""
Registry of tile types: MetaTile.type -> Tile subclass.

To add a tile type, write a Tile subclass with TYPE and SCANNER set and add
it to the tuple below. Everything else (creating tiles, selection caps and
weights, loading assets from assets.json) looks types up here.
"""

from typing import Dict, Optional, Type

from src.Tile import MetaTile, Tile, TileContext
from src.TileImage import TileImage
from src.TileMaze import TileMaze
from src.TileVideo import TileVideo

TILE_TYPES: Dict[str, Type[Tile]] = {cls.TYPE: cls for cls in (TileImage, TileVideo, TileMaze)}


def create_tile(meta_tile: MetaTile, x: float, h: int, ctx: TileContext) -> Optional[Tile]:
    """A new tile for `meta_tile`, or None if its type isn't registered."""
    cls = TILE_TYPES.get(meta_tile.type)
    return cls(meta_tile, x, h, ctx) if cls is not None else None
