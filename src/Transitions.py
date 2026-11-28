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
from typing import Callable, ClassVar, Dict, Optional, Tuple, Type

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPolygonF, QTransform

from src import GLSupport, TileShaders
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

    def release(self) -> None:
        """Called once when the transition ends; free per-swap resources (e.g. GL textures) here."""


class ShaderTransition(Transition):
    """
    A transition computed by a GLSL fragment shader when painting on the
    OpenGL renderer, with a QPainter fallback (draw_fallback) everywhere else.

    The shader gets both tiles (drawn with their own tile shaders) as
    textures. FRAGMENT defines `vec4 shade(vec2 uv)`, returning the blended,
    premultiplied colour (see GLSupport for what comes before and after it).
    Available inputs:

        vec2 uv;                    // 0..1 across the tile, (0, 0) at the top left
        uniform sampler2D old_tile; // unit 0; sample with tile_uv(uv)
        uniform sampler2D new_tile; // unit 1; sample with tile_uv(uv)
        uniform float progress;     // 0..1
        uniform vec2 size;          // tile size in device pixels
        vec2 tile_uv(vec2 uv);      // flips uv for the tile textures (rendered upside down by GL)

    Extra uniforms and textures (units 2+) come from set_uniforms() /
    extra_textures(). The "tile transition" -> "use shaders" setting turns
    the shader off, so draw_fallback is always used.
    """
    FRAGMENT: ClassVar[str] = ""
    PREAMBLE: ClassVar[str] = """
uniform sampler2D old_tile;
uniform sampler2D new_tile;
uniform float progress;
uniform vec2 size;
"""

    def __init__(self):
        self._targets = None  # (old TileTarget, new TileTarget), made on the first GL draw

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        use_gl = transition_config().use_shaders and GLSupport.is_gl_painter(painter)
        if not (use_gl and self._draw_gl(painter, old, new, progress)):
            self.draw_fallback(painter, old, new, progress)

    def draw_fallback(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        """The QPainter version, for the raster renderer. Defaults to a fade."""
        FadeTransition().draw(painter, old, new, progress)

    def set_uniforms(self, program, new: Tile, progress: float) -> None:
        """Set this transition's own uniforms, including samplers for units 2+ (the program is bound)."""

    def extra_textures(self, new: Tile) -> Tuple[int, ...]:
        """Texture ids for units 2, 3, ... Create them lazily here (the context is current)."""
        return ()

    def _draw_gl(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> bool:
        rect = tile_rect(new)
        state = GLSupport.PaintState.of(painter, rect)
        if not state.visible():
            return True  # nothing to draw
        program = GLSupport.shader_program(type(self).__name__, self.PREAMBLE + self.FRAGMENT)
        if program is None:
            return False
        if self._targets is None:
            self._targets = (GLSupport.TileTarget(), GLSupport.TileTarget())
        old_target, new_target = self._targets

        def set_uniforms(p) -> None:
            size = old_target.size
            GLSupport.set_sampler(p, "old_tile", 0)
            GLSupport.set_sampler(p, "new_tile", 1)
            GLSupport.set_uniform(p, "progress", progress)
            GLSupport.set_uniform(p, "size", size.width(), size.height())
            self.set_uniforms(p, new, progress)

        with GLSupport.native_painting(painter) as gl:
            old_target.render(gl, old.paint, rect, state.scale, key=TileShaders.paint_key(old))
            new_target.render(gl, new.paint, rect, state.scale, key=TileShaders.paint_key(new))
            extra = self.extra_textures(new)
            GLSupport.draw_quad(gl, program, state, (old_target.texture_id, new_target.texture_id) + extra,
                                set_uniforms)
        return True

    def release(self) -> None:
        if self._targets is not None:
            for target in self._targets:
                target.release()
            self._targets = None


class FadeTransition(Transition):
    """The new tile fades in over the old one."""
    NAME = "fade"

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        old.paint(painter)
        painter.save()
        painter.setOpacity(painter.opacity() * ease_in_out(progress))
        new.paint(painter)
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
        old.paint(painter)
        if progress <= 0.0:
            return
        rect = tile_rect(new)
        clip = QPainterPath()
        clip.addRect(rect)
        clip = clip.intersected(self.star_path(rect, progress))
        painter.save()
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        new.paint(painter)
        painter.restore()


def _draw_scaled(painter: QPainter, tile: Tile, center: QPointF, scale: float, opacity: float = 1.0) -> None:
    """Draw a tile scaled about `center`, at `opacity` (on top of the painter's own)."""
    painter.save()
    painter.translate(center)
    painter.scale(scale, scale)
    painter.translate(-center)
    painter.setOpacity(painter.opacity() * opacity)
    tile.paint(painter)
    painter.restore()


class ZoomTransition(Transition):
    """
    Dolly through: the old tile zooms in towards the viewer while the new
    tile pulls back from a close-up into place, fading in on top.
    """
    NAME = "zoom"
    OLD_ZOOM = 0.6   # the old tile grows to 1 + OLD_ZOOM times its size
    NEW_ZOOM = 0.4   # the new tile starts at 1 + NEW_ZOOM times its size

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        e = ease_in_out(progress)
        rect = tile_rect(new)
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)  # zoomed tiles must not cover neighbours
        _draw_scaled(painter, old, rect.center(), 1.0 + self.OLD_ZOOM * e)
        _draw_scaled(painter, new, rect.center(), 1.0 + self.NEW_ZOOM * (1.0 - e), opacity=e)
        painter.restore()


class SpinTransition(Transition):
    """
    The tile turns over like a card, around its vertical or horizontal axis
    (chosen at random), with the new asset on the back. The card darkens as
    it turns edge-on.
    """
    NAME = "spin"
    MAX_SHADE = 0.6  # darkness when edge-on

    def __init__(self, rng: Optional[random.Random] = None):
        rng = rng or random
        self.axis = rng.choice((Qt.Axis.YAxis, Qt.Axis.XAxis))
        self.direction = rng.choice((-1.0, 1.0))

    def draw(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        angle = 180.0 * ease_in_out(progress)  # 0..180 degrees
        # First half: the old face turns away. Second half: the new face turns in.
        tile, face_angle = (old, angle) if angle < 90.0 else (new, angle - 180.0)
        rect = tile_rect(new)
        center = rect.center()
        turn = QTransform()
        turn.translate(center.x(), center.y())
        turn.rotate(self.direction * face_angle, self.axis)
        turn.translate(-center.x(), -center.y())

        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)  # perspective may bulge past the rect
        painter.setTransform(turn, combine=True)
        tile.paint(painter)
        shade = self.MAX_SHADE * abs(math.sin(math.radians(face_angle)))
        if shade > 0.0:
            painter.fillRect(rect, QColor(0, 0, 0, round(255 * shade)))
        painter.restore()


# ----------------------------------------------------------------------
# Masks for MaskTransition: (width, height, rng) -> float32 array (h, w)
# where low values are revealed first. Values needn't be spread evenly:
# MaskTransition equalizes them so the revealed area grows steadily.
# ----------------------------------------------------------------------
MaskFn = Callable[[int, int, random.Random], np.ndarray]


def _grid(w: int, h: int) -> Tuple[np.ndarray, np.ndarray]:
    """Pixel-centre coordinates scaled to 0..1 on the longer side (so shapes aren't stretched)."""
    size = max(w, h)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return (xs + 0.5) / size, (ys + 0.5) / size


def _value_noise(w: int, h: int, cells: int, rng: random.Random) -> np.ndarray:
    """
    Smooth random values: random numbers on a coarse grid (`cells` across the
    longer side), blended between grid points with smoothstep. Kept in
    float32 so neighbouring pixels practically never tie (ties would be
    revealed in raster order, as hard lines).
    """
    cells_x = max(1, round(cells * w / max(w, h)))
    cells_y = max(1, round(cells * h / max(w, h)))
    grid = np.array([[rng.random() for _ in range(cells_x + 1)] for _ in range(cells_y + 1)], dtype=np.float32)

    def axis(n_pixels: int, n_cells: int):
        pos = (np.arange(n_pixels, dtype=np.float32) + 0.5) * n_cells / n_pixels
        i = np.minimum(pos.astype(np.int32), n_cells - 1)
        t = pos - i
        return i, t * t * (3.0 - 2.0 * t)

    ix, tx = axis(w, cells_x)
    iy, ty = axis(h, cells_y)
    tx, ty = tx[None, :], ty[:, None]
    top = grid[iy][:, ix] * (1 - tx) + grid[iy][:, ix + 1] * tx
    bottom = grid[iy + 1][:, ix] * (1 - tx) + grid[iy + 1][:, ix + 1] * tx
    return top * (1 - ty) + bottom * ty


def mask_noise(w: int, h: int, rng: random.Random) -> np.ndarray:
    """Soft random blobs: a dissolve that blooms in patches."""
    return _value_noise(w, h, 6, rng) + 0.35 * _value_noise(w, h, 17, rng)


def mask_linear(w: int, h: int, rng: random.Random) -> np.ndarray:
    """A straight wipe at a random angle."""
    xs, ys = _grid(w, h)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    return xs * math.cos(angle) + ys * math.sin(angle)


def mask_radial(w: int, h: int, rng: random.Random) -> np.ndarray:
    """A circle opening from a random point (or closing in on it)."""
    xs, ys = _grid(w, h)
    cx, cy = rng.uniform(0.2, 0.8) * w / max(w, h), rng.uniform(0.2, 0.8) * h / max(w, h)
    distance = np.hypot(xs - cx, ys - cy)
    return distance if rng.random() < 0.5 else -distance


def mask_clock(w: int, h: int, rng: random.Random) -> np.ndarray:
    """A clock-hand sweep around the centre, from a random start, either way round."""
    xs, ys = _grid(w, h)
    cx, cy = 0.5 * w / max(w, h), 0.5 * h / max(w, h)
    angle = np.arctan2(ys - cy, xs - cx) - rng.uniform(0.0, 2.0 * math.pi)
    angle = np.mod(angle, 2.0 * math.pi)
    return angle if rng.random() < 0.5 else -angle


def mask_blinds(w: int, h: int, rng: random.Random) -> np.ndarray:
    """Venetian blinds: stripes that all open at once, vertical or horizontal."""
    xs, ys = _grid(w, h)
    bands = rng.randint(5, 10)
    coord = xs * max(w, h) / w if rng.random() < 0.5 else ys * max(w, h) / h  # 0..1 across the tile
    # Small tie-breaker so each stripe opens from one side, not all pixels at once
    return np.mod(coord * bands, 1.0) + coord * 1e-3


MASKS: Dict[str, MaskFn] = {
    "noise": mask_noise,
    "linear": mask_linear,
    "radial": mask_radial,
    "clock": mask_clock,
    "blinds": mask_blinds,
}


def equalize(values: np.ndarray) -> np.ndarray:
    """Replace values by their rank, scaled to 0..1, so any mask reveals area at a steady rate."""
    flat = values.ravel()
    ranks = np.empty(flat.size, dtype=np.float32)
    ranks[np.argsort(flat, kind="stable")] = np.arange(flat.size, dtype=np.float32)
    return (ranks / max(1, flat.size - 1)).reshape(values.shape)


class MaskTransition(ShaderTransition):
    """
    Reveals the new tile through a grayscale mask (see MASKS): pixels whose
    mask value is below the progress show the new tile, with a soft edge.

    On OpenGL the mask is uploaded once per swap as a float texture and the
    shader does the per-pixel blend; elsewhere draw_fallback does it with
    numpy and QImage composition.
    """
    NAME = "mask"
    # Masks are computed at most this many px on the long side and scaled up
    # smoothly when drawn; they're smooth, so this costs no visible detail and
    # keeps large tiles cheap.
    MAX_MASK_SIZE = 320

    # Same formula as alpha(), per pixel
    FRAGMENT = """
uniform sampler2D mask;
uniform float softness;
vec4 shade(vec2 p) {
    float m = texture(mask, p).r;
    float a = softness <= 0.0 ? float(m < progress)
                              : clamp((progress * (1.0 + softness) - m) / softness, 0.0, 1.0);
    return mix(texture(old_tile, tile_uv(p)), texture(new_tile, tile_uv(p)), a);
}
"""

    def __init__(self, shape: Optional[str] = None, softness: Optional[float] = None,
                 rng: Optional[random.Random] = None):
        super().__init__()
        self._mask_texture = None  # GL texture of self._mask, made on the first GL draw
        self._mask_texture_of: Optional[np.ndarray] = None
        cfg = transition_config()
        self._rng = rng or random.Random()
        shape = shape or cfg.mask_shape
        self.shape = self._rng.choice(list(MASKS)) if shape == "random" else shape
        self.softness = cfg.mask_softness if softness is None else softness
        self._mask: Optional[np.ndarray] = None  # built on first draw

    @classmethod
    def mask_size(cls, w: int, h: int) -> Tuple[int, int]:
        """(width, height) the mask is computed at for a w x h tile."""
        scale = min(1.0, cls.MAX_MASK_SIZE / max(w, h))
        return max(1, round(w * scale)), max(1, round(h * scale))

    def mask_for(self, w: int, h: int) -> np.ndarray:
        """The equalized mask for a w x h tile, at mask_size() resolution."""
        mw, mh = self.mask_size(w, h)
        if self._mask is None or self._mask.shape != (mh, mw):
            self._mask = equalize(MASKS[self.shape](mw, mh, self._rng))
        return self._mask

    def alpha(self, w: int, h: int, progress: float) -> np.ndarray:
        """Opacity of the new tile, 0..1, per mask pixel (mask_size() resolution)."""
        mask = self.mask_for(w, h)
        soft = self.softness
        if soft <= 0.0:
            return (mask < progress).astype(np.float32)
        # Stretched so the soft edge starts fully outside at 0 and ends fully inside at 1
        return np.clip((progress * (1.0 + soft) - mask) / soft, 0.0, 1.0)

    def extra_textures(self, new: Tile) -> Tuple[int, ...]:
        mask = self.mask_for(int(new.width), int(new.height))
        if self._mask_texture_of is not mask:  # first draw, or rebuilt for a new size
            GLSupport.defer_delete(self._mask_texture)
            self._mask_texture = GLSupport.float_texture(mask)
            self._mask_texture_of = mask
        return (self._mask_texture.textureId(),)

    def set_uniforms(self, program, new: Tile, progress: float) -> None:
        GLSupport.set_sampler(program, "mask", 2)
        GLSupport.set_uniform(program, "softness", self.softness)

    def release(self) -> None:
        super().release()
        GLSupport.defer_delete(self._mask_texture)
        self._mask_texture = self._mask_texture_of = None

    def draw_fallback(self, painter: QPainter, old: Tile, new: Tile, progress: float) -> None:
        old.paint(painter)
        w, h = int(new.width), int(new.height)
        if progress <= 0.0 or w <= 0 or h <= 0:
            return
        x = int(new.x)

        alpha = self.alpha(w, h, progress)
        mh, mw = alpha.shape
        stride = (mw + 3) // 4 * 4  # QImage rows are 32-bit aligned
        alpha8 = np.zeros((mh, stride), dtype=np.uint8)
        alpha8[:, :mw] = (alpha * 255.0 + 0.5).astype(np.uint8)
        alpha_image = QImage(alpha8.data, mw, mh, stride, QImage.Format.Format_Alpha8)

        # Draw the new tile into its own layer, cut it out with the mask, then put it on top
        layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        layer_painter.translate(-x, 0)
        new.paint(layer_painter)
        layer_painter.resetTransform()
        layer_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        layer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        layer_painter.drawImage(QRectF(0, 0, w, h), alpha_image)  # scaled up to the tile
        layer_painter.end()
        painter.drawImage(x, 0, layer)


TRANSITIONS: Dict[str, Type[Transition]] = {
    cls.NAME: cls for cls in (FadeTransition, StarSwipeTransition, ZoomTransition, SpinTransition, MaskTransition)
}


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class TransitionConfig:
    type: str             # "random", "none", or a key of TRANSITIONS
    seconds: float        # >= 0; 0 = no transition
    star_points: int      # 3..12
    mask_shape: str       # "random" or a key of MASKS
    mask_softness: float  # 0..1; width of the mask's soft edge
    use_shaders: bool = True  # False: shader transitions always use their QPainter fallback


@section_config
def transition_config() -> TransitionConfig:
    """Validated "tile transition" settings, read once."""
    section = get_settings().section(SETTINGS_SECTION)
    kind = section.get("type")
    if kind not in ("random", "none") and kind not in TRANSITIONS:
        logger.error("Setting \"%s.type\" must be \"random\", \"none\" or one of %s, got %r. Using \"random\".",
                     SETTINGS_SECTION, list(TRANSITIONS), kind)
        kind = "random"
    shape = section.get("mask shape")
    if shape != "random" and shape not in MASKS:
        logger.error("Setting \"%s.mask shape\" must be \"random\" or one of %s, got %r. Using \"random\".",
                     SETTINGS_SECTION, list(MASKS), shape)
        shape = "random"
    return TransitionConfig(
        type=kind,
        seconds=max(0.0, section.get_number("seconds")),
        star_points=min(12, max(3, section.get_number("star points", int))),
        mask_shape=shape,
        mask_softness=min(1.0, max(0.0, section.get_number("mask softness"))),
        use_shaders=bool(section.get("use shaders", True)),
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
        # The old tile has left the strip, so its animated shaders are ticked from here
        TileShaders.tick(self.old, now, centre_on_screen=True)
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
        _discard(old)
        return
    new.transition = ActiveTransition(effect, old, now, transition_config().seconds)


def finish(tile: Tile) -> None:
    """End the tile's transition, if any, and release the old tile it was holding."""
    active = tile.transition
    if active is not None:
        tile.transition = None
        active.effect.release()
        _discard(active.old)


def release(tile: Tile) -> None:
    """Discard a tile for good: end its transition, then free its shaders and call removed()."""
    finish(tile)
    _discard(tile)


def _discard(tile: Tile) -> None:
    TileShaders.release(tile)
    tile.removed()


def draw_tile(painter: QPainter, tile: Tile) -> None:
    """Draw a tile, through its transition if one is running."""
    if tile.transition is not None:
        tile.transition.draw(painter, tile)
    else:
        tile.paint(painter)
