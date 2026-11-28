"""FrameClock: even steps from noisy vsynced frame times, real time without vsync."""

import random

import pytest

from src.FrameClock import FrameClock
from src.ScrollLayer import ScrollLayer, LayerConfig
from src.Tile import TileContext

P = 1 / 60  # refresh period


def run(clock, intervals, period=P, start=100.0):
    """Feed frame intervals to the clock; returns the step for each one (after the first frame)."""
    now = start
    clock.advance(now, period)
    steps = []
    for dt in intervals:
        now += dt
        steps.append(clock.advance(now, period))
    return steps


def test_first_frame_advances_one_period():
    assert FrameClock().advance(5.0, P) == pytest.approx(P)


def test_jittery_vsync_gives_exactly_even_steps():
    # Swaps are seen up to 3 ms either side of their refresh (the noise is on the
    # timestamps; each one still belongs to its own refresh)
    rng = random.Random(1)
    times = [k * P + rng.uniform(-0.003, 0.003) for k in range(2000)]
    steps = run(FrameClock(), [b - a for a, b in zip(times, times[1:])])
    assert all(s == pytest.approx(P) for s in steps)


def test_late_then_early_swap_pairs_stay_even():
    # The Windows compositor pattern: one swap returns late, the next early
    steps = run(FrameClock(), [P, 0.026, 2 * P - 0.026, P, P] * 50)
    assert all(s == pytest.approx(P) for s in steps)


def test_dropped_frame_catches_up_with_one_double_step():
    clock = FrameClock()
    steps = run(clock, [P] * 10 + [2 * P] + [P] * 10)
    assert steps.count(pytest.approx(2 * P)) == 1
    assert sum(steps) == pytest.approx(22 * P)  # no time lost or gained
    assert clock.late_frames == 1


def test_slightly_wrong_refresh_rate_never_hitches():
    # The display runs at 59.94 Hz while the screen reports 60
    steps = run(FrameClock(), [1 / 59.94] * 3000)
    assert all(s == pytest.approx(P) for s in steps)


def test_sustained_half_rate_moves_at_real_speed():
    steps = run(FrameClock(), [2 * P] * 300)
    assert sum(steps) == pytest.approx(600 * P, rel=0.02)


def test_vsync_off_falls_back_to_the_measured_period():
    # Frames every 5 ms although the screen reports 60 Hz: real speed, not 3x
    steps = run(FrameClock(), [0.005] * 600)
    assert sum(steps) == pytest.approx(600 * 0.005, rel=0.05)


def test_without_snap_steps_are_real_time():
    steps = run(FrameClock(snap=False), [0.010, 0.020, 0.0167])
    assert steps == pytest.approx([0.010, 0.020, 0.0167])


@pytest.mark.parametrize("snap", [True, False])
def test_a_stall_is_capped(snap):
    steps = run(FrameClock(snap=snap), [P, 2.0, P, P])
    assert max(steps) == pytest.approx(FrameClock.MAX_STEP)
    assert steps[-1] == pytest.approx(P, abs=1e-3)  # the lost time isn't repaid later


def test_layer_scrolls_by_fractional_frames(qapp, monkeypatch):
    layer = ScrollLayer(LayerConfig(speed=4.0), TileContext(asset_loader=None))
    scrolled = []
    monkeypatch.setattr(layer.layout, "scroll", scrolled.append)
    layer.tick(0.5, 1.5)
    assert scrolled == [pytest.approx(3.0)]
