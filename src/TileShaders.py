"""
Tile shaders: GPU effects drawn over a tile, such as a hue rotation or
ripples from a water drop.

Each new tile rolls every enabled shader's "chance" (HorizontalStrip calls
assign()); a tile can get several, which run in order, each one reading
the previous one's output. Tiles draw through Tile.paint(), which comes
here: the tile is drawn into a texture with its own draw(), then each
shader runs over it. The last pass goes straight onto the screen (or into
a transition's texture), following the painter's transform, opacity and
clip, so shaded tiles look right inside every transition.

Shaders only run on the OpenGL renderer. Everywhere else (the raster
renderer, the offscreen test platform, a shader that fails to compile, or
"tile shaders" -> "enabled": false) the tile is drawn plainly.

To add a shader: subclass TileShader, set NAME, write its GLSL file in
src/shaders/tile/ and name it in SHADER (it defines `vec4 shade(vec2 uv)`
returning a premultiplied colour; see tile/inputs.glsl), add a config
dataclass with `enabled` and `chance` and a read_config() for it, override
tick()/active()/set_uniforms() as needed, and add the class to
TILE_SHADERS. Its settings go in "tile shaders" -> NAME.
"""

import logging
import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Tuple, Type

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from src import GLSupport
from src.Settings import SettingsSection, get_settings, section_config

if TYPE_CHECKING:
    from src.Tile import Tile

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "tile shaders"  # this module's section in settings.json


def _chance(section: SettingsSection) -> float:
    return min(1.0, max(0.0, section.get_number("chance")))


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------
class TileShader:
    """
    One effect on one tile. Instances are made per tile, so they may keep
    random per-tile state and animation clocks.

    SHADER is its GLSL file under src/shaders (e.g. "tile/water_drop.glsl"),
    which defines `vec4 shade(vec2 uv)`. It comes after tile/inputs.glsl:

        vec2 uv;                // 0..1 across the tile, (0, 0) at the top left
        uniform sampler2D tile; // the tile so far (unit 0); sample with tile_uv(uv)
        uniform vec2 size;      // the texture's size in device pixels
    """
    NAME: ClassVar[str] = ""
    SHADER: ClassVar[str] = ""
    INPUTS: ClassVar[str] = "tile/inputs.glsl"

    @classmethod
    def program_spec(cls) -> GLSupport.ProgramSpec:
        return f"tile shader {cls.NAME}", (cls.INPUTS, cls.SHADER)

    def __init__(self, config: Any, rng: random.Random):
        self.config = config
        self._input = GLSupport.TileTarget()  # what this shader reads: the tile, or the previous shader's output

    @classmethod
    def read_config(cls, section: SettingsSection) -> Any:
        """Validated settings from this shader's section; must have `enabled` and `chance`."""
        raise NotImplementedError

    def tick(self, tile: "Tile", now: float, centre_on_screen: bool) -> None:
        """Once per frame. `now` is time.monotonic(); `centre_on_screen` is True once the tile's middle is in view."""

    def active(self) -> bool:
        """False while the shader has nothing to do (not started yet, or finished): the pass is skipped."""
        return True

    def set_uniforms(self, program, tile: "Tile") -> None:
        """Set this shader's own uniforms (the program is bound)."""

    def release(self) -> None:
        """The tile is gone: free the GL resources (deferred)."""
        self._input.release()


# ----------------------------------------------------------------------
# Hue rotation
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class HueRotateConfig:
    enabled: bool
    chance: float                            # 0..1 per new tile
    degrees_per_second: Tuple[float, float]  # (min, max) speed, >= 0; direction is random


class HueRotateShader(TileShader):
    """Turns every colour's hue around the colour wheel, slowly, or by a fixed random amount at speed 0."""
    NAME = "hue rotate"
    SHADER = "tile/hue_rotate.glsl"

    def __init__(self, config: HueRotateConfig, rng: random.Random):
        super().__init__(config, rng)
        self.start_degrees = rng.uniform(0.0, 360.0)
        self.speed = rng.uniform(*config.degrees_per_second) * rng.choice((-1.0, 1.0))
        self.degrees = self.start_degrees
        self._born: Optional[float] = None

    @classmethod
    def read_config(cls, section: SettingsSection) -> HueRotateConfig:
        lo, hi = section.get_range("degrees per second")
        return HueRotateConfig(bool(section.get("enabled")), _chance(section), (max(0.0, lo), max(0.0, hi)))

    def tick(self, tile: "Tile", now: float, centre_on_screen: bool) -> None:
        if self._born is None:
            self._born = now
        self.degrees = (self.start_degrees + self.speed * (now - self._born)) % 360.0

    def set_uniforms(self, program, tile: "Tile") -> None:
        GLSupport.set_uniform(program, "angle", math.radians(self.degrees))


# ----------------------------------------------------------------------
# Water drop
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class WaterDropConfig:
    enabled: bool
    chance: float      # 0..1 per new tile
    seconds: float     # > 0
    strength: float    # max displacement, fraction of the tile height
    wavelength: float  # crest to crest, fraction of the tile height


class WaterDropShader(TileShader):
    """
    A drop lands on the tile when it appears (once its centre is on screen
    and its image has loaded): rings of ripples spread out from a random
    point, bending the image and catching the light, and die away.
    """
    NAME = "water drop"
    SHADER = "tile/water_drop.glsl"

    def __init__(self, config: WaterDropConfig, rng: random.Random):
        super().__init__(config, rng)
        self.centre = (rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7))
        self.started: Optional[float] = None
        self.t = 0.0
        self.done = False

    @classmethod
    def read_config(cls, section: SettingsSection) -> WaterDropConfig:
        return WaterDropConfig(
            enabled=bool(section.get("enabled")),
            chance=_chance(section),
            seconds=max(0.1, section.get_number("seconds")),
            strength=min(0.2, max(0.0, section.get_number("strength"))),
            wavelength=min(1.0, max(0.005, section.get_number("wavelength"))),
        )

    def tick(self, tile: "Tile", now: float, centre_on_screen: bool) -> None:
        if self.done:
            return
        if self.started is None:
            if centre_on_screen and tile.is_ready():
                self.started = now
            return
        self.t = min(1.0, (now - self.started) / self.config.seconds)
        if self.t >= 1.0:
            self.done = True
            self.release()  # nothing more to draw; free the texture now

    def active(self) -> bool:
        return self.started is not None and not self.done

    def set_uniforms(self, program, tile: "Tile") -> None:
        cfg = self.config
        GLSupport.set_uniform(program, "t", self.t)
        GLSupport.set_uniform(program, "centre", *self.centre)
        GLSupport.set_uniform(program, "strength", cfg.strength)
        GLSupport.set_uniform(program, "wavelength", cfg.wavelength)


TILE_SHADERS: Dict[str, Type[TileShader]] = {
    cls.NAME: cls for cls in (HueRotateShader, WaterDropShader)
}


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class TileShadersConfig:
    enabled: bool              # master switch
    shaders: Dict[str, Any]    # NAME -> that shader's config (has .enabled and .chance)


@section_config
def shaders_config() -> TileShadersConfig:
    """Validated "tile shaders" settings, read once."""
    section = get_settings().section(SETTINGS_SECTION)
    return TileShadersConfig(
        enabled=bool(section.get("enabled")),
        shaders={name: cls.read_config(section.section(name)) for name, cls in TILE_SHADERS.items()},
    )


# ----------------------------------------------------------------------
# Per-tile lifecycle, called by HorizontalStrip, Transitions and Tile.paint
# ----------------------------------------------------------------------
def assign(tile: "Tile", rng: Optional[random.Random] = None) -> None:
    """Give a new tile its shaders: each enabled one, with its chance."""
    cfg = shaders_config()
    if not cfg.enabled:
        return
    rng = rng or random.Random()
    for name, cls in TILE_SHADERS.items():
        shader_cfg = cfg.shaders[name]
        if shader_cfg.enabled and rng.random() < shader_cfg.chance:
            tile.shaders.append(cls(shader_cfg, rng))


def shader_programs() -> List[GLSupport.ProgramSpec]:
    """The programs of the tile shaders that are switched on, for precompiling."""
    cfg = shaders_config()
    if not cfg.enabled:
        return []
    return [cls.program_spec() for name, cls in TILE_SHADERS.items() if cfg.shaders[name].enabled]


def tick(tile: "Tile", now: float, centre_on_screen: bool) -> None:
    for shader in tile.shaders:
        shader.tick(tile, now, centre_on_screen)


def release(tile: "Tile") -> None:
    """The tile is being discarded: free its shaders' GL resources."""
    for shader in tile.shaders:
        shader.release()


def paint_key(tile: "Tile") -> object:
    """Like Tile.content_key(), but for what tile.paint() draws: None while a shader is animating it."""
    if shaders_config().enabled and any(s.active() for s in tile.shaders):
        return None
    return tile.content_key()


def draw(painter: QPainter, tile: "Tile") -> None:
    """Draw `tile` through its active shaders, or plainly when there are none or GL isn't available."""
    shaders = [s for s in tile.shaders if s.active()] if shaders_config().enabled else []
    if not shaders or not GLSupport.is_gl_painter(painter) or not _draw_gl(painter, tile, shaders):
        tile.draw(painter)


def _draw_gl(painter: QPainter, tile: "Tile", shaders: list) -> bool:
    passes = []
    for shader in shaders:
        program = GLSupport.shader_program(*shader.program_spec())
        if program is not None:
            passes.append((shader, program))
    if not passes:
        return False
    rect = QRectF(tile.x, 0.0, tile.width, tile.height)
    state = GLSupport.PaintState.of(painter, rect)
    if not state.visible():
        return True

    with GLSupport.native_painting(painter) as gl:
        passes[0][0]._input.render(gl, tile.draw, rect, state.scale, key=tile.content_key())
        for index, (shader, program) in enumerate(passes):
            source = shader._input

            def set_uniforms(p, shader=shader, source=source) -> None:
                size = source.size
                GLSupport.set_sampler(p, "tile", 0)
                GLSupport.set_uniform(p, "size", size.width(), size.height())
                shader.set_uniforms(p, tile)

            if index + 1 < len(passes):  # into the next shader's input
                with passes[index + 1][0]._input.bound(gl, source.size):
                    GLSupport.draw_quad(gl, program, source.offscreen_state(rect), (source.texture_id,),
                                        set_uniforms)
            else:  # the last pass goes where the painter is drawing
                GLSupport.draw_quad(gl, program, state, (source.texture_id,), set_uniforms)
    return True
