"""
OpenGL plumbing for shader effects drawn in the middle of QPainter painting.

Everything here works only while painting on the OpenGL renderer
(GLEndlessScrollWidget). Callers check is_gl_painter() first and fall back
to plain QPainter drawing otherwise (the raster renderer, the offscreen
test platform, a driver that fails to compile a shader).

A shader draw goes:

    state = PaintState.of(painter, rect)      # transform, opacity, clip: before QPainter steps aside
    program = shader_program(name, source)    # compiled once per context
    with native_painting(painter) as gl:      # QPainter hands GL over to us
        target.render(gl, tile.draw, rect, state.scale)  # draw a tile into a texture (FBO)
        draw_quad(gl, program, state, (target.texture_id,), set_uniforms)  # run the shader over the rect

Fragment shaders define `vec4 shade(vec2 uv)` returning a premultiplied
colour; FRAGMENT_HEADER comes before it and FRAGMENT_MAIN after it.
FRAGMENT_MAIN applies the painter's opacity and clip, so a shader quad
behaves like any QPainter drawing: it follows the painter's transform
(even a perspective one), fades with its opacity and respects its clip.

Offscreen targets nest (a shaded tile inside a shader transition): each
TileTarget binds itself on a stack and rebinds whatever was bound before.

GL objects must be deleted while their context is current, which isn't the
case when a transition ends or a tile is culled (that happens in the tick,
not while painting). So owners hand them to defer_delete(), and the widget
calls collect_garbage() at the start of every paintGL.
"""

import contextlib
import logging
import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np
import shiboken6
from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import (QColor, QOpenGLContext, QOpenGLFunctions, QPaintEngine, QPainter, QPainterPath,
                           QTransform, QVector2D, QVector3D, QVector4D)
from PySide6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLPaintDevice, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject)

logger = logging.getLogger(__name__)

# GL enums (PySide6 has no GL constants without PyOpenGL)
GL_TRIANGLE_STRIP = 0x0005
GL_BLEND = 0x0BE2
GL_SCISSOR_TEST = 0x0C11
GL_STENCIL_TEST = 0x0B90
GL_DEPTH_TEST = 0x0B71
GL_ONE = 1
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_COLOR_BUFFER_BIT = 0x4000

MAX_TARGET_SIZE = 4096  # px; offscreen targets are capped to this on each side
SCALE_STEPS = 8         # tile textures are rendered at scales rounded up to 1/SCALE_STEPS
CLIP_GRANULARITY = 128  # px; clip masks are rounded up to multiples of this (a growing clip reuses its buffer)
CLIP_UNIT = 7           # texture unit of the clip coverage mask; shaders use units below it

# Every shader quad uses this vertex shader: a triangle strip of 4 vertices
# generated from gl_VertexID, so no vertex buffer is needed. The quad is
# `rect` (x, y, w, h) in painter coordinates, mapped to device pixels by
# `transform` (the painter's device transform, possibly a perspective one)
# and from there to clip space. w = d.z keeps texturing perspective-correct.
VERTEX_SHADER = """
#version 330
uniform vec4 rect;
uniform mat3 transform;
uniform vec2 target;
out vec2 uv;
void main() {
    vec2 corner = vec2(gl_VertexID & 1, gl_VertexID >> 1);  // (0,0) (1,0) (0,1) (1,1)
    uv = corner;                                             // (0, 0) at the rect's top left
    vec3 d = transform * vec3(rect.xy + corner * rect.zw, 1.0);
    vec2 ndc = vec2(2.0 * d.x / d.z / target.x - 1.0, 1.0 - 2.0 * d.y / d.z / target.y);
    gl_Position = vec4(ndc * d.z, 0.0, d.z);
}
"""

FRAGMENT_HEADER = """#version 330
in vec2 uv;
out vec4 color;
// Textures rendered by TileTarget are upside down (GL's origin is bottom left)
vec2 tile_uv(vec2 p) { return vec2(p.x, 1.0 - p.y); }
"""

FRAGMENT_MAIN = """
uniform float opacity;
uniform bool clipped;
uniform sampler2D clip_mask;
uniform vec4 clip_box;  // device px: x, y, w, h of clip_mask
uniform vec2 target;
void main() {
    float coverage = 1.0;
    if (clipped) {
        vec2 p = (vec2(gl_FragCoord.x, target.y - gl_FragCoord.y) - clip_box.xy) / clip_box.zw;
        coverage = (any(lessThan(p, vec2(0.0))) || any(greaterThan(p, vec2(1.0))))
                 ? 0.0 : texture(clip_mask, tile_uv(p)).a;
    }
    color = shade(uv) * (opacity * coverage);
}
"""


def is_gl_painter(painter: QPainter) -> bool:
    """True when `painter` draws through Qt's OpenGL paint engine with a current context."""
    engine = painter.paintEngine()
    return (engine is not None and engine.type() == QPaintEngine.Type.OpenGL2
            and QOpenGLContext.currentContext() is not None)


# ----------------------------------------------------------------------
# Per-context state: compiled programs, the empty VAO, deferred deletes
# ----------------------------------------------------------------------
class _ContextState:
    def __init__(self, context: QOpenGLContext):
        self.context = context  # keeps the Python wrapper alive while we cache things for it
        self.programs: Dict[str, Optional[QOpenGLShaderProgram]] = {}  # None: failed to build, don't retry
        self.uniforms: Dict[Tuple[int, str], int] = {}  # (id(program), name) -> location
        self.vao: Optional[QOpenGLVertexArrayObject] = None
        self.clip_target: Optional["TileTarget"] = None  # reused for every clipped quad


# Keyed by the C++ context's address: PySide may hand out a new Python wrapper
# for the same context on each currentContext() call, so id() would not match.
_states: Dict[int, _ContextState] = {}
_garbage: List[object] = []
_bound: List[QOpenGLFramebufferObject] = []  # offscreen targets currently bound, innermost last


def _state() -> _ContextState:
    context = QOpenGLContext.currentContext()
    key = shiboken6.getCppPointer(context)[0]
    state = _states.get(key)
    if state is None:
        state = _states[key] = _ContextState(context)
        # Drop the programs (with the context current) before the context goes away
        context.aboutToBeDestroyed.connect(lambda: _forget_context(key))
    return state


def _location(program: QOpenGLShaderProgram, name: str) -> int:
    """program.uniformLocation(name), cached (the lookup is a string search in the driver)."""
    cache = _state().uniforms
    key = (id(program), name)  # programs live in the cache as long as the context does
    location = cache.get(key)
    if location is None:
        location = cache[key] = program.uniformLocation(name)
    return location


def _forget_context(key: int) -> None:
    state = _states.pop(key, None)
    if state is not None:
        state.programs.clear()
        state.uniforms.clear()
        state.vao = None
        if state.clip_target is not None:
            state.clip_target.release()
    collect_garbage()


def defer_delete(*objects: object) -> None:
    """Keep GL objects alive until collect_garbage() runs with their context current."""
    _garbage.extend(o for o in objects if o is not None)


def collect_garbage() -> None:
    """Delete everything handed to defer_delete(). Call with the GL context current."""
    for obj in _garbage:
        if isinstance(obj, QOpenGLTexture):
            obj.destroy()
    _garbage.clear()  # dropping the last Python reference runs the C++ destructors


def pending_garbage() -> int:
    """How many objects are waiting for collect_garbage() (for tests)."""
    return len(_garbage)


def shader_program(name: str, shade_source: str) -> Optional[QOpenGLShaderProgram]:
    """
    The program for VERTEX_SHADER and a fragment shader made of
    FRAGMENT_HEADER + `shade_source` (which defines `vec4 shade(vec2 uv)`) +
    FRAGMENT_MAIN. Compiled once per context and cached under `name`. Returns
    None (and logs once) when it doesn't build, so callers can fall back.
    """
    state = _state()
    if name not in state.programs:
        program = QOpenGLShaderProgram()
        ok = (program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER)
              and program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
                                                  FRAGMENT_HEADER + shade_source + FRAGMENT_MAIN)
              and program.link())
        if not ok:
            logger.error("Shader %r failed to build; using the QPainter fallback.\n%s", name, program.log())
            program = None
        state.programs[name] = program
    return state.programs[name]


def set_uniform(program: QOpenGLShaderProgram, name: str, *values: float) -> None:
    """Set a float / vec2 / vec3 / vec4 uniform (PySide6's own overloads are ambiguous for plain numbers)."""
    location = _location(program, name)
    if location < 0:
        return  # unused uniforms are compiled away; not an error
    if len(values) == 1:
        program.setUniformValue1f(location, float(values[0]))
    else:
        vector = (QVector2D, QVector3D, QVector4D)[len(values) - 2]
        program.setUniformValue(location, vector(*(float(v) for v in values)))


def set_sampler(program: QOpenGLShaderProgram, name: str, unit: int) -> None:
    """Point a sampler uniform at texture unit `unit` (also used for bool/int uniforms)."""
    location = _location(program, name)
    if location >= 0:
        program.setUniformValue1i(location, int(unit))


@contextlib.contextmanager
def native_painting(painter: QPainter) -> Iterator[QOpenGLFunctions]:
    """
    Suspend QPainter so raw GL can run; yields the context's GL functions.
    QPainter restores its own state afterwards, but the GL state it doesn't
    track (bound textures, VAO) is ours to reset, which the helpers here do.
    """
    painter.beginNativePainting()
    try:
        yield QOpenGLContext.currentContext().functions()
    finally:
        painter.endNativePainting()


# ----------------------------------------------------------------------
# Where a quad goes: the painter's state, captured before native painting
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PaintState:
    """
    How a quad over `rect` is put on the current target: the painter's
    device transform, opacity and clip (in device pixels), plus the target's
    size in device pixels.
    """
    rect: QRectF               # painter coordinates
    transform: QTransform      # painter coordinates -> device pixels
    opacity: float
    clip: Optional[QPainterPath]  # device pixels; None = unclipped
    target: QSize

    @classmethod
    def of(cls, painter: QPainter, rect: QRectF) -> "PaintState":
        transform = painter.deviceTransform()
        device = painter.device()
        ratio = device.devicePixelRatio()
        clip = transform.map(painter.clipPath()) if painter.hasClipping() else None
        return cls(rect, transform, painter.opacity(), clip,
                   QSize(round(device.width() * ratio), round(device.height() * ratio)))

    @property
    def device_rect(self) -> QRectF:
        """Bounding box of the quad in device pixels."""
        return self.transform.mapRect(self.rect)

    @property
    def scale(self) -> float:
        """Device pixels per painter unit, to render textures at on-screen resolution."""
        mapped = self.device_rect
        w, h = max(1e-6, self.rect.width()), max(1e-6, self.rect.height())
        return max(0.01, mapped.width() / w, mapped.height() / h)

    def visible(self) -> bool:
        """False when the quad is empty, fully transparent, outside the target or clipped away."""
        box = self.device_rect.intersected(QRectF(0, 0, self.target.width(), self.target.height()))
        if self.clip is not None:
            box = box.intersected(self.clip.boundingRect())
        return self.opacity > 0.0 and box.width() >= 0.5 and box.height() >= 0.5


# ----------------------------------------------------------------------
# Offscreen targets
# ----------------------------------------------------------------------
def _bind_previous() -> None:
    if _bound:
        _bound[-1].bind()
    else:
        QOpenGLFramebufferObject.bindDefault()


class TileTarget:
    """
    An offscreen framebuffer to draw into, so a shader can sample the result
    as a texture. Reallocated when the needed size changes. Release with
    release() (deferred delete).
    """

    def __init__(self):
        self._fbo: Optional[QOpenGLFramebufferObject] = None
        self._content: Optional[tuple] = None  # (key, rect size) of what render() last drew, if keyed

    @property
    def texture_id(self) -> int:
        return self._fbo.texture() if self._fbo is not None else 0

    @property
    def size(self) -> QSize:
        return self._fbo.size() if self._fbo is not None else QSize()

    @contextlib.contextmanager
    def bound(self, gl: QOpenGLFunctions, size: QSize) -> Iterator[None]:
        """Bind (and clear) this target at `size` for drawing; rebinds the previous target afterwards."""
        size = QSize(min(MAX_TARGET_SIZE, max(1, size.width())), min(MAX_TARGET_SIZE, max(1, size.height())))
        if self._fbo is None or self._fbo.size() != size:
            self._content = None
            defer_delete(self._fbo)
            # RGBA8 colour texture. QPainter's GL engine needs a stencil buffer to fill
            # anything but rectangles (paths, polygons, ellipses); without one it fills bounding boxes.
            self._fbo = QOpenGLFramebufferObject(size, QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        self._fbo.bind()
        _bound.append(self._fbo)
        try:
            gl.glViewport(0, 0, size.width(), size.height())
            gl.glClearColor(0.0, 0.0, 0.0, 0.0)
            gl.glClear(GL_COLOR_BUFFER_BIT)
            yield
        finally:
            _bound.pop()
            _bind_previous()

    def render(self, gl: QOpenGLFunctions, draw: Callable[[QPainter], None], rect: QRectF, scale: float,
               key: object = None) -> None:
        """
        Run `draw(painter)` into this target. `rect` is the area to capture,
        in the coordinates `draw` paints in, and `scale` the device pixels per
        unit, so the texture has the on-screen resolution. Call inside
        native_painting(). `draw` may itself use shaders (targets nest).

        `key` identifies what `draw` paints (see Tile.content_key): when it
        and the size match the last render, the texture is kept as it is.
        None always redraws.
        """
        # Steps of 1/8, so a zoom or spin (a new scale every frame) doesn't reallocate every frame
        scale = math.ceil(scale * SCALE_STEPS - 1e-3) / SCALE_STEPS
        size = QSize(round(rect.width() * scale), round(rect.height() * scale))
        content = None if key is None else (key, size.width(), size.height(), rect.width(), rect.height())
        if content is not None and content == self._content and self._fbo is not None:
            return
        self._content = None
        with self.bound(gl, size):
            size = self._fbo.size()  # after capping
            device = QOpenGLPaintDevice(size)
            painter = QPainter(device)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.scale(size.width() / rect.width(), size.height() / rect.height())
            painter.translate(-rect.x(), -rect.y())
            draw(painter)
            painter.end()
        self._content = content

    def offscreen_state(self, rect: QRectF) -> PaintState:
        """A PaintState that fills this whole target with `rect` (after render(..., rect, ...))."""
        size = self.size
        transform = QTransform().scale(size.width() / rect.width(), size.height() / rect.height()) \
            .translate(-rect.x(), -rect.y())
        return PaintState(rect, transform, 1.0, None, size)

    def release(self) -> None:
        defer_delete(self._fbo)
        self._fbo = None
        self._content = None


def float_texture(values: np.ndarray) -> QOpenGLTexture:
    """A single-channel 32-bit float texture of a (h, w) array, row 0 at the top, linearly filtered."""
    values = np.ascontiguousarray(values, dtype=np.float32)
    h, w = values.shape
    texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
    texture.setFormat(QOpenGLTexture.TextureFormat.R32F)
    texture.setSize(w, h)
    texture.setMipLevels(1)
    texture.allocateStorage(QOpenGLTexture.PixelFormat.Red, QOpenGLTexture.PixelType.Float32)
    texture.setData(QOpenGLTexture.PixelFormat.Red, QOpenGLTexture.PixelType.Float32, values.tobytes())
    texture.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
    texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
    return texture


# ----------------------------------------------------------------------
# Drawing a shader quad
# ----------------------------------------------------------------------
def _clip_coverage(gl: QOpenGLFunctions, state: PaintState) -> Tuple[int, QRectF]:
    """Render the clip path into the shared clip target; returns (texture id, its box in device px)."""
    box = state.device_rect.intersected(state.clip.boundingRect()) \
        .intersected(QRectF(0, 0, state.target.width(), state.target.height()))
    box = box.toAlignedRect()
    # Same origin, size rounded up: the extra area is simply outside the clip (coverage 0)
    step = CLIP_GRANULARITY
    box = QRectF(box.x(), box.y(), max(1, -(-box.width() // step)) * step, max(1, -(-box.height() // step)) * step)
    context_state = _state()
    if context_state.clip_target is None:
        context_state.clip_target = TileTarget()
    clip = state.clip

    def fill(painter: QPainter) -> None:
        painter.fillPath(clip, QColor("white"))

    context_state.clip_target.render(gl, fill, box, 1.0)
    return context_state.clip_target.texture_id, box


def draw_quad(gl: QOpenGLFunctions, program: QOpenGLShaderProgram, state: PaintState,
              textures: Tuple[int, ...] = (),
              set_uniforms: Optional[Callable[[QOpenGLShaderProgram], None]] = None) -> None:
    """
    Run `program` over `state.rect` on the currently bound target, blending
    its premultiplied output over what's there, with the painter's
    transform, opacity and clip from `state`. `textures` are bound to units
    0, 1, ... in order, and `set_uniforms(program)` sets the shader's own
    uniforms once it is bound. Call inside native_painting().
    """
    # Before binding the program: rendering the clip runs a QPainter, which binds its own
    clip_texture, clip_box = (0, QRectF()) if state.clip is None else _clip_coverage(gl, state)

    program.bind()
    if set_uniforms is not None:
        set_uniforms(program)
    rect, target = state.rect, state.target
    set_uniform(program, "rect", rect.x(), rect.y(), rect.width(), rect.height())
    location = _location(program, "transform")
    if location >= 0:
        program.setUniformValue(location, state.transform)  # QTransform -> mat3, column-major like GLSL
    set_uniform(program, "target", target.width(), target.height())
    set_uniform(program, "opacity", state.opacity)
    set_sampler(program, "clipped", 1 if state.clip is not None else 0)
    set_sampler(program, "clip_mask", CLIP_UNIT)
    set_uniform(program, "clip_box", clip_box.x(), clip_box.y(), max(1.0, clip_box.width()),
                max(1.0, clip_box.height()))

    context_state = _state()
    if context_state.vao is None:
        context_state.vao = QOpenGLVertexArrayObject()
        context_state.vao.create()  # core profiles need a VAO bound to draw, even with no attributes

    gl.glViewport(0, 0, target.width(), target.height())
    for flag in (GL_SCISSOR_TEST, GL_STENCIL_TEST, GL_DEPTH_TEST):
        gl.glDisable(flag)
    gl.glEnable(GL_BLEND)
    gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
    units = list(enumerate(textures)) + ([(CLIP_UNIT, clip_texture)] if clip_texture else [])
    for unit, texture_id in units:
        gl.glActiveTexture(GL_TEXTURE0 + unit)
        gl.glBindTexture(GL_TEXTURE_2D, texture_id)

    context_state.vao.bind()
    gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
    context_state.vao.release()

    for unit, _ in reversed(units):
        gl.glActiveTexture(GL_TEXTURE0 + unit)
        gl.glBindTexture(GL_TEXTURE_2D, 0)
    program.release()
