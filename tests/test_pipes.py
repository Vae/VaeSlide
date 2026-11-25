"""Tile3DPipes: growth rules, cycles and resets, never swapping, and rendering."""

import random
import types

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

import src.HorizontalStrip as strip_module
from src.Settings import Settings
from src.Tile import TileContext
from src.Tile3DPipes import DIRECTIONS, Tile3DPipes, Tile3DPipesScanner, pipes_config
from helpers import make_strip

SMALL = {"grid size": [6, 4, 4], "speed": 100, "pipes per cycle": 3, "fill": 0.5, "hold seconds": 2}


def make_pipes(use_settings, seed=0, h=120, **overrides) -> Tile3DPipes:
    section = dict(SMALL, **overrides)
    use_settings({"tile 3d pipes": section})
    meta = Tile3DPipesScanner().scan(None)[0]
    return Tile3DPipes(meta, 0.0, h, TileContext(), rng=random.Random(seed))


def grow_until_hold(tile, limit=10_000):
    for _ in range(limit):
        if tile.phase != "grow":
            return
        tile.step(0.0)
    raise AssertionError("cycle never finished")


# ----------------------------------------------------------------------
# Settings and the pool
# ----------------------------------------------------------------------
def test_config_validation(use_settings):
    use_settings({"tile 3d pipes": {"grid size": [1, 99], "speed": 0, "turn chance": 7,
                                       "pipe radius": 3, "pipes per cycle": 0, "fill": 0,
                                       "color mode": "plaid", "count": -1}})
    cfg = pipes_config()
    defaults = Settings.DEFAULTS["tile 3d pipes"]
    assert cfg.size == tuple(defaults["grid size"])
    assert cfg.speed == defaults["speed"]
    assert (cfg.turn_chance, cfg.pipe_radius, cfg.pipes_per_cycle, cfg.fill) == (1.0, 0.45, 1, 0.05)
    assert cfg.color_mode == "per pipe" and cfg.count == 0


def test_scanner_entries(use_settings):
    use_settings({"tile 3d pipes": {"count": 3, "weight": 4.0, "grid size": [16, 10, 8]}})
    metas = Tile3DPipesScanner().scan(None)
    assert [m.resource_filename for m in metas] == ["pipes:0", "pipes:1", "pipes:2"]
    assert all(m.type == "3d pipes" and m.weight == 4.0 for m in metas)
    assert metas[0].width / metas[0].height == pytest.approx(16 / 10)  # x:y sets the shape
    assert not Tile3DPipesScanner.NEEDS_PATH


# ----------------------------------------------------------------------
# Growth
# ----------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(6))
def test_pipes_grow_through_free_neighbouring_cells(use_settings, seed):
    tile = make_pipes(use_settings, seed)
    grow_until_hold(tile)
    gx, gy, gz = SMALL["grid size"]
    used = []
    for prim in tile.primitives:
        if prim[0] == "pipe":
            a, b = prim[1], prim[2]
            assert sum(abs(p - q) for p, q in zip(a, b)) == 1, "segments join neighbouring cells"
            used.append(b)
        for cell in prim[1:3] if prim[0] == "pipe" else prim[1:2]:
            assert 0 <= cell[0] < gx and 0 <= cell[1] < gy and 0 <= cell[2] < gz
    starts = [p[1] for p in tile.primitives if p[0] == "ball"]
    assert len(used) == len(set(used)), "no cell is entered twice"
    assert set(used) <= tile.occupied and len(tile.occupied) == len(used) + tile.pipes_started
    assert set(starts) <= tile.occupied


def test_cycle_limits(use_settings):
    tile = make_pipes(use_settings, 3)
    grow_until_hold(tile)
    total = 6 * 4 * 4
    assert tile.pipes_started <= SMALL["pipes per cycle"]
    # Stops once the fill target is reached (the last pipe may overshoot by up to one pipe length)
    assert len(tile.occupied) <= SMALL["fill"] * total + tile._max_pipe_length() + 1
    assert tile.phase == "hold" and tile.pipe is None


def test_pipes_have_a_maximum_length(use_settings):
    tile = make_pipes(use_settings, 1, **{"grid size": [12, 12, 12], "pipes per cycle": 10, "fill": 0.2})
    grow_until_hold(tile)
    assert tile._max_pipe_length() == round(0.2 * 12 ** 3 / 10)
    # Count segments per color run: each pipe has its own color in "per pipe" mode
    lengths = {}
    for prim in tile.primitives:
        if prim[0] == "pipe":
            lengths[prim[3].rgb()] = lengths.get(prim[3].rgb(), 0) + 1
    assert max(lengths.values()) <= tile._max_pipe_length()


def test_turn_chance_zero_goes_straight_until_blocked(use_settings):
    tile = make_pipes(use_settings, 2, **{"turn chance": 0.0, "grid size": [20, 3, 3], "pipes per cycle": 1, "fill": 1.0})
    tile.step(0.0)  # starts the pipe
    direction = tile.pipe.direction
    for _ in range(3):
        before = tile.pipe.head
        tile.step(0.0)
        if tile.pipe is None:
            break
        if tile.pipe.direction != direction:
            # Only allowed when the way ahead was blocked or off the grid
            ahead = tuple(c + d for c, d in zip(before, direction))
            assert ahead in tile.occupied or not tile._inside(ahead)
            direction = tile.pipe.direction


def test_joints_mark_turns(use_settings):
    tile = make_pipes(use_settings, 4, **{"turn chance": 1.0})
    for _ in range(12):
        tile.step(0.0)
    segments = [p for p in tile.primitives if p[0] == "pipe"]
    balls = {p[1] for p in tile.primitives if p[0] == "ball"}
    for s1, s2 in zip(segments, segments[1:]):
        if s1[2] == s2[1]:  # same pipe, consecutive segments
            d1 = tuple(b - a for a, b in zip(s1[1], s1[2]))
            d2 = tuple(b - a for a, b in zip(s2[1], s2[2]))
            if d1 != d2:
                assert s1[2] in balls, "every turn has a joint"


@pytest.mark.parametrize("mode", ["per pipe", "per cycle"])
def test_color_modes(use_settings, mode):
    tile = make_pipes(use_settings, 5, **{"color mode": mode, "pipes per cycle": 4})
    grow_until_hold(tile)
    colors = {p[3].rgb() for p in tile.primitives}
    if mode == "per cycle":
        assert len(colors) == 1
    else:
        assert len(colors) == tile.pipes_started > 1


# ----------------------------------------------------------------------
# Timing, reset, and never swapping
# ----------------------------------------------------------------------
def test_waits_until_visible_then_grows_at_speed(use_settings):
    tile = make_pipes(use_settings, 0, speed=10)
    for t in range(5):
        tile.tick(float(t), False)
    assert tile.primitives == []
    tile.tick(10.0, True)
    tile.tick(10.55, True)  # 5.5 steps' worth
    # One step starts a pipe (its knob), each later step adds a segment or a joint+segment
    assert 1 <= len([p for p in tile.primitives if p[0] == "pipe"]) <= 5


def test_holds_then_resets_with_new_colors(use_settings):
    tile = make_pipes(use_settings, 6, **{"color mode": "per cycle"})
    tile.tick(0.0, True)
    now = 0.0
    while tile.phase == "grow":
        now += 0.1
        tile.tick(now, True)
    finished_at = now
    first_color = tile.primitives[0][3].rgb()
    old_angle = (tile._yaw, tile._pitch)
    tile.tick(finished_at + 1.9, True)
    assert tile.phase == "hold" and tile.cycles == 0
    tile.tick(finished_at + 2.1, True)
    assert tile.phase == "grow" and tile.cycles == 1
    assert tile.primitives == [] and tile.occupied == set()
    assert (tile._yaw, tile._pitch) != old_angle
    now = finished_at + 2.1
    while not tile.primitives:  # the new cycle grows straight away (no need to wait for visibility)
        now += 0.1
        tile.tick(now, True)
    assert tile.primitives[0][3].rgb() != first_color


def test_never_swaps(use_settings):
    tile = make_pipes(use_settings)
    assert tile.swap_seconds((1.0, 2.0)) == (0.0, 0.0)
    grow_until_hold(tile)
    assert not tile.can_swap()


def test_pipes_tile_stays_in_a_swapping_strip(qapp, use_settings, monkeypatch):
    use_settings({"tile 3d pipes": dict(SMALL, count=3)})
    clock = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(strip_module, "time", types.SimpleNamespace(monotonic=lambda: clock.now))
    metas = Tile3DPipesScanner().scan(None)  # three same-aspect alternatives
    strip = make_strip(metas, width=1000, height=100, swap=(0.5, 0.5), margin=0)
    strip.scroll(0.0)
    before = list(strip.tiles)
    for step in range(1, 40):
        clock.now = step * 0.5
        strip.scroll(0.0)
    assert strip.tiles == before
    assert not strip.swapper.pending


def test_describe(use_settings):
    tile = make_pipes(use_settings)
    tile.step(0.0)
    text = tile.describe()
    assert "6x4x4" in text and "cycle 1" in text and "1 pipe" in text


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
def render(tile) -> QImage:
    image = QImage(tile.width, tile.height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    tile.draw(painter)
    painter.end()
    return image


def lit_pixels(image: QImage) -> int:
    return sum(1 for x in range(0, image.width(), 3) for y in range(0, image.height(), 3)
               if image.pixelColor(x, y) != QColor("black"))


def test_starts_black_then_draws_pipes(qapp, use_settings):
    tile = make_pipes(use_settings, 7)
    assert lit_pixels(render(tile)) == 0
    for _ in range(15):
        tile.step(0.0)
    assert lit_pixels(render(tile)) > 20


def test_incremental_render_matches_a_full_render(qapp, use_settings):
    """Caching depth slabs must give the same picture as drawing everything at once."""
    tile = make_pipes(use_settings, 8)
    for _ in range(60):
        tile.step(0.0)
        render(tile)  # incremental: only dirty slabs are redrawn
    incremental = render(tile)
    tile._layers = {}
    tile._image = None
    full = render(tile)
    assert incremental == full


def test_resize_rerenders(qapp, use_settings):
    tile = make_pipes(use_settings, 9)
    for _ in range(20):
        tile.step(0.0)
    render(tile)
    tile.set_height(60)
    image = render(tile)
    assert (image.width(), image.height()) == (tile.width, 60)
    assert lit_pixels(image) > 0


def test_end_on_pipes_render(qapp, use_settings):
    """A camera looking straight down a pipe draws its round end without errors."""
    tile = make_pipes(use_settings, 10)
    tile._yaw = tile._pitch = 0.0
    tile._add(("pipe", (2, 2, 0), (2, 2, 1), QColor("red")))
    image = render(tile)
    project, _ = tile._projector(tile.width, tile.height)
    x, y, _ = project(tile._to_view((2, 2, 0)))
    center = image.pixelColor(int(x), int(y))
    assert center.red() > 0 and center.green() == 0  # the red pipe's round end
