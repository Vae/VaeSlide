"""
Shader files under src/shaders: composing, error locations, precompiling and reloading.

The composing and log tests run anywhere; the rest need OpenGL and skip offscreen.
Run them on the desktop with:

    $env:QT_QPA_PLATFORM='windows'; .venv\\Scripts\\python -m pytest tests/test_shader_files.py
"""

import logging
import os

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

import helpers
from src import GLSupport, TileShaders, Transitions
from src.GLSupport import SOURCE_NUMBER_BASE, compose_source, shader_path
from src.Transitions import ShaderTransition, TRANSITIONS
from src.TileShaders import TILE_SHADERS
from src.VaeScroll import opengl_available
from helpers import meta, pixels

W, H = 64, 36


def all_specs():
    return ([cls.program_spec() for cls in TRANSITIONS.values() if issubclass(cls, ShaderTransition)]
            + [cls.program_spec() for cls in TILE_SHADERS.values()])


# ----------------------------------------------------------------------
# Without GL
# ----------------------------------------------------------------------
def test_every_shader_file_exists():
    files = {GLSupport.VERTEX_FILE, GLSupport.HEADER_FILE, GLSupport.MAIN_FILE}
    for _, parts in all_specs():
        files.update(parts)
    missing = [f for f in files if not os.path.isfile(shader_path(f))]
    assert not missing
    assert len(all_specs()) == 3  # mask, hue rotate, water drop


def test_compose_puts_one_version_first_and_marks_each_file(tmp_path):
    a = tmp_path / "a.glsl"
    b = tmp_path / "b.glsl"
    a.write_text("#version 330\nfloat x;\n")
    b.write_text("float y;\nfloat z;\n")
    lines = compose_source((str(a), str(b))).splitlines()
    assert lines[0] == GLSupport.GLSL_VERSION
    assert sum(line.startswith("#version") for line in lines) == 1
    # Each file's text keeps its line numbers after its #line marker
    first = lines.index(f"#line 1 {SOURCE_NUMBER_BASE}")
    assert lines[first + 2] == "float x;"  # line 2 of a.glsl
    second = lines.index(f"#line 1 {SOURCE_NUMBER_BASE + 1}")
    assert lines[second + 1:second + 3] == ["float y;", "float z;"]


@pytest.mark.parametrize("log, expected", [
    ("101(7) : error C1008: undefined variable \"foo\"", "tile/water_drop.glsl:7 : error C1008"),  # NVIDIA
    ("101:7(12): error: `foo' undeclared", "tile/water_drop.glsl:7(12): error"),                  # Mesa
    ("ERROR: 101:7: 'foo' : undeclared identifier", "tile/water_drop.glsl:7: 'foo'"),             # AMD / Intel
])
def test_error_locations_name_the_file(log, expected):
    files = ("tile/inputs.glsl", "tile/water_drop.glsl")
    assert expected in GLSupport._readable_log(log, files)


def test_unrelated_numbers_in_a_log_are_left_alone():
    files = ("tile/inputs.glsl",)
    assert GLSupport._readable_log("0(3) : error C0000: 330(2)", files) == "0(3) : error C0000: 330(2)"


def test_precompile_lists_follow_the_settings(use_settings):
    assert {name for name, _ in Transitions.shader_programs()} == {"transition mask"}
    assert {name for name, _ in TileShaders.shader_programs()} == {"tile shader hue rotate",
                                                                   "tile shader water drop"}
    use_settings({"tile transition": {"use shaders": False},
                  "tile shaders": {"water drop": {"enabled": False}}})
    assert Transitions.shader_programs() == []
    assert [name for name, _ in TileShaders.shader_programs()] == ["tile shader hue rotate"]
    use_settings({"tile transition": {"type": "fade"}, "tile shaders": {"enabled": False}})
    assert Transitions.shader_programs() == [] and TileShaders.shader_programs() == []


# ----------------------------------------------------------------------
# With GL
# ----------------------------------------------------------------------
@pytest.fixture
def gl(qapp):
    if not opengl_available():
        pytest.skip("no OpenGL context on this platform (e.g. QT_QPA_PLATFORM=offscreen)")


@pytest.fixture
def reload_now(monkeypatch):
    monkeypatch.setattr(GLSupport, "RELOAD_SECONDS", 0.0)


def solid(rgb: str) -> str:
    return f"vec4 shade(vec2 p) {{ return vec4({rgb}, 1.0); }}\n"


def write(path, text):
    """Write a shader file so its signature surely differs from the last version."""
    path.write_text(text)
    info = os.stat(path)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))


@pytest.fixture
def effect_from(tile_type, tmp_path):
    """A shader transition whose shader is a temporary file, and a canvas to draw it on repeatedly."""
    source = tmp_path / "effect.glsl"
    Plain = tile_type("plain")
    old = Plain(meta("old", W, H), 0.0, H, None)
    new = Plain(meta("new", W, H), 0.0, H, None)

    class FromFile(ShaderTransition):
        NAME = f"from file {id(source)}"
        SHADER = str(source)

    effect = FromFile()
    effect.draw_fallback = lambda *a: None  # fallback draws nothing: black

    def centre_colour(canvas) -> tuple:
        image = canvas.grabFramebuffer().convertToFormat(QImage.Format.Format_ARGB32)
        b, g, r, _ = pixels(image)[H // 2, W // 2]
        return int(r), int(g), int(b)

    canvas = helpers.GLCanvas(lambda p: effect.draw(p, old, new, 0.5), (W, H))
    yield source, canvas, centre_colour
    effect.release()
    canvas.close()
    canvas.deleteLater()


def test_compile_errors_point_at_the_file_and_line(gl, effect_from, caplog):
    source, canvas, colour = effect_from
    write(source, "// a comment\n\nvec4 shade(vec2 p) { return undefined_thing; }\n")
    with caplog.at_level(logging.ERROR):
        assert colour(canvas) == (0, 0, 0)  # fell back
    assert f"{source.as_posix()}:3".lower() in caplog.text.replace("\\", "/").lower()


def test_an_edited_file_is_rebuilt(gl, effect_from, reload_now, caplog):
    source, canvas, colour = effect_from
    write(source, solid("1.0, 0.0, 0.0"))
    assert colour(canvas) == (255, 0, 0)
    write(source, solid("0.0, 0.0, 1.0"))
    with caplog.at_level(logging.INFO):
        assert colour(canvas) == (0, 0, 255)
    assert "reloaded" in caplog.text


def test_a_failed_rebuild_keeps_the_previous_version(gl, effect_from, reload_now, caplog):
    source, canvas, colour = effect_from
    write(source, solid("0.0, 1.0, 0.0"))
    assert colour(canvas) == (0, 255, 0)
    write(source, "vec4 shade(vec2 p) { oops }\n")
    with caplog.at_level(logging.ERROR):
        assert colour(canvas) == (0, 255, 0)
        assert colour(canvas) == (0, 255, 0)
    assert caplog.text.count("keeping the previous version") == 1  # logged once, not every frame
    write(source, solid("1.0, 1.0, 1.0"))
    assert colour(canvas) == (255, 255, 255)  # fixed: picked up again


def test_unchanged_files_are_only_checked_every_reload_interval(gl, effect_from, monkeypatch):
    source, canvas, colour = effect_from
    write(source, solid("1.0, 0.0, 0.0"))
    assert colour(canvas) == (255, 0, 0)
    write(source, solid("0.0, 0.0, 1.0"))
    assert colour(canvas) == (255, 0, 0)  # within RELOAD_SECONDS: not looked at yet


def test_precompile_builds_every_shader(gl, caplog):
    built = []

    def paint(painter):
        built.append(GLSupport.precompile(all_specs()))

    with caplog.at_level(logging.INFO):
        helpers.render_gl(paint, (8, 8))
    assert built == [3] and "Compiled 3 of 3 shaders" in caplog.text
