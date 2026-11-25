"""Maze generators and the TileMaze lifecycle."""

import random
from collections import deque

import pytest

from src.MazeGenerators import ALGORITHMS
from src.Settings import Settings
from src.Tile import TileContext
from src.TileMaze import TileMaze, TileMazeScanner, maze_config

SIZES = [(2, 2), (7, 5), (20, 13), (1 + 30, 3)]


def carve(algorithm, cols, rows, seed):
    """Run a generator to the end. Returns (edges, cells touched by any event)."""
    edges, touched = set(), set()
    for event in ALGORITHMS[algorithm](cols, rows, random.Random(seed)):
        kind = event[0]
        assert kind in ("visit", "link", "head"), event
        cells = event[1:]
        touched.update(cells)
        for c, r in cells:
            assert 0 <= c < cols and 0 <= r < rows, f"{event} is outside the grid"
        if kind == "link":
            a, b = cells
            assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, f"{event} links non-neighbours"
            edge = frozenset((a, b))
            assert edge not in edges, f"{event} carves the same wall twice"
            edges.add(edge)
    return edges, touched


@pytest.mark.parametrize("algorithm", sorted(ALGORITHMS))
@pytest.mark.parametrize("cols, rows", SIZES)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_generators_make_perfect_mazes(algorithm, cols, rows, seed):
    """A perfect maze is a spanning tree: every cell connected, no loops."""
    edges, touched = carve(algorithm, cols, rows, seed)
    all_cells = {(c, r) for c in range(cols) for r in range(rows)}
    assert touched == all_cells
    assert len(edges) == cols * rows - 1  # a tree on n nodes has n-1 edges

    links = {}
    for edge in edges:
        a, b = tuple(edge)
        links.setdefault(a, []).append(b)
        links.setdefault(b, []).append(a)
    seen, queue = {(0, 0)}, deque([(0, 0)])
    while queue:
        for n in links.get(queue.popleft(), []):
            if n not in seen:
                seen.add(n)
                queue.append(n)
    assert seen == all_cells  # connected + n-1 edges => no loops


@pytest.mark.parametrize("algorithm", sorted(ALGORITHMS))
def test_generators_are_deterministic_for_a_seed(algorithm):
    assert carve(algorithm, 9, 6, 42) == carve(algorithm, 9, 6, 42)


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def test_maze_config_validation(use_settings):
    use_settings({"tile maze": {"maze size": [1, 9999], "algorithm": "nope", "speed": -1,
                                "wall size": 5, "count": -3, "hold seconds": -1}})
    cfg = maze_config()
    defaults = Settings.DEFAULTS["tile maze"]
    assert (cfg.cols, cfg.rows) == tuple(defaults["maze size"])
    assert cfg.algorithm == "random"
    assert cfg.speed == defaults["speed"]
    assert cfg.wall_size == 0.9
    assert cfg.count == 0
    assert cfg.hold_seconds == 0.0


def test_scanner_entries(use_settings):
    use_settings({"tile maze": {"count": 3, "weight": 7.0, "maze size": [10, 5], "wall size": 0.5}})
    metas = TileMazeScanner().scan(None)
    assert [m.resource_filename for m in metas] == ["maze:0", "maze:1", "maze:2"]
    assert all(m.type == "maze" and m.weight == 7.0 for m in metas)
    # Aspect ratio of the drawn maze: (cols + wall) : (rows + wall)
    assert metas[0].width / metas[0].height == pytest.approx(10.5 / 5.5)
    assert not TileMazeScanner.NEEDS_PATH


# ----------------------------------------------------------------------
# TileMaze lifecycle
# ----------------------------------------------------------------------
def make_maze(use_settings, **overrides) -> TileMaze:
    section = {"maze size": [6, 4], "speed": 1000, "solve": True,
               "hold seconds": 2.0, "flash seconds": 1.0, "algorithm": "prim"}
    section.update(overrides)
    use_settings({"tile maze": section})
    meta = TileMazeScanner().scan(None)[0] if maze_config().count else None
    return TileMaze(meta, 0.0, 120, TileContext())


def run_until(maze, phases, start=0.0, step=0.05, limit=60.0):
    """Tick (visible) until the maze leaves `phases`. Returns the time reached."""
    now = start
    while maze._phase in phases:
        now += step
        maze.tick(now, True)
        assert now < limit, f"stuck in phase {maze._phase}"
    return now


def test_maze_waits_until_visible(qapp, use_settings):
    maze = make_maze(use_settings)
    for t in range(5):
        maze.tick(float(t), False)
    assert maze._phase == "generate" and not maze._links


def test_maze_generates_solves_flashes_then_allows_swap(qapp, use_settings):
    maze = make_maze(use_settings)
    assert maze.algorithm == "prim"
    assert "prim" in maze.describe()
    cols, rows = 6, 4
    sx, sy = maze.start
    ex, ey = maze.end
    assert abs(sx - ex) + abs(sy - ey) >= (cols + rows) // 2

    maze.tick(0.0, True)
    now = run_until(maze, {"generate", "solve"})
    assert maze._phase == "flash"
    path = maze._solution
    assert path[0] == maze.start and path[-1] == maze.end
    for a, b in zip(path, path[1:]):
        assert b in maze._links[a], "solution must follow carved passages"
    assert not maze.can_swap()  # flashing

    now = run_until(maze, {"flash"}, start=now)
    assert maze._phase == "done"
    assert maze._done_at - maze._finished_at >= 1.0  # flashed for "flash seconds"
    # "hold seconds" (2) counts from when the maze completed, which includes the flash
    maze.tick(maze._finished_at + 1.9, True)
    assert not maze.can_swap()
    maze.tick(maze._finished_at + 2.001, True)  # just past, to avoid float rounding at exactly 2.0
    assert maze.can_swap()


def test_maze_without_solving_is_done_after_generation(qapp, use_settings):
    maze = make_maze(use_settings, solve=False, **{"hold seconds": 0})
    maze.tick(0.0, True)
    run_until(maze, {"generate"})
    assert maze._phase == "done"
    assert maze._solution == []
    assert maze.can_swap()


def test_maze_draws_and_resizes(qapp, use_settings):
    from PySide6.QtGui import QImage, QPainter
    maze = make_maze(use_settings)
    maze.tick(0.0, True)
    run_until(maze, {"generate", "solve"})
    target = QImage(maze.width, maze.height, QImage.Format.Format_ARGB32)
    painter = QPainter(target)
    maze.draw(painter)
    painter.end()
    maze.set_height(60)
    assert maze.height == 60 and maze._image.height() == 60
