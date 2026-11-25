"""
TileMaze: a procedural tile that gradually draws a maze, then optionally
solves it. Settings live in the "tile maze" section of settings.json.
"""

import logging
import random
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from src.AssetScanner import AssetScanner
from src.MazeGenerators import ALGORITHMS, Cell
from src.MediaCache import MediaCache
from src.Settings import get_settings, section_config
from src.Tile import MetaTile, Tile, TileContext

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "tile maze"  # this module's section in settings.json
MAX_STEPS_PER_TICK = 2000       # stops a long stall (e.g. after a hitch) from freezing a frame
FLASH_HZ = 3.0                  # solution flashes per second once solved

# Block states in the (2*cols+1) x (2*rows+1) grid of cells and walls
WALL, PASSAGE, SOLUTION = 0, 1, 2


@dataclass(frozen=True)
class MazeConfig:
    count: int
    weight: float
    cols: int
    rows: int
    algorithm: str
    speed: float
    wall_size: float
    solve: bool
    hold_seconds: float
    flash_seconds: float


@section_config
def maze_config() -> MazeConfig:
    """Validated "tile maze" settings, read once."""
    section = get_settings().section(SETTINGS_SECTION)
    defaults = get_settings().DEFAULTS[SETTINGS_SECTION]

    size = section.get("maze size")
    try:
        cols, rows = (int(v) for v in size)
        if not (2 <= cols <= 500 and 2 <= rows <= 500):
            raise ValueError("each dimension must be 2..500")
    except (TypeError, ValueError) as e:
        logger.error("Setting \"%s.maze size\" must be [cols, rows] (2..500 each), got %r (%s). Using %r.",
                     SETTINGS_SECTION, size, e, defaults["maze size"])
        cols, rows = defaults["maze size"]

    algorithm = section.get("algorithm")
    if algorithm != "random" and algorithm not in ALGORITHMS:
        logger.error("Setting \"%s.algorithm\" must be \"random\" or one of %s, got %r. Using \"random\".",
                     SETTINGS_SECTION, list(ALGORITHMS), algorithm)
        algorithm = "random"

    speed = section.get_number("speed")
    if speed <= 0:
        logger.error("Setting \"%s.speed\" must be > 0, got %r. Using %r.",
                     SETTINGS_SECTION, speed, defaults["speed"])
        speed = float(defaults["speed"])

    wall_size = min(0.9, max(0.05, section.get_number("wall size")))

    return MazeConfig(
        count=max(0, section.get_number("count", int)),
        weight=max(0.0, section.get_number("weight")),
        cols=cols,
        rows=rows,
        algorithm=algorithm,
        speed=speed,
        wall_size=wall_size,
        solve=bool(section.get("solve")),
        hold_seconds=max(0.0, section.get_number("hold seconds")),
        flash_seconds=max(0.0, section.get_number("flash seconds")),
    )


def _random_palette(rng: random.Random) -> Dict[str, QColor]:
    """Random colors, kept readable: walls and passages differ strongly in brightness."""
    dark = QColor.fromHsvF(rng.random(), rng.uniform(0.4, 0.9), rng.uniform(0.08, 0.3))
    light = QColor.fromHsvF(rng.random(), rng.uniform(0.1, 0.5), rng.uniform(0.75, 0.95))
    wall, passage = (dark, light) if rng.random() < 0.5 else (light, dark)
    start_hue = rng.random()
    return {
        "wall": wall,
        "passage": passage,
        "solution": QColor.fromHsvF(rng.random(), rng.uniform(0.75, 1.0), rng.uniform(0.8, 1.0)),
        "head": QColor.fromHsvF(rng.random(), 1.0, 1.0),
        # Start/end: bright, and on opposite sides of the color wheel from each other
        "start": QColor.fromHsvF(start_hue, 1.0, 1.0),
        "end": QColor.fromHsvF((start_hue + 0.5) % 1.0, 1.0, 1.0),
    }


class TileMazeScanner(AssetScanner):
    """Creates the maze entries for the selection pool from settings (there are no files)."""

    NEEDS_PATH = False

    def scan(self, media_cache: MediaCache, path: Optional[str] = None) -> list[MetaTile]:
        cfg = maze_config()
        # The aspect ratio of the drawn maze: cols cells plus one extra wall
        f = cfg.wall_size
        width = round((cfg.cols + f) * 1000)
        height = round((cfg.rows + f) * 1000)
        meta_tiles = [
            MetaTile(weight=cfg.weight, type=TileMaze.TYPE, width=width, height=height,
                     resource_filename=f"maze:{i}", json_filename="")
            for i in range(cfg.count)
        ]
        logger.info("Added %d maze tile(s) (%dx%d, algorithm %s).",
                    len(meta_tiles), cfg.cols, cfg.rows, cfg.algorithm)
        return meta_tiles


class TileMaze(Tile):
    """
    Draws a maze one generator step at a time, starting once it first comes
    on screen. When generation finishes it optionally animates the solution
    from the top-left entrance to the bottom-right exit.
    """

    TYPE = "maze"
    SCANNER = TileMazeScanner

    def __init__(self, meta_tile: MetaTile, x: float, h: int, ctx: TileContext):
        super().__init__(meta_tile, x, h, ctx)
        cfg = maze_config()
        self._cfg = cfg
        self._cols, self._rows = cfg.cols, cfg.rows
        self._rng = random.Random()
        self.algorithm = (self._rng.choice(list(ALGORITHMS)) if cfg.algorithm == "random"
                          else cfg.algorithm)
        self._events = ALGORITHMS[self.algorithm](self._cols, self._rows, self._rng)
        self._colors = _random_palette(self._rng)

        self._bw, self._bh = 2 * self._cols + 1, 2 * self._rows + 1
        self._blocks = bytearray(self._bw * self._bh)  # all WALL
        self._links: Dict[Cell, List[Cell]] = {}
        self._head: Optional[Cell] = None

        # Random start and end cells, at least half the maze apart (Manhattan distance)
        self.start, self.end = self._pick_endpoints()

        self._phase = "generate"  # -> "solve" -> "flash" -> "done"
        self._solution: List[Cell] = []
        self._solution_blocks: List[tuple[int, int]] = []  # cells and connectors, for flashing
        self._solution_pos = 0
        self._last_time: Optional[float] = None
        self._step_budget = 0.0
        self._finished_at: Optional[float] = None  # maze complete (and solution drawn, if solving)
        self._flash_until: Optional[float] = None
        self._done_at: Optional[float] = None
        self._now = 0.0

        self._image: Optional[QImage] = None
        self._x_edges: List[int] = []
        self._y_edges: List[int] = []
        self._render_all()

    def _pick_endpoints(self) -> tuple[Cell, Cell]:
        min_dist = (self._cols + self._rows) // 2  # always reachable: corners are cols+rows-2 apart
        while True:
            a = (self._rng.randrange(self._cols), self._rng.randrange(self._rows))
            b = (self._rng.randrange(self._cols), self._rng.randrange(self._rows))
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) >= min_dist:
                return a, b

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------
    def _index(self, bx: int, by: int) -> int:
        return by * self._bw + bx

    @staticmethod
    def _cell_block(cell: Cell) -> tuple[int, int]:
        return 2 * cell[0] + 1, 2 * cell[1] + 1

    @staticmethod
    def _between_block(a: Cell, b: Cell) -> tuple[int, int]:
        return a[0] + b[0] + 1, a[1] + b[1] + 1

    @staticmethod
    def _edges(n_cells: int, size_px: int, wall_size: float) -> List[int]:
        """
        Pixel edges of the 2n+1 blocks along one axis: walls (even blocks) are
        wall_size of a cell pitch thick, passages (odd blocks) the rest.
        """
        pitch = size_px / (n_cells + wall_size)
        edges = []
        for i in range(2 * n_cells + 1):
            edges.append(round((i // 2) * pitch + (i % 2) * wall_size * pitch))
        edges.append(size_px)
        return edges

    def _block_rect(self, bx: int, by: int) -> QRect:
        x0, x1 = self._x_edges[bx], self._x_edges[bx + 1]
        y0, y1 = self._y_edges[by], self._y_edges[by + 1]
        return QRect(x0, y0, x1 - x0, y1 - y0)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _color_for(self, state: int) -> QColor:
        return (self._colors["wall"], self._colors["passage"], self._colors["solution"])[state]

    def _render_all(self) -> None:
        """Redraw the whole maze image at the tile's current size."""
        w, h = self.width, self._height
        if w <= 0 or h <= 0:
            self._image = None
            return
        self._x_edges = self._edges(self._cols, w, self._cfg.wall_size)
        self._y_edges = self._edges(self._rows, h, self._cfg.wall_size)
        self._image = QImage(w, h, QImage.Format.Format_RGB32)
        self._image.fill(self._colors["wall"])
        painter = QPainter(self._image)
        for by in range(self._bh):
            for bx in range(self._bw):
                state = self._blocks[self._index(bx, by)]
                if state != WALL:
                    painter.fillRect(self._block_rect(bx, by), self._color_for(state))
        painter.end()

    def _paint_blocks(self, blocks: List[tuple[int, int]]) -> None:
        if self._image is None or not blocks:
            return
        painter = QPainter(self._image)
        for bx, by in blocks:
            painter.fillRect(self._block_rect(bx, by), self._color_for(self._blocks[self._index(bx, by)]))
        painter.end()

    def _set_block(self, block: tuple[int, int], state: int, changed: List[tuple[int, int]]) -> None:
        i = self._index(*block)
        if self._blocks[i] != state:
            self._blocks[i] = state
            changed.append(block)

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------
    def tick(self, now: float, visible: bool) -> None:
        self._now = now
        if self._phase == "flash":
            if now >= self._flash_until:
                self._finish(now)
            return
        if self._phase == "done":
            return
        if self._last_time is None:
            if visible:  # start drawing once the viewer can see it
                self._last_time = now
            return

        self._step_budget += (now - self._last_time) * self._cfg.speed
        self._last_time = now
        steps = int(self._step_budget)
        self._step_budget -= steps
        steps = min(steps, MAX_STEPS_PER_TICK)

        changed: List[tuple[int, int]] = []
        for _ in range(steps):
            if not self._step(changed):
                break
        self._paint_blocks(changed)

    def _step(self, changed: List[tuple[int, int]]) -> bool:
        """Advance one step. Returns False once everything is finished."""
        if self._phase == "generate":
            event = next(self._events, None)
            if event is None:
                self._finish_generation()
                return self._phase == "solve"
            kind = event[0]
            if kind == "visit":
                self._set_block(self._cell_block(event[1]), PASSAGE, changed)
                self._head = event[1]
            elif kind == "link":
                a, b = event[1], event[2]
                self._set_block(self._cell_block(a), PASSAGE, changed)
                self._set_block(self._cell_block(b), PASSAGE, changed)
                self._set_block(self._between_block(a, b), PASSAGE, changed)
                self._links.setdefault(a, []).append(b)
                self._links.setdefault(b, []).append(a)
                self._head = b
            elif kind == "head":
                self._head = event[1]
            return True

        if self._phase == "solve":
            if self._solution_pos >= len(self._solution):
                # Solution fully drawn: flash it before the tile may be swapped
                self._finished_at = self._now
                self._flash_until = self._now + self._cfg.flash_seconds
                self._phase = "flash"
                return False
            cell = self._solution[self._solution_pos]
            if self._solution_pos > 0:
                between = self._between_block(self._solution[self._solution_pos - 1], cell)
                self._set_block(between, SOLUTION, changed)
                self._solution_blocks.append(between)
            self._set_block(self._cell_block(cell), SOLUTION, changed)
            self._solution_blocks.append(self._cell_block(cell))
            self._solution_pos += 1
            return True

        return False

    def _finish_generation(self) -> None:
        self._head = None
        logger.debug("Maze %s generated with %s", self.meta_tile.resource_filename, self.algorithm)
        if self._cfg.solve:
            self._solution = self._solve()
            self._phase = "solve"
        else:
            self._finished_at = self._now
            self._finish(self._now)

    def _finish(self, now: float) -> None:
        self._phase = "done"
        self._done_at = now

    def _solve(self) -> List[Cell]:
        """Breadth-first search from the start cell to the end cell."""
        start, goal = self.start, self.end
        came_from: Dict[Cell, Optional[Cell]] = {start: None}
        queue = deque([start])
        while queue:
            cell = queue.popleft()
            if cell == goal:
                break
            for n in self._links.get(cell, []):
                if n not in came_from:
                    came_from[n] = cell
                    queue.append(n)
        if goal not in came_from:
            return []
        path = []
        cell: Optional[Cell] = goal
        while cell is not None:
            path.append(cell)
            cell = came_from[cell]
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # Tile interface
    # ------------------------------------------------------------------
    def describe(self) -> str:
        return (f"{self.meta_tile.resource_filename}: algorithm \"{self.algorithm}\", "
                f"{self._cols}x{self._rows}, start {self.start}, end {self.end}, phase {self._phase}")

    def can_swap(self) -> bool:
        """
        Only swap a finished maze: after the solution has stopped flashing, and
        at least "hold seconds" after the maze (and its solution) completed.
        """
        return (self._done_at is not None
                and self._now - self._finished_at >= self._cfg.hold_seconds)

    def set_height(self, target_h: int):
        self._height = target_h
        self._render_all()

    def draw(self, painter: QPainter) -> None:
        if self._image is None:
            return
        x = int(self.x)
        painter.drawImage(x, 0, self._image)
        if not self._x_edges:
            return

        # Flash: blank the solution path on the "off" half of each cycle
        if self._phase == "flash" and int((self._flash_until - self._now) * FLASH_HZ * 2) % 2 == 1:
            passage = self._colors["passage"]
            for block in self._solution_blocks:
                painter.fillRect(self._block_rect(*block).translated(x, 0), passage)

        if self._head is not None:
            painter.fillRect(self._block_rect(*self._cell_block(self._head)).translated(x, 0),
                             self._colors["head"])

        # Start and end markers are always visible, on top of everything
        painter.fillRect(self._block_rect(*self._cell_block(self.start)).translated(x, 0),
                         self._colors["start"])
        painter.fillRect(self._block_rect(*self._cell_block(self.end)).translated(x, 0),
                         self._colors["end"])

    def removed(self):
        self._image = None
