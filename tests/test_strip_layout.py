"""HorizontalStrip filling and culling in both scroll directions."""

import random

import pytest

from helpers import make_strip, meta

WIDTH, HEIGHT = 1000, 100


def assert_contiguous(strip, gap):
    tiles = strip.tiles
    xs = [t.x for t in tiles]
    assert xs == sorted(xs), "tiles must stay sorted by x"
    for a, b in zip(tiles, tiles[1:]):
        assert b.x == pytest.approx(a.right + gap), "tiles must be exactly one gap apart"


def assert_covers_screen(strip, gap):
    tiles = strip.tiles
    assert tiles, "strip is empty"
    # No uncovered space bigger than the gap at either edge
    assert tiles[0].x <= max(gap, 0) + 1e-6
    assert tiles[-1].right >= WIDTH - max(gap, 0) - 1e-6


def mixed_metas(n=12):
    rng = random.Random(4)
    return [meta(f"m{i}", width=rng.randint(40, 250), height=100) for i in range(n)]


def test_initial_fill(tile_type):
    tile_type("fake")
    strip = make_strip(mixed_metas())
    assert strip.tiles[0].x == 0
    assert_contiguous(strip, 0)
    # One strip-height of buffer past the right (leading) edge
    assert strip.tiles[-1].right >= WIDTH + HEIGHT
    assert strip.tiles[-2].right < WIDTH + HEIGHT
    assert all(t.height == HEIGHT for t in strip.tiles)


@pytest.mark.parametrize("gap", [0, 10, -5])
@pytest.mark.parametrize("dx", [3.7, -3.7])
def test_scrolling_keeps_the_screen_covered(tile_type, gap, dx):
    Fake = tile_type("fake")
    strip = make_strip(mixed_metas(), gap=gap)
    for _ in range(600):
        strip.scroll(dx)
        assert_contiguous(strip, gap)
        assert_covers_screen(strip, gap)
    # Tiles that left the strip were told so
    on_strip = set(map(id, strip.tiles))
    gone = [t for t in Fake.created if id(t) not in on_strip]
    assert gone and all(t.removed_called for t in gone)
    assert not any(t.removed_called for t in strip.tiles)


@pytest.mark.parametrize("gap", [0, 10])
@pytest.mark.parametrize("dx", [5.0, -5.0])
def test_no_respawn_thrash(tile_type, gap, dx):
    """Tiles are only created as space opens up, not re-created every frame."""
    Fake = tile_type("fake")
    strip = make_strip([meta(str(i)) for i in range(5)], gap=gap)
    initial = len(Fake.created)
    frames = 400
    strip.scroll(dx)  # the first scroll in a new direction may pre-fill the other buffer
    after_first = len(Fake.created)
    for _ in range(frames - 1):
        strip.scroll(dx)
    pitch = 100 + gap
    expected = abs(dx) * (frames - 1) / pitch
    assert len(Fake.created) - after_first <= expected + 2
    assert after_first - initial <= (HEIGHT + WIDTH) / pitch + 2


def test_negative_scroll_buffers_the_left_edge(tile_type):
    tile_type("fake")
    strip = make_strip([meta(str(i)) for i in range(5)])
    strip.scroll(-1.0)
    assert strip.tiles[0].x <= -HEIGHT
    # Trailing (right) edge gets no buffer: nothing starts past the screen
    assert all(t.x < WIDTH for t in strip.tiles)


def test_reversing_direction(tile_type):
    tile_type("fake")
    strip = make_strip(mixed_metas(), gap=4)
    for dx in [6.0] * 200 + [-6.0] * 400 + [2.5] * 200:
        strip.scroll(dx)
        assert_contiguous(strip, 4)
        assert_covers_screen(strip, 4)


def test_resize_relayouts_at_the_new_height(tile_type):
    tile_type("fake")
    strip = make_strip(mixed_metas(), gap=3)
    for _ in range(50):
        strip.scroll(4.0)
    first_x = strip.tiles[0].x
    strip.resize(WIDTH, 200)
    assert strip.tiles[0].x == first_x  # scroll position is kept
    assert all(t.height == 200 for t in strip.tiles)
    assert_contiguous(strip, 3)
    assert strip.tiles[-1].right >= WIDTH + 200


def test_empty_asset_list_draws_nothing(tile_type):
    tile_type("fake")
    strip = make_strip([])
    strip.scroll(5.0)
    assert strip.tiles == []


def test_spawning_stops_when_every_type_is_capped(tile_type):
    Fake = tile_type("fake")
    Fake.allow = False
    strip = make_strip([meta("a")])
    assert strip.tiles == []
    Fake.allow = True  # e.g. a video slot was freed
    strip.scroll(1.0)
    assert strip.tiles


def test_failed_load_zeroes_the_assets_weight(tile_type):
    tile_type("fake")
    strip = make_strip([meta("a"), meta("b")])
    victim = strip.tiles[0]
    victim.load_failed = True
    strip.scroll(1.0)
    assert victim.meta_tile.weight.value == 0.0


def test_hit_test(tile_type):
    tile_type("fake")
    strip = make_strip([meta(str(i)) for i in range(5)], gap=10)
    assert strip.hit_test(50, 50) is strip.tiles[0]
    assert strip.hit_test(105, 50) is None  # in the gap
    assert strip.hit_test(115, 50) is strip.tiles[1]
    assert strip.hit_test(50, 150) is None  # below the strip


def test_layers_get_independent_weights(tile_type):
    tile_type("fake")
    shared = [meta("a", weight=4.0)]
    strip = make_strip(shared)
    assert shared[0].weight.value == 4.0  # the strip halved its own copy, not the master list
    assert strip.tiles[0].meta_tile is not shared[0]
