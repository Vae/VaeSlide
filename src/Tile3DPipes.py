"""
Tile3DPipes: 3D pipes growing through a grid on a black background,
drawn with QPainter in software perspective (no OpenGL).

Pipes grow one cell at a time, turning at random or when blocked. Cylinders
are shaded trapezoids; spheres mark where a pipe starts, turns and ends.
Everything is drawn far to near (painter's algorithm), so pipes pass in
front of and behind each other. When a cycle is finished the picture is
held for a moment, then cleared and started again with new colors.

These tiles never swap. Settings live in the "tile 3d pipes" section.
"""

import logging
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPolygonF, QRadialGradient

from src.AssetScanner import AssetScanner
from src.MediaCache import MediaCache
from src.Settings import get_settings, section_config
from src.Tile import MetaTile, Tile, TileContext

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "tile 3d pipes"  # this module's section in settings.json
MAX_STEPS_PER_TICK = 50                # stops a long stall (e.g. after a hitch) from bursting
COLOR_MODES = ("per pipe", "per cycle")

Cell = Tuple[int, int, int]
Vec3 = Tuple[float, float, float]
DIRECTIONS: Tuple[Cell, ...] = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))

# Light direction in view space (x right, y down, z away from the viewer):
# from the upper left, in front of the scene
_L = (-0.45, -0.65, -0.6)
_L_LEN = math.sqrt(sum(c * c for c in _L))
LIGHT: Vec3 = (_L[0] / _L_LEN, _L[1] / _L_LEN, _L[2] / _L_LEN)
AMBIENT = 0.22
JOINT_SCALE = 1.3      # joint spheres are a bit fatter than the pipe
SEGMENT_OVERLAP = 0.03  # segments overlap by this fraction of a cell at each end (hides seams)
SLAB_DEPTH = 0.5        # depth of one cached render layer, in cells (thinner = cheaper redraws, more layers to stack)


@dataclass(frozen=True)
class PipesConfig:
    count: int
    weight: float
    size: Tuple[int, int, int]
    speed: float
    turn_chance: float
    pipe_radius: float
    pipes_per_cycle: int
    fill: float
    hold_seconds: float
    color_mode: str


@section_config
def pipes_config() -> PipesConfig:
    """Validated "tile 3d pipes" settings, read once."""
    section = get_settings().section(SETTINGS_SECTION)
    defaults = get_settings().DEFAULTS[SETTINGS_SECTION]

    size = section.get("grid size")
    try:
        gx, gy, gz = (int(v) for v in size)
        if not all(2 <= v <= 40 for v in (gx, gy, gz)):
            raise ValueError("each dimension must be 2..40")
    except (TypeError, ValueError) as e:
        logger.error("Setting \"%s.grid size\" must be [x, y, z] (2..40 each), got %r (%s). Using %r.",
                     SETTINGS_SECTION, size, e, defaults["grid size"])
        gx, gy, gz = defaults["grid size"]

    speed = section.get_number("speed")
    if speed <= 0:
        logger.error("Setting \"%s.speed\" must be > 0, got %r. Using %r.", SETTINGS_SECTION, speed, defaults["speed"])
        speed = float(defaults["speed"])

    color_mode = section.get("color mode")
    if color_mode not in COLOR_MODES:
        logger.error("Setting \"%s.color mode\" must be one of %s, got %r. Using \"per pipe\".",
                     SETTINGS_SECTION, list(COLOR_MODES), color_mode)
        color_mode = "per pipe"

    return PipesConfig(
        count=max(0, section.get_number("count", int)),
        weight=max(0.0, section.get_number("weight")),
        size=(gx, gy, gz),
        speed=speed,
        turn_chance=min(1.0, max(0.0, section.get_number("turn chance"))),
        pipe_radius=min(0.45, max(0.05, section.get_number("pipe radius"))),
        pipes_per_cycle=max(1, section.get_number("pipes per cycle", int)),
        fill=min(1.0, max(0.05, section.get_number("fill"))),
        hold_seconds=max(0.0, section.get_number("hold seconds")),
        color_mode=color_mode,
    )


def random_pipe_color(rng: random.Random) -> QColor:
    """A saturated, bright color."""
    return QColor.fromHsvF(rng.random(), rng.uniform(0.55, 1.0), rng.uniform(0.75, 1.0))


def _shade(base: QColor, diffuse: float, specular: float) -> QColor:
    """Base color lit by `diffuse` (0..1), plus a white `specular` highlight."""
    k = AMBIENT + (1.0 - AMBIENT) * diffuse
    s = 0.75 * specular
    return QColor.fromRgbF(min(1.0, base.redF() * k + s),
                           min(1.0, base.greenF() * k + s),
                           min(1.0, base.blueF() * k + s))


class Tile3DPipesScanner(AssetScanner):
    """Creates the pipes entries for the selection pool from settings (there are no files)."""

    NEEDS_PATH = False

    def scan(self, media_cache: MediaCache, path: Optional[str] = None) -> list[MetaTile]:
        cfg = pipes_config()
        gx, gy, _ = cfg.size
        meta_tiles = [
            MetaTile(weight=cfg.weight, type=Tile3DPipes.TYPE, width=gx * 1000, height=gy * 1000,
                     resource_filename=f"pipes:{i}", json_filename="")
            for i in range(cfg.count)
        ]
        logger.info("Added %d pipes tile(s) (%dx%dx%d grid).", len(meta_tiles), *cfg.size)
        return meta_tiles


class Pipe:
    """One pipe: a color, its head cell and the direction it is growing in."""

    def __init__(self, color: QColor, head: Cell, direction: Cell):
        self.color = color
        self.head = head
        self.direction = direction
        self.length = 0


class Tile3DPipes(Tile):
    """
    Grows 3D pipes in a grid, starting once the tile is first visible.
    Phases: "grow" -> "hold" -> (cleared) -> "grow" ... It never swaps.
    """

    TYPE = "3d pipes"
    SCANNER = Tile3DPipesScanner

    def __init__(self, meta_tile: MetaTile, x: float, h: int, ctx: TileContext,
                 rng: Optional[random.Random] = None):
        super().__init__(meta_tile, x, h, ctx)
        self._cfg = pipes_config()
        self._rng = rng or random.Random()
        self._size = self._cfg.size
        self._last_time: Optional[float] = None
        self._step_budget = 0.0
        self._image: Optional[QImage] = None
        self.cycles = 0
        self._reset()

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------
    def _reset(self) -> None:
        """Clear the picture and start a new cycle with new colors and a new camera angle."""
        self.occupied: Set[Cell] = set()
        # Draw list: ("pipe", a, b, color) cylinders between neighbouring cells,
        # ("ball", cell, scale, color) spheres
        self.primitives: List[tuple] = []
        # The same primitives grouped into depth slabs, one cached layer image
        # each (see _render): depth slab -> [(depth, order, prim, a, b)]
        self._slabs: Dict[int, List[tuple]] = {}
        self._layers: Dict[int, QImage] = {}
        self._new_items: Dict[int, List[tuple]] = {}  # added since the last render, per slab
        self.pipe: Optional[Pipe] = None
        self.pipes_started = 0
        self.phase = "grow"
        self._hold_until: Optional[float] = None
        self._cycle_color = random_pipe_color(self._rng)
        # A slightly different viewpoint each cycle
        self._yaw = math.radians(self._rng.uniform(-22.0, 22.0))
        self._pitch = math.radians(self._rng.uniform(-14.0, 14.0))
        self._dirty = True

    def _add(self, prim: tuple) -> None:
        """Record a primitive: ("pipe", a, b, color) or ("ball", cell, scale, color)."""
        self.primitives.append(prim)
        if prim[0] == "pipe":
            a, b = self._to_view(prim[1]), self._to_view(prim[2])
            # Overlap neighbouring segments slightly so antialiased edges don't leave seams
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            a = (a[0] - ux * SEGMENT_OVERLAP, a[1] - uy * SEGMENT_OVERLAP, a[2] - uz * SEGMENT_OVERLAP)
            b = (b[0] + ux * SEGMENT_OVERLAP, b[1] + uy * SEGMENT_OVERLAP, b[2] + uz * SEGMENT_OVERLAP)
            item = ((a[2] + b[2]) / 2.0, 0, prim, a, b)
        else:
            p = self._to_view(prim[1])
            item = (p[2], 1, prim, p, None)  # at equal depth, joints go over pipes
        slab = math.floor(item[0] / SLAB_DEPTH)
        self._slabs.setdefault(slab, []).append(item)
        self._new_items.setdefault(slab, []).append(item)
        self._dirty = True

    def _max_pipe_length(self) -> int:
        """Pipes stop growing at this length, so a cycle has about "pipes per cycle" pipes."""
        total = self._size[0] * self._size[1] * self._size[2]
        return max(4, round(self._cfg.fill * total / self._cfg.pipes_per_cycle))

    def _inside(self, cell: Cell) -> bool:
        return all(0 <= c < n for c, n in zip(cell, self._size))

    def _free_directions(self, cell: Cell, exclude: Optional[Cell] = None) -> List[Cell]:
        out = []
        for d in DIRECTIONS:
            if exclude is not None and d == exclude:
                continue
            n = (cell[0] + d[0], cell[1] + d[1], cell[2] + d[2])
            if self._inside(n) and n not in self.occupied:
                out.append(d)
        return out

    def _cycle_done(self) -> bool:
        total = self._size[0] * self._size[1] * self._size[2]
        return self.pipes_started >= self._cfg.pipes_per_cycle or len(self.occupied) >= self._cfg.fill * total

    def _start_pipe(self) -> bool:
        """Start a new pipe at a random free cell. False if there is no room."""
        free = [
            (x, y, z)
            for x in range(self._size[0]) for y in range(self._size[1]) for z in range(self._size[2])
            if (x, y, z) not in self.occupied
        ]
        self._rng.shuffle(free)
        for cell in free:
            dirs = self._free_directions(cell)
            if dirs:
                color = random_pipe_color(self._rng) if self._cfg.color_mode == "per pipe" else self._cycle_color
                self.pipe = Pipe(color, cell, self._rng.choice(dirs))
                self.occupied.add(cell)
                self._add(("ball", cell, JOINT_SCALE, color))  # starting knob
                self.pipes_started += 1
                return True
        return False

    def _end_pipe(self) -> None:
        pipe = self.pipe
        self._add(("ball", pipe.head, JOINT_SCALE, pipe.color))  # end knob
        self.pipe = None

    def step(self, now: float) -> None:
        """Advance the simulation by one cell."""
        if self.phase != "grow":
            return
        if self.pipe is None:
            if self._cycle_done() or not self._start_pipe():
                self.phase = "hold"
                self._hold_until = now + self._cfg.hold_seconds
            self._dirty = True
            return

        pipe = self.pipe
        reverse = (-pipe.direction[0], -pipe.direction[1], -pipe.direction[2])
        options = self._free_directions(pipe.head, exclude=reverse)
        if not options or pipe.length >= self._max_pipe_length():
            self._end_pipe()
            self._dirty = True
            return
        direction = pipe.direction
        if direction not in options or self._rng.random() < self._cfg.turn_chance:
            turns = [d for d in options if d != pipe.direction] or options
            direction = self._rng.choice(turns)
        if direction != pipe.direction:
            self._add(("ball", pipe.head, JOINT_SCALE, pipe.color))  # joint
            pipe.direction = direction
        nxt = (pipe.head[0] + direction[0], pipe.head[1] + direction[1], pipe.head[2] + direction[2])
        self._add(("pipe", pipe.head, nxt, pipe.color))
        self.occupied.add(nxt)
        pipe.head = nxt
        pipe.length += 1
        self._dirty = True

    def tick(self, now: float, visible: bool) -> None:
        if self.phase == "hold":
            if now >= self._hold_until:
                self.cycles += 1
                self._reset()
                self._last_time = now
            return
        if self._last_time is None:
            if visible:  # start growing once the viewer can see it
                self._last_time = now
            return
        self._step_budget += (now - self._last_time) * self._cfg.speed
        self._last_time = now
        steps = min(int(self._step_budget), MAX_STEPS_PER_TICK)
        self._step_budget -= int(self._step_budget)
        for _ in range(steps):
            self.step(now)
            if self.phase != "grow":
                break

    # ------------------------------------------------------------------
    # Tile interface
    # ------------------------------------------------------------------
    def swap_seconds(self, default: tuple[float, float]) -> tuple[float, float]:
        return 0.0, 0.0  # never swapped: it starts over by itself instead

    def can_swap(self) -> bool:
        return False

    def describe(self) -> str:
        gx, gy, gz = self._size
        return (f"{self.meta_tile.resource_filename}: {gx}x{gy}x{gz} grid, cycle {self.cycles + 1}, "
                f"{self.pipes_started} pipe(s), {len(self.occupied)} cells filled, phase {self.phase}")

    def set_height(self, target_h: int):
        self._height = target_h
        self._dirty = True

    def draw(self, painter: QPainter) -> None:
        if self._dirty or self._image is None or self._image.height() != self._height:
            self._render()
        painter.drawImage(int(self.x), 0, self._image)

    def removed(self):
        self._image = None
        self._layers = {}

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _to_view(self, cell: Cell) -> Vec3:
        """Cell centre in view space: centred on the grid, rotated by this cycle's camera angle."""
        gx, gy, gz = self._size
        x, y, z = cell[0] - (gx - 1) / 2.0, cell[1] - (gy - 1) / 2.0, cell[2] - (gz - 1) / 2.0
        cy, sy = math.cos(self._yaw), math.sin(self._yaw)
        x, z = x * cy + z * sy, -x * sy + z * cy
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        y, z = y * cp - z * sp, y * sp + z * cp
        return x, y, z

    def _projector(self, w: int, h: int):
        """Returns project(view_point) -> (screen x, screen y, px per world unit at that depth)."""
        gx, _, gz = self._size
        distance = 1.6 * max(gz, gx * 0.6)  # camera distance from the grid centre
        focal = w * distance / gx           # the grid's middle plane spans the tile width
        cx, cy = w / 2.0, h / 2.0

        def project(p: Vec3) -> Tuple[float, float, float]:
            depth = max(0.5, p[2] + distance)
            scale = focal / depth
            return cx + p[0] * scale, cy + p[1] * scale, scale

        return project, distance

    @staticmethod
    def _draw_order(item: tuple) -> Tuple[float, int]:
        """Sort key: farther first; at equal depth, joints after pipes."""
        return -item[0], item[1]

    def _screen_box(self, item: tuple, project, radius: float) -> Tuple[float, float, float, float]:
        """Screen-space bounding box (x0, y0, x1, y1) of a primitive."""
        _, _, prim, a, b = item
        points = [project(a)] if b is None else [project(a), project(b)]
        r = radius * (prim[2] if prim[0] == "ball" else 1.0)
        pad = max(s for _, _, s in points) * r + 1.0
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def _can_append(self, slab: int, new: List[tuple], project, radius: float) -> bool:
        """
        True if the new items can simply be painted on top of the slab's
        cached layer: no older item that sorts after them (is nearer)
        overlaps them on screen.
        """
        new_ids = {id(item) for item in new}
        for n in new:
            nx0, ny0, nx1, ny1 = self._screen_box(n, project, radius)
            key = self._draw_order(n)
            for m in self._slabs[slab]:
                if id(m) in new_ids or self._draw_order(m) <= key:
                    continue
                mx0, my0, mx1, my1 = self._screen_box(m, project, radius)
                if mx0 < nx1 and nx0 < mx1 and my0 < ny1 and ny0 < my1:
                    return False
        return True

    def _paint_items(self, layer: QImage, items: List[tuple], project, radius: float) -> None:
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for _, _, prim, a, b in sorted(items, key=self._draw_order):
            if prim[0] == "pipe":
                self._draw_cylinder(painter, project, a, b, radius, prim[3])
            else:
                self._draw_sphere(painter, project, a, radius * prim[2], prim[3])
        painter.end()

    def _render(self) -> None:
        """
        Painter's algorithm, made incremental. Primitives are grouped into
        depth slabs, each cached as its own transparent layer, and the final
        image stacks the layers far to near. A new primitive is painted on
        top of its slab's layer when nothing nearer in that slab overlaps it;
        otherwise the slab is redrawn in depth order. Segments are one cell
        long, so sorting them by their midpoints is good enough.
        """
        w, h = max(1, self.width), max(1, self._height)
        if self._image is None or self._image.size() != QSize(w, h):
            self._layers = {}  # new size: every layer has to be redrawn
        project, _ = self._projector(w, h)
        radius = self._cfg.pipe_radius

        for slab in self._slabs:
            new = self._new_items.get(slab)
            layer = self._layers.get(slab)
            if layer is not None and not new:
                continue  # unchanged
            if layer is not None and self._can_append(slab, new, project, radius):
                self._paint_items(layer, new, project, radius)
                continue
            if layer is None:
                layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
                self._layers[slab] = layer
            layer.fill(Qt.GlobalColor.transparent)
            self._paint_items(layer, self._slabs[slab], project, radius)
        self._new_items = {}

        image = QImage(w, h, QImage.Format.Format_RGB32)
        image.fill(QColor("black"))
        painter = QPainter(image)
        for slab in sorted(self._layers, reverse=True):  # far to near
            painter.drawImage(0, 0, self._layers[slab])
        painter.end()
        self._image = image
        self._dirty = False

    @staticmethod
    def _draw_cylinder(painter: QPainter, project, a: Vec3, b: Vec3, radius: float, color: QColor) -> None:
        ax, ay, sa = project(a)
        bx, by, sb = project(b)
        ra, rb = radius * sa, radius * sb
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-6:
            # Seen exactly end-on: just the round end
            painter.setBrush(color.darker(130))
            painter.drawEllipse(QPointF(ax, ay), max(ra, rb), max(ra, rb))
            return
        nx, ny = -dy / length, dx / length  # screen-space normal across the pipe

        # Shading across the pipe: the surface normal turns from -n through
        # "towards the viewer" to +n (Lambert diffuse + Phong-ish highlight)
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        half = (ra + rb) / 2.0
        gradient = QLinearGradient(mx - nx * half, my - ny * half, mx + nx * half, my + ny * half)
        steps = 10
        for i in range(steps + 1):
            s = -1.0 + 2.0 * i / steps
            facing = math.sqrt(max(0.0, 1.0 - s * s))
            normal = (s * nx, s * ny, -facing)
            diffuse = max(0.0, sum(n * l for n, l in zip(normal, LIGHT)))
            gradient.setColorAt(i / steps, _shade(color, diffuse, diffuse ** 24))
        painter.setBrush(QBrush(gradient))

        # A pipe running towards the viewer shows its round end; draw it under the side
        if length < ra + rb:
            near_x, near_y, near_r = (ax, ay, ra) if sa > sb else (bx, by, rb)
            end = QRadialGradient(QPointF(near_x, near_y), near_r)
            end.setColorAt(0.0, _shade(color, 0.85, 0.0))
            end.setColorAt(1.0, _shade(color, 0.35, 0.0))
            painter.setBrush(QBrush(end))
            painter.drawEllipse(QPointF(near_x, near_y), near_r, near_r)
            painter.setBrush(QBrush(gradient))

        painter.drawPolygon(QPolygonF([
            QPointF(ax + nx * ra, ay + ny * ra), QPointF(bx + nx * rb, by + ny * rb),
            QPointF(bx - nx * rb, by - ny * rb), QPointF(ax - nx * ra, ay - ny * ra),
        ]))

    @staticmethod
    def _draw_sphere(painter: QPainter, project, p: Vec3, radius: float, color: QColor) -> None:
        x, y, scale = project(p)
        r = radius * scale
        focal = QPointF(x + LIGHT[0] * r * 0.55, y + LIGHT[1] * r * 0.55)
        gradient = QRadialGradient(QPointF(x, y), r, focal)
        gradient.setColorAt(0.0, _shade(color, 1.0, 0.9))
        gradient.setColorAt(0.18, _shade(color, 0.95, 0.15))
        gradient.setColorAt(0.65, _shade(color, 0.55, 0.0))
        gradient.setColorAt(1.0, _shade(color, 0.12, 0.0))
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(x, y), r, r)
