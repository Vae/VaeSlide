"""
Step-by-step maze generators for TileMaze.

Each generator takes (cols, rows, rng) and yields one event per animation
step, so the maze can be drawn gradually:

    ("visit", a)     carve cell a and move the head there
    ("link", a, b)   carve cells a and b and the wall between them; head -> b
    ("head", a)      only move the head (e.g. backtracking, random walks)

Cells are (col, row) tuples. Every algorithm produces a perfect maze (a
spanning tree), so there is exactly one path between any two cells.
"""

import random
from typing import Callable, Dict, Iterator, List, Tuple

Cell = Tuple[int, int]
Event = tuple
Generator = Callable[[int, int, random.Random], Iterator[Event]]


def _neighbors(cell: Cell, cols: int, rows: int) -> List[Cell]:
    c, r = cell
    out = []
    if r > 0:
        out.append((c, r - 1))
    if c < cols - 1:
        out.append((c + 1, r))
    if r < rows - 1:
        out.append((c, r + 1))
    if c > 0:
        out.append((c - 1, r))
    return out


def recursive_backtracker(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Depth-first search: long winding corridors, few dead ends."""
    start = (rng.randrange(cols), rng.randrange(rows))
    visited = {start}
    stack = [start]
    yield ("visit", start)
    while stack:
        current = stack[-1]
        options = [n for n in _neighbors(current, cols, rows) if n not in visited]
        if options:
            nxt = rng.choice(options)
            visited.add(nxt)
            stack.append(nxt)
            yield ("link", current, nxt)
        else:
            stack.pop()
            if stack:
                yield ("head", stack[-1])


def prim(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Randomized Prim's: grows outward from one point; many short dead ends."""
    start = (rng.randrange(cols), rng.randrange(rows))
    visited = {start}
    frontier: List[Cell] = []
    in_frontier = set()

    def add_frontier(cell: Cell) -> None:
        for n in _neighbors(cell, cols, rows):
            if n not in visited and n not in in_frontier:
                in_frontier.add(n)
                frontier.append(n)

    yield ("visit", start)
    add_frontier(start)
    while frontier:
        i = rng.randrange(len(frontier))
        frontier[i], frontier[-1] = frontier[-1], frontier[i]
        cell = frontier.pop()
        in_frontier.discard(cell)
        parent = rng.choice([n for n in _neighbors(cell, cols, rows) if n in visited])
        visited.add(cell)
        yield ("link", parent, cell)
        add_frontier(cell)


def kruskal(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Randomized Kruskal's: joins random walls everywhere at once."""
    parent = {(c, r): (c, r) for c in range(cols) for r in range(rows)}

    def find(cell: Cell) -> Cell:
        while parent[cell] != cell:
            parent[cell] = parent[parent[cell]]
            cell = parent[cell]
        return cell

    edges = [((c, r), (c + 1, r)) for c in range(cols - 1) for r in range(rows)]
    edges += [((c, r), (c, r + 1)) for c in range(cols) for r in range(rows - 1)]
    rng.shuffle(edges)
    if cols * rows == 1:
        yield ("visit", (0, 0))
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            yield ("link", a, b)


def aldous_broder(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Random walk: unbiased but slow to finish (the head wanders a lot)."""
    current = (rng.randrange(cols), rng.randrange(rows))
    visited = {current}
    remaining = cols * rows - 1
    yield ("visit", current)
    while remaining:
        nxt = rng.choice(_neighbors(current, cols, rows))
        if nxt not in visited:
            visited.add(nxt)
            remaining -= 1
            yield ("link", current, nxt)
        else:
            yield ("head", nxt)
        current = nxt


def wilson(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Loop-erased random walks: unbiased; slow start, fast finish."""
    cells = [(c, r) for c in range(cols) for r in range(rows)]
    rng.shuffle(cells)
    visited = {cells[0]}
    yield ("visit", cells[0])
    for start in cells[1:]:
        if start in visited:
            continue
        path = [start]
        index = {start: 0}
        current = start
        while current not in visited:
            nxt = rng.choice(_neighbors(current, cols, rows))
            yield ("head", nxt)
            if nxt in index:  # loop: erase it
                for cell in path[index[nxt] + 1:]:
                    del index[cell]
                path = path[:index[nxt] + 1]
            else:
                index[nxt] = len(path)
                path.append(nxt)
            current = nxt
        for a, b in zip(path, path[1:]):
            visited.add(a)
            yield ("link", b, a)  # grow from the maze outward


def hunt_and_kill(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Random walk until stuck, then scan for a new starting point."""
    current = (rng.randrange(cols), rng.randrange(rows))
    visited = {current}
    yield ("visit", current)
    while True:
        options = [n for n in _neighbors(current, cols, rows) if n not in visited]
        if options:
            nxt = rng.choice(options)
            visited.add(nxt)
            yield ("link", current, nxt)
            current = nxt
            continue
        # Hunt: first unvisited cell (row by row) that touches the maze
        found = None
        for r in range(rows):
            yield ("head", (0, r))
            for c in range(cols):
                cell = (c, r)
                if cell in visited:
                    continue
                touching = [n for n in _neighbors(cell, cols, rows) if n in visited]
                if touching:
                    found = (rng.choice(touching), cell)
                    break
            if found:
                break
        if not found:
            return
        visited.add(found[1])
        yield ("link", *found)
        current = found[1]


def binary_tree(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Each cell links north or west: fast, with a strong diagonal bias."""
    for r in range(rows):
        for c in range(cols):
            options = []
            if r > 0:
                options.append((c, r - 1))
            if c > 0:
                options.append((c - 1, r))
            if options:
                yield ("link", rng.choice(options), (c, r))
            else:
                yield ("visit", (c, r))


def sidewinder(cols: int, rows: int, rng: random.Random) -> Iterator[Event]:
    """Row by row: horizontal runs, each with one link north."""
    for r in range(rows):
        run: List[Cell] = []
        for c in range(cols):
            cell = (c, r)
            run.append(cell)
            at_east = c == cols - 1
            carve_east = not at_east and (r == 0 or rng.random() < 0.5)
            if carve_east:
                yield ("link", cell, (c + 1, r))
            elif r > 0:
                member = rng.choice(run)
                yield ("link", (member[0], r - 1), member)
                run = []
            else:
                yield ("visit", cell)  # end of the top row's single run


ALGORITHMS: Dict[str, Generator] = {
    "recursive backtracker": recursive_backtracker,
    "prim": prim,
    "kruskal": kruskal,
    "aldous-broder": aldous_broder,
    #"wilson": wilson,
    "hunt and kill": hunt_and_kill,
    "binary tree": binary_tree,
    "sidewinder": sidewinder,
}
