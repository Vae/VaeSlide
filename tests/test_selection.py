"""AssetPicker: weighted picking, per-type caps and multipliers; and the video limits."""

import random

import pytest

from src.AssetPicker import AssetPicker, aspect_key
from src.TileVideo import TileVideo
from helpers import meta


def picker_of(*metas) -> AssetPicker:
    return AssetPicker(metas)


def test_pick_halves_the_picked_weight_in_the_picker_only(tile_type):
    tile_type("fake")
    m = meta("a", weight=8.0)
    picker = picker_of(m)
    assert picker.pick() is m
    assert picker.weight(m) == 4.0
    assert m.weight == 8.0  # the shared MetaTile is never modified


def test_pick_follows_weights(tile_type):
    tile_type("fake")
    random.seed(1)
    counts = {"heavy": 0, "light": 0}
    for _ in range(4000):
        # A fresh picker each draw, so halving doesn't change the odds
        picker = picker_of(meta("heavy", weight=3.0), meta("light", weight=1.0))
        counts[picker.pick().resource_filename] += 1
    assert counts["heavy"] / 4000 == pytest.approx(0.75, abs=0.03)


def test_halving_spreads_picks_out(tile_type):
    tile_type("fake")
    random.seed(2)
    picker = picker_of(*(meta(str(i)) for i in range(5)))
    picked = [picker.pick().resource_filename for _ in range(50)]
    # Repeats are discouraged: every asset shows up, none dominates
    assert set(picked) == {str(i) for i in range(5)}
    assert max(picked.count(str(i)) for i in range(5)) <= 15


def test_all_zero_weights_pick_uniformly(tile_type):
    tile_type("fake")
    metas = [meta("a", weight=0.0), meta("b", weight=0.0)]
    assert picker_of(*metas).pick() in metas  # no exception from random.choices


def test_disabled_asset_is_not_picked(tile_type):
    tile_type("fake")
    a, b = meta("a"), meta("b")
    picker = picker_of(a, b)
    picker.disable(a)
    assert picker.weight(a) == 0.0
    assert all(picker.pick() is b for _ in range(30))


def test_pick_from_candidates(tile_type):
    tile_type("fake")
    a, b, c = meta("a"), meta("b"), meta("c")
    picker = picker_of(a, b, c)
    assert picker.pick([c]) is c
    assert picker.weight(c) == 0.5 and picker.weight(a) == 1.0
    assert picker.pick([]) is None


def test_type_that_cannot_be_selected_is_skipped(tile_type):
    Capped = tile_type("capped")
    tile_type("free")
    Capped.allow = False
    picker = picker_of(meta("c", type="capped", weight=1000.0), meta("f", type="free", weight=1.0))
    assert all(picker.pick().type == "free" for _ in range(20))


def test_returns_none_when_no_type_can_be_selected(tile_type):
    Capped = tile_type("capped")
    Capped.allow = False
    assert picker_of(meta("c", type="capped")).pick() is None


def test_unregistered_types_are_not_filtered(tile_type):
    m = meta("x", type="not registered")
    assert picker_of(m).pick() is m


def test_multiplier_scales_a_types_odds(tile_type):
    Damped = tile_type("damped")
    tile_type("normal")
    Damped.multiplier = 0.0
    random.seed(3)
    for _ in range(50):
        picker = picker_of(meta("d", type="damped", weight=1000.0), meta("n", type="normal", weight=1.0))
        assert picker.pick().type == "normal"


def test_replacing_is_passed_to_can_select(tile_type):
    Fake = tile_type("fake")
    seen = []
    Fake.can_select = classmethod(lambda cls, replacing=None: seen.append(replacing) or True)
    sentinel = object()
    picker_of(meta("a")).pick(replacing=sentinel)
    assert seen == [sentinel]


def test_empty_picker_is_falsy():
    assert not AssetPicker()
    assert AssetPicker([meta("a")])


# ----------------------------------------------------------------------
# Aspect ratios and swap alternatives
# ----------------------------------------------------------------------
@pytest.mark.parametrize("w, h, key", [
    (1920, 1080, (16, 9)), (100, 100, (1, 1)), (300, 200, (3, 2)),
    (0, 10, None), (10, None, None), ("x", 5, None),
])
def test_aspect_key(w, h, key):
    assert aspect_key(meta("m", width=w, height=h)) == key


def test_alternatives_have_the_same_ratio_and_a_different_file():
    square = meta("sq")
    big_square = meta("big", width=500, height=500)
    same_file = meta("sq")  # another entry for the same file
    wide = meta("wide", width=200, height=100)
    picker = picker_of(square, big_square, same_file, wide)
    assert picker.alternatives(square) == [big_square]
    assert picker.alternatives(wide) == []


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
