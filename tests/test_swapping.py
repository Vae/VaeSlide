"""Tile swapping, driven by a fake clock so no test has to wait."""

import random
import types

import pytest

import src.HorizontalStrip as strip_module
from src.TileSwapper import TileSwapper
from helpers import make_strip, meta

WIDTH, HEIGHT = 1000, 100   # 100x100 tiles: ten fit on screen, at x = 0, 100, ... 900
MARGIN = 100                # so only the tiles at x = 100 ... 800 may swap


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(strip_module, "time", types.SimpleNamespace(monotonic=c))
    return c


def square_metas(n=4):
    return [meta(f"m{i}") for i in range(n)]


def frame(strip, clock, at=None):
    """One frame without moving, so positions stay fixed."""
    if at is not None:
        clock.now = at
    strip.scroll(0.0)


def by_x(strip):
    return {round(t.x): t for t in strip.tiles}


def test_swap_after_random_delay_in_range(tile_type, clock):
    tile_type("fake")
    random.seed(5)
    strip = make_strip(square_metas(), swap=(2.0, 4.0), margin=MARGIN)
    frame(strip, clock, 0.0)  # starts the clocks of on-screen tiles

    on_screen = [t for t in strip.tiles if t.right > 0 and t.x < WIDTH]
    assert len(on_screen) == 10
    assert all(t.visible_since == 0.0 and 2.0 <= t.swap_after <= 4.0 for t in on_screen)
    assert all(t.visible_since is None for t in strip.tiles if t.x >= WIDTH)  # buffer tiles

    before = by_x(strip)
    frame(strip, clock, 1.99)
    assert by_x(strip) == before  # too early for anyone

    frame(strip, clock, 4.01)  # requests the swaps
    frame(strip, clock)        # replacements are ready, so they go in
    after = by_x(strip)
    assert set(after) == set(before), "swaps must not move tiles"
    for x in range(100, 900, 100):
        assert after[x] is not before[x], f"tile at x={x} should have swapped"
        assert after[x].meta_tile.resource_filename != before[x].meta_tile.resource_filename
        assert before[x].removed_called
    # The edge margin protects the tiles at the screen edges
    assert after[0] is before[0]
    assert after[900] is before[900]


def test_delays_are_random_fractions_within_range(tile_type):
    Fake = tile_type("fake")
    tile = Fake(meta("a"), 0, HEIGHT, None)
    delays = []
    for _ in range(500):
        TileSwapper.start_clock(tile, 0.0, 1.25, 1.75)
        delays.append(tile.swap_after)
    assert min(delays) >= 1.25 and max(delays) <= 1.75
    assert len(set(delays)) > 400
    assert any(d != round(d, 1) for d in delays)  # not whole numbers or tenths
    TileSwapper.start_clock(tile, 0.0, -5.0, 0.5)
    assert 0.0 <= tile.swap_after <= 0.5  # a negative minimum is clamped to 0


def test_fixed_delay(tile_type, clock):
    tile_type("fake")
    strip = make_strip(square_metas(), swap=(3.0, 3.0), margin=MARGIN)
    frame(strip, clock, 0.0)
    before = by_x(strip)
    frame(strip, clock, 2.999)
    frame(strip, clock)
    assert by_x(strip)[400] is before[400]
    frame(strip, clock, 3.0)
    frame(strip, clock)
    assert by_x(strip)[400] is not before[400]


@pytest.mark.parametrize("swap", [(0.0, 0.0), (5.0, -1.0)])
def test_max_of_zero_or_less_disables_swapping(tile_type, clock, swap):
    tile_type("fake")
    strip = make_strip(square_metas(), swap=swap, margin=MARGIN)
    frame(strip, clock, 0.0)
    before = by_x(strip)
    frame(strip, clock, 1000.0)
    frame(strip, clock)
    assert by_x(strip) == before


def test_can_swap_holds_the_swap_back(tile_type, clock):
    tile_type("fake")
    strip = make_strip(square_metas(), swap=(1.0, 1.0), margin=MARGIN)
    frame(strip, clock, 0.0)
    held = by_x(strip)[400]
    held.swappable = False  # e.g. a video that hasn't finished playing
    frame(strip, clock, 50.0)
    frame(strip, clock)
    assert by_x(strip)[400] is held
    held.swappable = True
    frame(strip, clock)  # the clock kept running, so it swaps right away
    frame(strip, clock)
    assert by_x(strip)[400] is not held


def test_swap_waits_for_the_replacement_to_be_ready(tile_type, clock):
    Fake = tile_type("fake")
    strip = make_strip(square_metas(), swap=(1.0, 1.0), margin=MARGIN)
    frame(strip, clock, 0.0)
    old = by_x(strip)[400]
    Fake.start_ready = False  # replacements load "in the background"
    frame(strip, clock, 1.5)
    replacement = strip.swapper.pending[old]
    frame(strip, clock)
    assert by_x(strip)[400] is old  # still showing the old tile
    replacement.ready = True
    frame(strip, clock)
    assert by_x(strip)[400] is replacement


def test_failed_replacement_is_dropped(tile_type, clock):
    Fake = tile_type("fake")
    strip = make_strip(square_metas(), swap=(1.0, 1.0), margin=MARGIN)
    frame(strip, clock, 0.0)
    old = by_x(strip)[400]
    Fake.start_ready = False
    frame(strip, clock, 1.5)
    replacement = strip.swapper.pending[old]
    replacement.load_failed = True
    frame(strip, clock)
    assert old not in strip.swapper.pending
    assert by_x(strip)[400] is old
    assert replacement.removed_called
    assert strip.picker.weight(replacement.meta_tile) == 0.0


def test_only_same_aspect_ratio_assets_are_used(tile_type, clock):
    tile_type("fake")
    random.seed(6)
    # 1:1 squares and one 2:1 (also 200x100 == 400x200 reduced)
    metas = square_metas(3) + [meta("wide", width=200, height=100), meta("wide2", width=400, height=200)]
    strip = make_strip(metas, swap=(1.0, 1.0), margin=0)
    frame(strip, clock, 0.0)
    for step in range(1, 20):
        frame(strip, clock, float(step))
        for tile in strip.tiles:
            mt = tile.meta_tile
            assert (mt.width / mt.height) in (1.0, 2.0)
    # A wide tile on screen only ever swaps to the other wide asset
    wide = [t for t in strip.tiles if t.meta_tile.width / t.meta_tile.height == 2.0 and 0 <= t.x < WIDTH]
    for t in wide:
        assert t.meta_tile.resource_filename in ("wide", "wide2")


def test_tile_without_a_match_is_kept(tile_type, clock):
    tile_type("fake")
    strip = make_strip([meta("only")], swap=(1.0, 1.0), margin=MARGIN)
    frame(strip, clock, 0.0)
    before = by_x(strip)
    for step in range(1, 5):
        frame(strip, clock, float(step))
    assert by_x(strip) == before


def test_clock_restarts_when_a_tile_leaves_the_screen(tile_type, clock):
    tile_type("fake")
    strip = make_strip(square_metas(), swap=(5.0, 5.0), margin=0)
    frame(strip, clock, 0.0)
    leaving = by_x(strip)[0]
    clock.now = 1.0
    strip.scroll(50.0)   # half off screen
    assert leaving.visible_since == 0.0
    strip.scroll(50.0)   # fully off screen: culled
    assert leaving not in strip.tiles and leaving.removed_called


def test_resize_drops_pending_swaps(tile_type, clock):
    Fake = tile_type("fake")
    strip = make_strip(square_metas(), swap=(1.0, 1.0), margin=MARGIN)
    frame(strip, clock, 0.0)
    Fake.start_ready = False
    frame(strip, clock, 1.5)
    pending = list(strip.swapper.pending.values())
    assert pending
    strip.resize(WIDTH, 150)
    assert strip.swapper.pending == {}
    assert all(t.removed_called for t in pending)
