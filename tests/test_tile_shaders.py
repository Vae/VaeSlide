"""
Tile shaders (src/TileShaders.py): settings, assignment, animation clocks,
and on OpenGL, what they draw, including inside other transitions.

The GL tests skip under the default offscreen platform; run them with
    $env:QT_QPA_PLATFORM='windows'; .venv\\Scripts\\python -m pytest tests/test_tile_shaders.py
"""

import random

import numpy as np
import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor

import helpers
from src import GLSupport, TileShaders, Transitions
from src.TileShaders import TILE_SHADERS, HueRotateShader, WaterDropShader, shaders_config
from src.Transitions import MaskTransition, SpinTransition, StarSwipeTransition, ZoomTransition
from src.VaeScroll import opengl_available
from helpers import make_strip, meta, mismatch, pixels

W, H = 160, 90
X, Y = 20, 5      # the tile sits away from the origin, with a translated painter, like in a layer
CANVAS = (W + 2 * X, H + 2 * Y)


def all_on(use_settings, **overrides):
    """Every shader enabled with chance 1, plus per-shader overrides, e.g. water_drop={"seconds": 1}."""
    section = {"enabled": True}
    for name in TILE_SHADERS:
        section[name] = {"enabled": True, "chance": 1.0, **overrides.get(name.replace(" ", "_"), {})}
    use_settings({"tile shaders": section})


@pytest.fixture
def quad_type(tile_type):
    """Tiles with four differently coloured quadrants, so flips, offsets and hue shifts show."""
    Quads = tile_type("quads")

    def draw(self, painter):
        a, b, c, d = getattr(self, "colors", [QColor(n) for n in ("red", "green", "blue", "yellow")])
        x, w, h = self.x, self.width / 2, self.height / 2
        painter.fillRect(QRectF(x, 0, w, h), a)
        painter.fillRect(QRectF(x + w, 0, w, h), b)
        painter.fillRect(QRectF(x, h, w, h), c)
        painter.fillRect(QRectF(x + w, h, w, h), d)

    Quads.draw = draw
    return Quads


def quads(quad_type, name="q"):
    return quad_type(meta(name, W, H), float(X), H, None)


def hue(degrees: float) -> HueRotateShader:
    shader = HueRotateShader(shaders_config().shaders["hue rotate"], random.Random(0))
    shader.start_degrees, shader.speed, shader.degrees = degrees, 0.0, degrees
    return shader


def drop(t: float) -> WaterDropShader:
    shader = WaterDropShader(shaders_config().shaders["water drop"], random.Random(0))
    shader.centre, shader.started, shader.t = (0.5, 0.5), 0.0, t
    return shader


# ----------------------------------------------------------------------
# Settings and assignment
# ----------------------------------------------------------------------
def test_registry():
    assert set(TILE_SHADERS) == {"hue rotate", "water drop"}
    for name, cls in TILE_SHADERS.items():
        assert cls.NAME == name


def test_config_validation(use_settings):
    use_settings({"tile shaders": {
        "hue rotate": {"chance": 7, "degrees per second": [-20, 10]},
        "water drop": {"chance": -1, "seconds": 0, "strength": 5, "wavelength": 0},
    }})
    cfg = shaders_config()
    assert cfg.enabled
    hue_cfg, drop_cfg = cfg.shaders["hue rotate"], cfg.shaders["water drop"]
    assert hue_cfg.chance == 1.0 and hue_cfg.degrees_per_second == (0.0, 10.0)
    assert drop_cfg.chance == 0.0 and drop_cfg.seconds == 0.1
    assert drop_cfg.strength == 0.2 and drop_cfg.wavelength == 0.005


def test_assign_rolls_each_enabled_shader(use_settings, quad_type):
    all_on(use_settings)
    tile = quads(quad_type)
    TileShaders.assign(tile, random.Random(1))
    assert [type(s) for s in tile.shaders] == [HueRotateShader, WaterDropShader]


@pytest.mark.parametrize("section", [
    {"enabled": False, "hue rotate": {"chance": 1.0}, "water drop": {"chance": 1.0}},   # master switch
    {"hue rotate": {"enabled": False, "chance": 1.0}, "water drop": {"enabled": False, "chance": 1.0}},
    {"hue rotate": {"chance": 0.0}, "water drop": {"chance": 0.0}},
])
def test_shaders_can_be_turned_off(use_settings, quad_type, section):
    use_settings({"tile shaders": section})
    tile = quads(quad_type)
    for seed in range(20):
        TileShaders.assign(tile, random.Random(seed))
    assert tile.shaders == []


def test_chance_is_respected(use_settings, quad_type):
    use_settings({"tile shaders": {"hue rotate": {"chance": 0.25}, "water drop": {"enabled": False}}})
    rng = random.Random(3)
    hits = 0
    for _ in range(2000):
        tile = quads(quad_type)
        TileShaders.assign(tile, rng)
        hits += len(tile.shaders)
    assert hits / 2000 == pytest.approx(0.25, abs=0.03)


def test_strip_assigns_and_ticks_shaders(use_settings, quad_type, monkeypatch):
    all_on(use_settings, water_drop={"seconds": 2.0})
    import src.HorizontalStrip as strip_module
    clock = [10.0]
    monkeypatch.setattr(strip_module, "time", type("T", (), {"monotonic": staticmethod(lambda: clock[0])}))
    strip = make_strip([meta(f"m{i}", type="quads") for i in range(3)], width=500, height=100)
    strip.scroll(0.0)
    on_screen = [t for t in strip.tiles if 0 <= t.x + t.width / 2 <= 500]
    off_screen = [t for t in strip.tiles if t not in on_screen]
    assert on_screen and off_screen  # the leading-edge buffer holds tiles beyond the screen
    for tile in strip.tiles:
        assert [s.NAME for s in tile.shaders] == ["hue rotate", "water drop"]
    assert all(t.shaders[1].active() for t in on_screen)
    assert not any(t.shaders[1].active() for t in off_screen)  # drops wait until the tile appears
    clock[0] = 13.0
    strip.scroll(0.0)
    assert not any(t.shaders[1].active() for t in on_screen)   # finished after 2 s


def test_discarded_tiles_release_their_shaders(use_settings, quad_type):
    all_on(use_settings)
    a, b = quads(quad_type, "a"), quads(quad_type, "b")
    released = []
    for tile in (a, b):
        TileShaders.assign(tile, random.Random(0))
        for shader in tile.shaders:
            shader.release = lambda tile=tile: released.append(tile)
    Transitions.begin(b, a, now=0.0)
    Transitions.release(b)  # ends the transition (releasing a), then releases b
    assert released.count(a) == 2 and released.count(b) == 2
    assert a.removed_called and b.removed_called


# ----------------------------------------------------------------------
# Animation clocks
# ----------------------------------------------------------------------
def test_hue_rotates_over_time(use_settings, quad_type):
    use_settings({"tile shaders": {"hue rotate": {"degrees per second": [30, 30]}}})
    shader = HueRotateShader(shaders_config().shaders["hue rotate"], random.Random(4))
    tile = quads(quad_type)
    shader.tick(tile, 100.0, False)
    start = shader.degrees
    shader.tick(tile, 102.0, False)
    assert abs(shader.speed) == 30.0
    assert (shader.degrees - start) % 360 == pytest.approx(60.0 if shader.speed > 0 else 300.0)


def test_hue_speed_zero_is_a_fixed_shift(use_settings, quad_type):
    use_settings({"tile shaders": {"hue rotate": {"degrees per second": 0}}})
    shader = HueRotateShader(shaders_config().shaders["hue rotate"], random.Random(5))
    tile = quads(quad_type)
    shader.tick(tile, 0.0, True)
    first = shader.degrees
    shader.tick(tile, 50.0, True)
    assert shader.degrees == first == shader.start_degrees


def test_water_drop_waits_for_the_tile_to_appear_and_load(use_settings, quad_type):
    use_settings({"tile shaders": {"water drop": {"seconds": 2.0}}})
    shader = WaterDropShader(shaders_config().shaders["water drop"], random.Random(0))
    tile = quads(quad_type)
    tile.ready = False
    shader.tick(tile, 1.0, True)
    assert not shader.active()           # not loaded yet
    tile.ready = True
    shader.tick(tile, 2.0, False)
    assert not shader.active()           # centre not on screen yet
    shader.tick(tile, 3.0, True)
    assert shader.active() and shader.t == 0.0
    shader.tick(tile, 4.0, True)
    assert shader.t == pytest.approx(0.5)
    shader.tick(tile, 5.0, True)
    assert not shader.active() and shader.done
    shader.tick(tile, 9.0, False)        # never restarts
    assert not shader.active()


# ----------------------------------------------------------------------
# Drawing without OpenGL
# ----------------------------------------------------------------------
def test_without_gl_tiles_draw_plainly(qapp, use_settings, quad_type):
    all_on(use_settings)
    shaded, plain = quads(quad_type), quads(quad_type)
    shaded.shaders = [hue(120.0), drop(0.5)]
    a = helpers.render_raster(shaded.paint, CANVAS, (0, Y))
    b = helpers.render_raster(plain.draw, CANVAS, (0, Y))
    assert mismatch(a, b, tolerance=0) == 0.0


# ----------------------------------------------------------------------
# Drawing with OpenGL
# ----------------------------------------------------------------------
@pytest.fixture
def gl(qapp):
    if not opengl_available():
        pytest.skip("no OpenGL context on this platform (e.g. QT_QPA_PLATFORM=offscreen)")


def gl_image(paint):
    return helpers.render_gl(paint, CANVAS, (0, Y))


def colour(image, x, y) -> tuple:
    c = image.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def close(a: tuple, b: tuple, tolerance=6) -> bool:
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


# Pixel centres of the four quadrants, in canvas coordinates
QUADRANTS = [(X + W // 4, Y + H // 4), (X + 3 * W // 4, Y + H // 4),
             (X + W // 4, Y + 3 * H // 4), (X + 3 * W // 4, Y + 3 * H // 4)]


def test_hue_rotation_turns_the_colour_wheel(gl, use_settings, quad_type):
    tile = quads(quad_type)
    tile.shaders = [hue(120.0)]
    image = gl_image(tile.paint)
    # Rotating by 120 degrees about the grey axis maps red -> green -> blue -> red
    assert close(colour(image, *QUADRANTS[0]), (0, 255, 0))
    assert close(colour(image, *QUADRANTS[2]), (255, 0, 0))
    assert close(colour(image, *QUADRANTS[3]), (0, 255, 255))   # yellow -> cyan


def test_zero_hue_and_finished_drop_match_the_plain_tile(gl, use_settings, quad_type):
    shaded, plain = quads(quad_type), quads(quad_type)
    shaded.shaders = [hue(0.0), drop(1.0)]  # drop at t = 1: no waves left
    assert mismatch(gl_image(shaded.paint), gl_image(plain.draw)) == 0.0


def test_shaders_chain_in_order(gl, use_settings, quad_type):
    tile = quads(quad_type)
    tile.shaders = [hue(120.0), hue(120.0)]  # 240 degrees: red -> blue
    image = gl_image(tile.paint)
    assert close(colour(image, *QUADRANTS[0]), (0, 0, 255))


def test_water_drop_ripples_inside_the_tile(gl, use_settings, quad_type):
    shaded, plain = quads(quad_type), quads(quad_type)
    shaded.shaders = [drop(0.3)]
    rippled, flat = gl_image(shaded.paint), gl_image(plain.draw)
    assert mismatch(rippled, flat) > 0.01  # the quadrant edges are bent
    outside = pixels(rippled).copy()
    outside[Y:Y + H, X:X + W] = 0
    assert outside[..., :3].max() == 0


def test_inactive_shaders_cost_nothing(gl, use_settings, quad_type, monkeypatch):
    tile = quads(quad_type)
    waiting = drop(0.0)
    waiting.started = None  # not on screen yet
    tile.shaders = [waiting]
    monkeypatch.setattr(TileShaders, "_draw_gl", lambda *a: pytest.fail("ran a shader pass"))
    gl_image(tile.paint)


def test_master_switch_off_draws_plainly(gl, use_settings, quad_type):
    use_settings({"tile shaders": {"enabled": False}})
    shaded, plain = quads(quad_type), quads(quad_type)
    shaded.shaders = [hue(120.0)]
    assert mismatch(gl_image(shaded.paint), gl_image(plain.draw)) == 0.0


# A shaded tile must follow whatever the painter does in a QPainter transition:
# transforms (zoom, perspective spin), opacity (fade, zoom) and clips (star, zoom, spin).
# With a 0-degree hue shader the shaded tile must look like the plain one.
@pytest.mark.parametrize("make, progress", [
    (lambda: ZoomTransition(), 0.4),
    (lambda: StarSwipeTransition(points=5, rng=random.Random(2)), 0.5),
    (lambda: SpinTransition(rng=random.Random(0)), 0.3),
    (lambda: SpinTransition(rng=random.Random(0)), 0.7),
])
def test_shaded_tiles_follow_painter_transitions(gl, use_settings, quad_type, make, progress):
    def draw(shaded: bool):
        old, new = quads(quad_type, "old"), quads(quad_type, "new")
        new.colors = [QColor(n) for n in ("magenta", "cyan", "white", "darkGreen")]
        if shaded:
            old.shaders, new.shaders = [hue(0.0)], [hue(0.0)]
        return gl_image(lambda p: make().draw(p, old, new, progress))

    # Edges may differ by a pixel (texture sampling vs. QPainter's own rasterising)
    assert mismatch(draw(True), draw(False), tolerance=24) < 0.03


def test_tiles_that_draw_paths_render_correctly_offscreen(gl, use_settings, tile_type):
    """QPainter's GL engine fills paths with the stencil buffer, so offscreen targets need one."""
    Blob = tile_type("blob")

    def draw(self, painter):
        painter.setBrush(QColor("red"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(self.x + 10, 10, self.width - 20, self.height - 20))

    Blob.draw = draw
    shaded, plain = Blob(meta("s", W, H), float(X), H, None), Blob(meta("p", W, H), float(X), H, None)
    shaded.shaders = [hue(0.0)]
    image = gl_image(shaded.paint)
    assert colour(image, X + 2, Y + 2) == (0, 0, 0)  # outside the ellipse, inside its bounding box
    assert mismatch(image, gl_image(plain.draw), tolerance=24) < 0.02


def test_shaded_tile_fades_with_painter_opacity(gl, use_settings, quad_type):
    tile = quads(quad_type)
    tile.shaders = [hue(120.0)]

    def paint(painter):
        painter.setOpacity(0.5)
        tile.paint(painter)

    r, g, b = colour(gl_image(paint), *QUADRANTS[0])
    assert r < 6 and abs(g - 128) <= 6 and b < 6  # green at half strength over black


def test_shaded_tile_respects_a_clip(gl, use_settings, quad_type):
    tile = quads(quad_type)
    tile.shaders = [hue(120.0)]

    def paint(painter):
        painter.setClipRect(QRectF(X, 0, W / 2, H))  # left half only
        tile.paint(painter)

    image = pixels(gl_image(paint))
    assert image[Y:Y + H, X + W // 2 + 2:X + W, :3].max() == 0
    assert image[Y + 10, X + 10, 1] > 200  # green inside (BGRA order: index 1 is green)


def test_shaded_tile_inside_a_shader_transition(gl, use_settings, quad_type):
    """Nested offscreen targets: the old tile's hue shader runs inside the mask transition's texture."""
    old, new = quads(quad_type, "old"), quads(quad_type, "new")
    old.shaders = [hue(120.0)]
    effect = MaskTransition(shape="linear", softness=0.0, rng=random.Random(0))
    image = gl_image(lambda p: effect.draw(p, old, new, 0.0))
    assert close(colour(image, *QUADRANTS[0]), (0, 255, 0))
    effect.release()


def test_transition_shaders_can_be_turned_off(gl, use_settings, quad_type, monkeypatch):
    use_settings({"tile transition": {"use shaders": False}})
    old, new = quads(quad_type, "old"), quads(quad_type, "new")
    effect = MaskTransition(shape="linear", rng=random.Random(0))
    used = []
    monkeypatch.setattr(effect, "draw_fallback", lambda *a: used.append(True))
    gl_image(lambda p: effect.draw(p, old, new, 0.5))
    assert used == [True]


def test_unchanged_content_is_not_redrawn(gl, use_settings, quad_type):
    tile = quads(quad_type)
    tile.shaders = [hue(120.0)]
    key, draws = ["a"], []
    plain_draw = tile.draw
    tile.content_key = lambda: key[0]
    tile.draw = lambda painter: (draws.append(1), plain_draw(painter))
    canvas = helpers.GLCanvas(tile.paint, CANVAS, (0, Y))
    try:
        first = canvas.grabFramebuffer()
        second = canvas.grabFramebuffer()  # same context, same key: the texture is reused
        assert len(draws) == 1 and mismatch(first, second) == 0.0
        key[0] = "b"
        canvas.grabFramebuffer()
        assert len(draws) == 2
        tile.content_key = lambda: None  # unknown: redrawn every frame
        canvas.grabFramebuffer()
        canvas.grabFramebuffer()
        assert len(draws) == 4
    finally:
        canvas.close()


def test_finished_drop_frees_its_texture(gl, use_settings, quad_type):
    use_settings({"tile shaders": {"water drop": {"seconds": 1.0}}})
    tile = quads(quad_type)
    shader = WaterDropShader(shaders_config().shaders["water drop"], random.Random(0))
    tile.shaders = [shader]
    shader.tick(tile, 0.0, True)
    shader.tick(tile, 0.5, True)
    gl_image(tile.paint)
    before = GLSupport.pending_garbage()
    shader.tick(tile, 1.5, True)  # done
    assert GLSupport.pending_garbage() == before + 1
