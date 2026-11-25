"""Plain helpers shared by the tests (fixtures live in conftest.py)."""

from src.HorizontalStrip import HorizontalStrip, StripConfig
from src.Tile import MetaTile, TileContext, Weight


def meta(name: str, width: int = 100, height: int = 100, type: str = "fake", weight: float = 1.0) -> MetaTile:
    """A MetaTile for tests."""
    return MetaTile(weight=Weight(weight), type=type, width=width, height=height,
                    resource_filename=name, json_filename="")


def make_strip(metas, width=1000, height=100, gap=0, swap=(0.0, 0.0), margin=0.0) -> HorizontalStrip:
    """A strip laid out at width x height, with the given gap and swap settings."""
    strip = HorizontalStrip(TileContext(), StripConfig(gap_px=gap, swap_seconds=swap, swap_edge_margin=margin))
    strip.set_meta_tiles(metas)
    strip.resize(width, height)
    return strip
