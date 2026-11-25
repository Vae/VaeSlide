"""
Transitions: how a swapped tile changes from its old asset to its new one.

When TileSwapper puts a replacement in place, the new tile takes the slot
immediately and carries an ActiveTransition that keeps the old tile alive.
Until the transition ends, the strip draws the pair through it instead of
drawing the new tile directly. Then the old tile is released.

To add a transition: subclass Transition, set NAME, implement draw(), and
add the class to the tuple behind TRANSITIONS. It becomes selectable in
settings.json under "tile transition" -> "type".
"""

import logging
import math
import random
from dataclasses import dataclass
from typing import ClassVar, Dict, Optional, Type

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPolygonF

from src.Settings import get_settings, section_config
from src.Tile import Tile

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "tile transition"  # this module's section in settings.json


def ease_in_out(t: float) -> float:
    """Smoothstep: slow start, slow finish. t and the result are 0..1."""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def tile_rect(tile: Tile) -> QRectF:
    """The tile's on-strip rectangle, in the painter's (strip) coordinates."""
    return QRectF(tile.x, 0.0, tile.width, tile.height)


# ----------------------------------------------------------------------
# Transition types
# ----------------------------------------------------------------------
class Transition:
    """
    One kind of transition. Instances are created per swap, so a transition
    may keep random per-swap state (e.g. a rotation) in __init__.
    """
    NAME: ClassVar[str] = ""

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        """
        Draw the pair at `progress` (0 = only the old tile, 1 = only the new
        one). Both tiles occupy the same rectangle (see tile_rect). Leave the
        painter's state as it was (use save/restore).
        """
        raise NotImplementedError


class FadeTransition(Transition):
    """The new tile fades in over the old one."""
    NAME = "fade"

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        old.draw(painter)
        painter.save()
        painter.setOpacity(painter.opacity() * ease_in_out(progress))
        new.draw(painter)
        painter.restore()


class StarSwipeTransition(Transition):
    """A star grows (and turns) from the middle of the tile, revealing the new tile inside it."""
    NAME = "star swipe"
    INNER_RATIO = 0.45   # inner vertex radius as a fraction of the outer radius
    TURN_DEGREES = 72.0  # how far the star rotates over the whole transition

    def __init__(self, points: Optional[int] = None, rng: Optional[random.Random] = None):
        self.points = points if points is not None else transition_config().star_points
        rng = rng or random
        self._start_angle = rng.uniform(0.0, 360.0)
        self._spin = rng.choice((-1.0, 1.0))

    def star_path(self, rect: QRectF, progress: float) -> QPainterPath:
        """The star at `progress`, centred on `rect`. At 1 it covers the whole rect."""
        # Nearest star edge to the centre is an inner vertex, so the rect is
        # fully covered once the inner radius reaches the rect's half-diagonal.
        half_diagonal = math.hypot(rect.width(), rect.height()) / 2.0
        outer = ease_in_out(progress) * half_diagonal / self.INNER_RATIO
        inner = outer * self.INNER_RATIO
        angle0 = math.radians(self._start_angle + self._spin * self.TURN_DEGREES * progress)
        center = rect.center()
        step = math.pi / self.points
        polygon = QPolygonF([
            QPointF(center.x() + r * math.cos(angle0 + i * step), center.y() + r * math.sin(angle0 + i * step))
            for i, r in ((i, outer if i % 2 == 0 else inner) for i in range(2 * self.points))
        ])
        path = QPainterPath()
        path.addPolygon(polygon)
        path.closeSubpath()
        return path

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        old.draw(painter)
        if progress <= 0.0:
            return
        rect = tile_rect(new)
        clip = QPainterPath()
        clip.addRect(rect)
        clip = clip.intersected(self.star_path(rect, progress))
        painter.save()
        painter.setClipPath(clip)
        new.draw(painter)
        painter.restore()


TRANSITIONS: Dict[str, Type[Transition]] = {
    cls.NAME: cls for cls in (FadeTransition, StarSwipeTransition)
}


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class TransitionConfig:
    type: str          # "random", "none", or a key of TRANSITIONS
    seconds: float     # >= 0; 0 = no transition
    star_points: int   # 3..12


@section_config
def transition_config() -> TransitionConfig:
    """Validated "tile transition" settings, read once."""
    section = get_settings().section(SETTINGS_SECTION)
    kind = section.get("type")
    if kind not in ("random", "none") and kind not in TRANSITIONS:
        logger.error("Setting \"%s.type\" must be \"random\", \"none\" or one of %s, got %r. Using \"random\".",
                     SETTINGS_SECTION, list(TRANSITIONS), kind)
        kind = "random"
    return TransitionConfig(
        type=kind,
        seconds=max(0.0, section.get_number("seconds")),
        star_points=min(12, max(3, section.get_number("star points", int))),
    )


def new_transition() -> Optional[Transition]:
    """A transition for one swap, following the settings; None means swap instantly."""
    cfg = transition_config()
    if cfg.type == "none" or cfg.seconds <= 0 or not TRANSITIONS:
        return None
    cls = random.choice(list(TRANSITIONS.values())) if cfg.type == "random" else TRANSITIONS[cfg.type]
    return cls()


# ----------------------------------------------------------------------
# A transition in progress
# ----------------------------------------------------------------------
class ActiveTransition:
    """A running transition, attached to the new tile as `tile.transition`."""

    def __init__(self, effect: Transition, old: Tile, start: float, duration: float):
        self.effect = effect
        self.old = old
        self.start = start
        self.duration = duration
        self.progress = 0.0

    def update(self, now: float) -> bool:
        """Advance to `now`. Returns True once the transition has finished."""
        self.progress = 1.0 if self.duration <= 0 else min(1.0, max(0.0, (now - self.start) / self.duration))
        return self.progress >= 1.0

    def draw(self, painter: QPainter, new: Tile) -> None:
        self.old.x = new.x  # the old tile rides along with the slot as it scrolls
        self.effect.draw(painter, self.old, new, self.progress)


def begin(new: Tile, old: Tile, now: float) -> None:
    """
    Make `new` replace `old` with a transition, or instantly if transitions
    are off. Takes ownership of `old`: it is released when the transition ends.
    """
    finish(old)  # `old` may itself still be mid-transition
    effect = new_transition()
    if effect is None:
        old.removed()
        return
    new.transition = ActiveTransition(effect, old, now, transition_config().seconds)


def finish(tile: Tile) -> None:
    """End the tile's transition, if any, and release the old tile it was holding."""
    active = tile.transition
    if active is not None:
        tile.transition = None
        active.old.removed()


def release(tile: Tile) -> None:
    """Discard a tile for good: end its transition, then call removed()."""
    finish(tile)
    tile.removed()


def draw_tile(painter: QPainter, tile: Tile) -> None:
    """Draw a tile, through its transition if one is running."""
    if tile.transition is not None:
        tile.transition.draw(painter, tile)
    else:
        tile.draw(painter)
