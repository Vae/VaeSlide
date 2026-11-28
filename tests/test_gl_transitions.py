"""
Shader transitions on the OpenGL renderer, compared against their QPainter fallback.

These need a real OpenGL context, so they skip under the default offscreen
platform. Run them on the desktop with:

    $env:QT_QPA_PLATFORM='windows'; .venv\\Scripts\\python -m pytest tests/test_gl_transitions.py
"""

import random

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

import helpers
from src import GLSupport, Transitions
from src.Transitions import MaskTransition, ShaderTransition
from src.VaeScroll import opengl_available
from helpers import meta, mismatch, pixels

W, H = 160, 90
TILE_X, TILE_Y = 20, 5  # the tile sits away from the origin, with a translated painter, like in a layer


@pytest.fixture
def gl(qapp):
    if not opengl_available():
        pytest.skip("no OpenGL context on this platform (e.g. QT_QPA_PLATFORM=offscreen)")


@pytest.fixture
def quad_type(tile_type):
    """Tiles with four differently coloured quadrants, so flips and offsets show."""
    Quads = tile_type("quads")

    def draw(self, painter):
        a, b, c, d = self.colors
        x, w, h = self.x, self.width / 2, self.height / 2
        painter.fillRect(QRectF(x, 0, w, h), a)
        painter.fillRect(QRectF(x + w, 0, w, h), b)
        painter.fillRect(QRectF(x, h, w, h), c)
        painter.fillRect(QRectF(x + w, h, w, h), d)

    Quads.draw = draw
    return Quads


def pair(quad_type):
    old = quad_type(meta("old", W, H), float(TILE_X), H, None)
    new = quad_type(meta("new", W, H), float(TILE_X), H, None)
    old.colors = [QColor(c) for c in ("red", "green", "blue", "yellow")]
    new.colors = [QColor(c) for c in ("magenta", "cyan", "white", "darkGreen")]
    return old, new


CANVAS = (W + 2 * TILE_X, H + 2 * TILE_Y)


def render_gl(paint) -> QImage:
    return helpers.render_gl(paint, CANVAS, (0, TILE_Y))


def render_raster(paint) -> QImage:
    return helpers.render_raster(paint, CANVAS, (0, TILE_Y))


@pytest.mark.parametrize("progress", [0.0, 0.3, 0.6, 1.0])
@pytest.mark.parametrize("softness", [0.0, 0.25])
def test_mask_shader_matches_the_fallback(gl, quad_type, progress, softness):
    old, new = pair(quad_type)

    def effect():
        return MaskTransition(shape="linear", softness=softness, rng=random.Random(3))

    gpu, cpu = effect(), effect()
    gl_image = render_gl(lambda p: gpu.draw(p, old, new, progress))
    raster_image = render_raster(lambda p: cpu.draw_fallback(p, old, new, progress))
    # Only the mask's edge may differ slightly (texture filtering vs. QImage scaling)
    assert mismatch(gl_image, raster_image) < 0.02
    gpu.release()


def test_shader_is_used_and_stays_inside_the_tile(gl, quad_type, monkeypatch):
    old, new = pair(quad_type)
    effect = MaskTransition(shape="noise", softness=0.1, rng=random.Random(1))
    monkeypatch.setattr(effect, "draw_fallback", lambda *a: pytest.fail("fell back to QPainter"))
    image = render_gl(lambda p: effect.draw(p, old, new, 0.5))
    border = pixels(image).copy()
    border[TILE_Y:TILE_Y + H, TILE_X:TILE_X + W] = 0
    assert border[..., :3].max() == 0  # black outside the tile
    effect.release()


def test_a_broken_shader_falls_back(gl, quad_type, caplog, tmp_path):
    source = tmp_path / "broken.glsl"
    source.write_text("vec4 shade(vec2 p) { this is not glsl }\n")

    class Broken(ShaderTransition):
        NAME = "broken"
        SHADER = str(source)

    old, new = pair(quad_type)
    fell_back = []
    effect = Broken()
    effect.draw_fallback = lambda *a: fell_back.append(True)
    render_gl(lambda p: effect.draw(p, old, new, 0.5))
    assert fell_back and "failed to build" in caplog.text


def test_release_defers_gl_deletes(gl, quad_type):
    old, new = pair(quad_type)
    effect = MaskTransition(shape="radial", rng=random.Random(2))
    render_gl(lambda p: effect.draw(p, old, new, 0.5))
    GLSupport.collect_garbage()  # nothing to do here: everything is still in use
    effect.release()
    assert GLSupport.pending_garbage() == 3  # two tile framebuffers and the mask texture
    render_gl(lambda p: None)  # the next paintGL deletes them with the context current
    assert GLSupport.pending_garbage() == 0


def test_finish_releases_the_effect(quad_type):
    old, new = pair(quad_type)
    released = []
    effect = MaskTransition(shape="linear")
    effect.release = lambda: released.append(True)
    new.transition = Transitions.ActiveTransition(effect, old, 0.0, 1.0)
    Transitions.finish(new)
    assert released == [True] and old.removed_called
