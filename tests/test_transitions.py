"""Transitions between a swapped tile's old and new asset."""

import random
import types

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

import src.HorizontalStrip as strip_module
from src import Transitions
from src.Transitions import (TRANSITIONS, ActiveTransition, FadeTransition, StarSwipeTransition,
                             Transition, ease_in_out, new_transition, transition_config)
from helpers import make_strip, meta

OLD_COLOR, NEW_COLOR = QColor("red"), QColor("blue")


@pytest.fixture
def clock(monkeypatch):
    c = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(strip_module, "time", types.SimpleNamespace(monotonic=lambda: c.now))
    return c


@pytest.fixture
def solid_type(tile_type):
    """Fake tiles that paint their rectangle in one colour (red unless told otherwise)."""
    Solid = tile_type("fake")

    def draw(self, painter):
        painter.fillRect(QRectF(self.x, 0, self.width, self.height), getattr(self, "color", OLD_COLOR))

    Solid.draw = draw
    return Solid


def render(effect: Transition, old, new, progress: float, size=(100, 100)) -> QImage:
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    effect.draw(painter, old, new, progress)
    painter.end()
    return image


def pair(solid_type, w=100, h=100):
    old = solid_type(meta("old", w, h), 0.0, h, None)
    new = solid_type(meta("new", w, h), 0.0, h, None)
    old.color, new.color = OLD_COLOR, NEW_COLOR
    return old, new


def color_at(image: QImage, x: int, y: int) -> QColor:
    return image.pixelColor(x, y)


# ----------------------------------------------------------------------
# Settings and selection
# ----------------------------------------------------------------------
def test_registry():
    assert set(TRANSITIONS) == {"fade", "star swipe"}
    for name, cls in TRANSITIONS.items():
        assert cls.NAME == name and issubclass(cls, Transition)


def test_config_validation(use_settings):
    use_settings({"tile transition": {"type": "wobble", "seconds": -2, "star points": 50}})
    cfg = transition_config()
    assert (cfg.type, cfg.seconds, cfg.star_points) == ("random", 0.0, 12)


@pytest.mark.parametrize("kind, expected", [("fade", FadeTransition), ("star swipe", StarSwipeTransition)])
def test_named_transition(use_settings, kind, expected):
    use_settings({"tile transition": {"type": kind}})
    assert all(isinstance(new_transition(), expected) for _ in range(10))


def test_random_uses_every_transition(use_settings):
    use_settings({"tile transition": {"type": "random"}})
    random.seed(7)
    assert {type(new_transition()) for _ in range(40)} == set(TRANSITIONS.values())


@pytest.mark.parametrize("section", [{"type": "none"}, {"type": "fade", "seconds": 0}])
def test_transitions_can_be_turned_off(use_settings, section):
    use_settings({"tile transition": section})
    assert new_transition() is None


def test_ease_in_out():
    assert ease_in_out(-1) == 0.0 and ease_in_out(0) == 0.0
    assert ease_in_out(0.5) == 0.5
    assert ease_in_out(1) == 1.0 and ease_in_out(2) == 1.0
    assert ease_in_out(0.1) < 0.1 and ease_in_out(0.9) > 0.9  # slow start, slow finish


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
def test_fade_blends(qapp, solid_type):
    old, new = pair(solid_type)
    effect = FadeTransition()
    assert color_at(render(effect, old, new, 0.0), 50, 50) == OLD_COLOR
    assert color_at(render(effect, old, new, 1.0), 50, 50) == NEW_COLOR
    mid = color_at(render(effect, old, new, 0.5), 50, 50)
    assert 100 < mid.red() < 155 and 100 < mid.blue() < 155


def test_star_swipe_grows_from_the_center(qapp, solid_type):
    old, new = pair(solid_type, 160, 90)
    effect = StarSwipeTransition(points=5, rng=random.Random(1))
    start = render(effect, old, new, 0.0, (160, 90))
    assert color_at(start, 80, 45) == OLD_COLOR
    mid = render(effect, old, new, 0.5, (160, 90))
    assert color_at(mid, 80, 45) == NEW_COLOR   # the centre is revealed first
    assert color_at(mid, 1, 1) == OLD_COLOR     # the corners last
    end = render(effect, old, new, 1.0, (160, 90))
    for x, y in [(0, 0), (159, 0), (0, 89), (159, 89), (80, 45)]:
        assert color_at(end, x, y) == NEW_COLOR, f"({x}, {y}) not covered at the end"


@pytest.mark.parametrize("points", [3, 5, 8, 12])
def test_star_path_covers_the_tile_at_the_end(points):
    rect = QRectF(10, 0, 300, 100)
    path = StarSwipeTransition(points=points, rng=random.Random(points)).star_path(rect, 1.0)
    for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
        assert path.contains(corner)
    assert path.elementCount() >= 2 * points
    assert not StarSwipeTransition(points=points).star_path(rect, 0.0).contains(QPointF(150, 50))


def test_star_swipe_only_draws_inside_the_tile(qapp, solid_type):
    """The new tile must not spill outside its rectangle (onto neighbours)."""
    old, new = pair(solid_type, 50, 100)
    old.x = new.x = 25.0
    image = render(StarSwipeTransition(points=5), old, new, 0.5)
    for x in (0, 10, 80, 99):
        assert color_at(image, x, 50) == QColor("black")


# ----------------------------------------------------------------------
# Lifecycle in a strip
# ----------------------------------------------------------------------
def swapped_strip(solid_type, clock, use_settings, seconds=1.0):
    use_settings({"tile transition": {"type": "fade", "seconds": seconds}})
    strip = make_strip([meta(f"m{i}") for i in range(4)], swap=(1.0, 1.0), margin=100)
    clock.now = 0.0
    strip.scroll(0.0)
    old = next(t for t in strip.tiles if round(t.x) == 400)
    clock.now = 1.5
    strip.scroll(0.0)  # requests the swap
    strip.scroll(0.0)  # replacement is ready: transition starts
    new = next(t for t in strip.tiles if round(t.x) == 400)
    assert new is not old
    return strip, old, new


def test_transition_runs_then_releases_the_old_tile(qapp, solid_type, clock, use_settings):
    strip, old, new = swapped_strip(solid_type, clock, use_settings, seconds=2.0)
    assert new.transition.old is old and new.transition.progress == 0.0
    clock.now = 2.5
    strip.scroll(0.0)
    assert new.transition.progress == pytest.approx(0.5)
    assert not old.removed_called
    clock.now = 3.6
    strip.scroll(0.0)
    assert new.transition is None and old.removed_called


def test_old_tile_follows_the_slot_while_scrolling(qapp, solid_type, clock, use_settings):
    strip, old, new = swapped_strip(solid_type, clock, use_settings)
    strip.scroll(30.0)
    image = QImage(1000, 100, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    strip.draw(painter)
    painter.end()
    assert old.x == new.x


def test_culled_tile_releases_its_old_tile(qapp, solid_type, clock, use_settings):
    strip, old, new = swapped_strip(solid_type, clock, use_settings, seconds=100.0)
    for _ in range(20):
        strip.scroll(50.0)  # scroll the pair off screen
    assert new not in strip.tiles
    assert new.removed_called and old.removed_called


def test_resize_finishes_transitions(qapp, solid_type, clock, use_settings):
    strip, old, new = swapped_strip(solid_type, clock, use_settings, seconds=100.0)
    strip.resize(1000, 150)
    assert new.transition is None and old.removed_called


def test_strip_removed_releases_everything(qapp, solid_type, clock, use_settings):
    strip, old, new = swapped_strip(solid_type, clock, use_settings, seconds=100.0)
    strip.removed()
    assert old.removed_called and new.removed_called


def test_no_transition_swaps_instantly(qapp, solid_type, clock, use_settings):
    strip, old, new = swapped_strip(solid_type, clock, use_settings, seconds=0.0)
    assert new.transition is None and old.removed_called


def test_swapping_again_mid_transition(solid_type):
    a, b, c = (solid_type(meta(n), 0.0, 100, None) for n in "abc")
    Transitions.begin(b, a, now=0.0)
    Transitions.begin(c, b, now=0.1)  # b is replaced before its own transition ended
    assert a.removed_called            # b's old tile was released
    assert c.transition.old is b and not b.removed_called
    assert b.transition is None


def test_active_transition_progress(solid_type):
    old = solid_type(meta("o"), 0, 100, None)
    active = ActiveTransition(FadeTransition(), old, start=10.0, duration=4.0)
    assert not active.update(9.0) and active.progress == 0.0
    assert not active.update(11.0) and active.progress == 0.25
    assert active.update(14.0) and active.progress == 1.0
    assert ActiveTransition(FadeTransition(), old, 0.0, 0.0).update(0.0)
