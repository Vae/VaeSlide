# VaeScroll: Not just a slideshow, a sliding slideshow!

A scrolling screensaver written in Python with PySide6. Images, videos and procedurally generated scenes scroll sideways across several parallax layers. Tiles swap in place through animated transitions, and on the OpenGL renderer they can pick up GPU shader effects.

### Why?

- Learning how to use AI in my personal projects and how it fits into IDE workflows. Plus, I want to see what AI can do and how far I can push it
- Experimenting with Python. I'm mostly a C++ developer, but I'm interested in expanding my skillset and Python is extremely popular
- Creating a fun project I'll personally use
- Publishing something someone might want to use

## Features

### Scrolling
- Any number of parallax layers, each with its own speed and vertical overlap
- An endless strip on each layer that fills and culls tiles in either direction
- The mouse wheel speeds up or slows down the layer under the cursor; slowing past zero reverses it
- A global speed multiplier, plus adjustable gaps (or overlaps) between tiles
- Smooth, time-based motion synced to the display's refresh, so scrolling is the same speed at any refresh rate

### Tiles
- **Images** from any number of folders, loaded and scaled on background threads so scrolling never stalls
- **Videos** (mp4, webm, mkv, mov, avi, ogv, gif) that loop, and are only swapped out after playing through once. The number of videos loaded at once can be capped or weighted down
- **Mazes**, generated live by one of eight algorithms (recursive backtracker, Prim, Kruskal, Aldous-Broder, Wilson, hunt and kill, binary tree, sidewinder), then solved with an animated path
- **3D pipes**, a software-rendered homage to the classic screensaver: lit pipes grow through a 3D grid, then clear and start again with new colours and a new camera angle
- Weighted random selection per layer, so recently shown assets come up less often
- Optional `<name>.json` sidecar next to an image to set its weight or size
- The tile system is extensible: a new kind of tile is a `Tile` subclass plus a scanner

### Swaps and transitions
- After a random time on screen, a tile swaps in place for another asset of the same aspect ratio, so its neighbours don't move
- A swap waits until the new asset has loaded, so a slot never flashes blank
- Transitions: **fade**, **star swipe**, **zoom** (dolly-through), **spin** (perspective card flip), and **mask** (luma wipes shaped as noise, linear, radial, clock or blinds, with a soft edge)
- On OpenGL the mask wipe runs as a GPU shader; elsewhere it uses an equivalent QPainter version

### OpenGL and shaders
- An OpenGL renderer with MSAA, falling back to plain raster painting when OpenGL isn't available
- Per-tile GPU effects, rolled by chance as each tile is created:
  - **Hue rotate**: the tile's colours slowly cycle around the colour wheel
  - **Water drop**: ripples spread across the tile as it comes on screen, bending and lighting the image
- Shaded tiles keep their effect through every transition, including perspective, opacity and clipping
- Every effect can be turned off, one at a time or all together

### Other
- Left-click a tile to log what it is (a file path, or a maze's algorithm and state)
- Middle-click a tile to move its file into an archive folder
- Optional frame stats (fps and paint time) in the log
- A pytest suite covering layout, selection, swapping, transitions and shaders, with pixel-by-pixel comparisons of the GPU output against the QPainter versions

## Getting started

### Requirements
- Python 3.12 (other recent 3.x versions probably work, but aren't tested)
- Windows. Other platforms supported by PySide6 should work, but haven't been tried
- A GPU with OpenGL 3.3 for the shader effects (optional)

### Install

```
git clone <this repo>
cd VaeSlide
python -m venv .venv
.venv\Scripts\activate
pip install PySide6 numpy Pillow moviepy
pip install -r requirements-dev.txt      # pytest, only needed to run the tests
```

### Point it at your pictures

Create `assets.json` in the repo root, listing the folders to show:

```json
{
  "sources": [
    {"type": "image", "path": "C:/Pictures/slideshow"},
    {"type": "video", "path": "C:/Videos/loops"},
    {"type": "image", "path": "#C:/Pictures/disabled"}
  ]
}
```

A path starting with `#` is skipped, and a folder that doesn't exist is skipped with a warning. Mazes and 3D pipes need no folder. The first run reads every file's size and caches it in `.media_cache.json`, so later starts are fast.

### Run

Run from the repo root as a module:

```
.venv\Scripts\python -m src.VaeScroll
```

| Option | Effect |
|---|---|
| `--speed <multiplier>` | Overrides `"global speed"` |
| `--renderer opengl\|raster` | Overrides `"renderer"` |
| `--frame-stats` | Logs fps and paint time every 5 seconds |
| `--window` | Accepted; the app currently always runs windowed |

Any key, a drag, or a click on empty space quits.

### Settings

Optional `settings.json` in the repo root. Every key has a built-in default, so the file only needs what you want to change. The defaults, with comments, are in `Settings.DEFAULTS` in `src/Settings.py`. For example:

```json
{
  "global speed": 1.5,
  "layers": [
    {"speed": 0.5, "overlap": 0.0},
    {"speed": 2.0, "overlap": 0.1}
  ],
  "tile swap seconds": [8, 12],
  "archive directory": "C:/Pictures/archive",
  "tile transition": {"type": "random", "seconds": 1.0},
  "tile video": {"max loaded videos": 3},
  "tile maze": {"count": 0},
  "tile shaders": {
    "hue rotate": {"enabled": true, "chance": 0.15},
    "water drop": {"enabled": false}
  }
}
```

Turning effects off:

| Setting | Turns off |
|---|---|
| `"renderer": "raster"` | Every GPU effect |
| `"tile shaders": {"enabled": false}` | All tile shaders |
| `"tile shaders": {"hue rotate": {"enabled": false}}` | One tile shader (or set its `"chance"` to 0) |
| `"tile transition": {"use shaders": false}` | GPU transitions; the QPainter versions are used instead |
| `"tile transition": {"type": "none"}` | Transitions; swaps happen instantly |
| `"tile swap seconds": 0` | Swapping |

### Tests

```
.venv\Scripts\python -m pytest
```

The tests run offscreen, where there's no OpenGL, so the GL tests are skipped. To run them too, use the desktop platform:

```
$env:QT_QPA_PLATFORM='windows'; .venv\Scripts\python -m pytest
```

## Future features
- More tile shaders and shader transitions
- Text composing with the ability to fetch text from external sources
- HTML composing (this seems like a stretch)
- AI image generation for automated tile generation
- Support for multiple monitors with independent scrolling
- Ability to disable scrolling and compose a static scene

## License

GPL-3.0. See `LICENSE`.
