"""Weighted asset picking, per-type caps and multipliers, and the video limits."""

import random

import pytest

from src.HorizontalStrip import HorizontalStrip
from src.TileVideo import TileVideo
from helpers import meta

pick = HorizontalStrip._weighted_pick


def test_pick_halves_the_picked_weight(tile_type):
    tile_type("fake")
    m = meta("a", weight=8.0)
    assert pick([m]) is m
    assert m.weight.value == 4.0


def test_pick_follows_weights(tile_type):
    tile_type("fake")
    random.seed(1)
    counts = {"heavy": 0, "light": 0}
    for _ in range(4000):
        heavy, light = meta("heavy", weight=3.0), meta("light", weight=1.0)  # fresh weights each draw
        counts[pick([heavy, light]).resource_filename] += 1
    assert counts["heavy"] / 4000 == pytest.approx(0.75, abs=0.03)


def test_halving_spreads_picks_out(tile_type):
    tile_type("fake")
    random.seed(2)
    metas = [meta(str(i)) for i in range(5)]
    picked = [pick(metas).resource_filename for _ in range(50)]
    # Repeats are discouraged: every asset shows up, none dominates
    assert set(picked) == {str(i) for i in range(5)}
    assert max(picked.count(str(i)) for i in range(5)) <= 15


def test_all_zero_weights_pick_uniformly(tile_type):
    tile_type("fake")
    metas = [meta("a", weight=0.0), meta("b", weight=0.0)]
    assert pick(metas) in metas  # no exception from random.choices


def test_type_that_cannot_be_selected_is_skipped(tile_type):
    Capped = tile_type("capped")
    tile_type("free")
    Capped.allow = False
    metas = [meta("c", type="capped", weight=1000.0), meta("f", type="free", weight=1.0)]
    assert all(pick(metas).type == "free" for _ in range(20))


def test_returns_none_when_no_type_can_be_selected(tile_type):
    Capped = tile_type("capped")
    Capped.allow = False
    assert pick([meta("c", type="capped")]) is None


def test_unregistered_types_are_not_filtered(tile_type):
    m = meta("x", type="not registered")
    assert pick([m]) is m


def test_multiplier_scales_a_types_odds(tile_type):
    Damped = tile_type("damped")
    tile_type("normal")
    Damped.multiplier = 0.0
    random.seed(3)
    for _ in range(50):
        metas = [meta("d", type="damped", weight=1000.0), meta("n", type="normal", weight=1.0)]
        assert pick(metas).type == "normal"


def test_replacing_is_passed_to_can_select(tile_type):
    Fake = tile_type("fake")
    seen = []
    Fake.can_select = classmethod(lambda cls, replacing=None: seen.append(replacing) or True)
    sentinel = object()
    pick([meta("a")], replacing=sentinel)
    assert seen == [sentinel]


# ----------------------------------------------------------------------
# TileVideo limits (no QMediaPlayer needed: only class methods are used)
# ----------------------------------------------------------------------
def counted_video() -> TileVideo:
    """A TileVideo stand-in that counts against the cap, without creating a player."""
    video = TileVideo.__new__(TileVideo)
    video._counted = True
    return video


def test_video_cap(use_settings, monkeypatch):
    use_settings({"tile video": {"max loaded videos": 2}})
    monkeypatch.setattr(TileVideo, "active_count", 1)
    assert TileVideo.can_select()
    monkeypatch.setattr(TileVideo, "active_count", 2)
    assert not TileVideo.can_select()
    # A video -> video swap frees its own slot
    assert TileVideo.can_select(replacing=counted_video())


@pytest.mark.parametrize("cap, allowed", [(-1, True), (0, False)])
def test_video_cap_special_values(use_settings, cap, allowed):
    use_settings({"tile video": {"max loaded videos": cap}})
    assert TileVideo.can_select() is allowed


@pytest.mark.parametrize("factor, count, expected", [
    (1.0, 5, 1.0),     # 1.0 = no effect
    (0.5, 0, 1.0),
    (0.5, 2, 0.25),
    (0.0, 1, 0.0),
    (-3.0, 1, 0.0),    # negative is invalid and treated as 0
])
def test_video_weight_multiplier(use_settings, monkeypatch, factor, count, expected):
    use_settings({"tile video": {"weight per loaded video": factor}})
    monkeypatch.setattr(TileVideo, "active_count", count)
    assert TileVideo.selection_weight_multiplier() == pytest.approx(expected)


def test_video_swap_seconds_come_from_its_section(use_settings):
    use_settings({"tile video": {"tile swap seconds": [30, 60.5]}})
    assert counted_video().swap_seconds((1.0, 2.0)) == (30.0, 60.5)
