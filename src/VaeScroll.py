#!/usr/bin/env python3
"""
Endless Scroll Screensaver
==========================
A PySide6-based screensaver featuring seamless horizontal parallax scrolling.
Asset folders come from assets.json and settings from settings.json.

Usage (from the repo root):
    python -m src.VaeScroll             # fullscreen across all monitors
    python -m src.VaeScroll --window    # windowed mode for testing
    python -m src.VaeScroll --speed 2.0 # override "global speed" from settings.json
"""

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QColor, QSurfaceFormat, QOffscreenSurface, QOpenGLContext
from PySide6.QtCore import QTimer, Qt
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from src import Diagnostics, GLSupport
from src.AssetLoader import AssetLoader
from src.AssetScanner import read_asset_sources
from src.FrameClock import FrameClock, REFERENCE_FPS
from src.ImageCache import ImageCache
from src.MediaCache import MediaCache
from src.ScrollLayer import ScrollLayer, LayerConfig, parse_layer_configs
from src.Settings import Settings, get_settings
from src.Tile import MetaTile, TileContext
from src.TileTypes import TILE_TYPES

logger = logging.getLogger(__name__)

# glGetString names (PySide6 has no GL enum constants without PyOpenGL)
GL_VENDOR, GL_RENDERER, GL_VERSION = 0x1F00, 0x1F01, 0x1F02

def load_layer_configs() -> List[LayerConfig]:
    """Layer configs from settings.json, falling back to the built-in defaults."""
    configs = parse_layer_configs(get_settings().get("layers"))
    if not configs:
        logger.error("No valid layers in settings.json; using default layers.")
        configs = parse_layer_configs(Settings.DEFAULTS["layers"])
    return configs


class FrameStats:
    """Logs the average frame interval and paint time every few seconds (--frame-stats)."""

    def __init__(self, label: str, every_seconds: float = 5.0):
        self._label = label
        self._every = every_seconds
        self._window_start: Optional[float] = None  # set by the first frame

    def record(self, paint_start: float, paint_end: float, late_frames: int = 0) -> None:
        """late_frames: the FrameClock's running count of missed vsyncs."""
        if self._window_start is None:
            self._start_window(paint_start, late_frames)
            return
        self._frames += 1
        paint = paint_end - paint_start
        self._paint_total += paint
        self._paint_max = max(self._paint_max, paint)
        self._interval_max = max(self._interval_max, paint_start - self._last_start)
        self._last_start = paint_start
        elapsed = paint_start - self._window_start
        if elapsed >= self._every:
            # Paint time is CPU time only; with OpenGL the GPU work finishes later, so fps matters more
            logger.info("[%s] %.1f fps, paint avg %.2f ms, max %.2f ms, frame interval max %.1f ms, "
                        "late frames %d",
                        self._label, self._frames / elapsed,
                        1000 * self._paint_total / self._frames, 1000 * self._paint_max,
                        1000 * self._interval_max, late_frames - self._late_at_start)
            self._start_window(paint_start, late_frames)

    def _start_window(self, now: float, late_frames: int) -> None:
        self._window_start = now
        self._last_start = now
        self._late_at_start = late_frames
        self._frames = 0
        self._paint_total = 0.0
        self._paint_max = 0.0
        self._interval_max = 0.0


class ScrollWidgetMixin:
    """
    The main screensaver widget's behaviour, shared by the raster and OpenGL widgets.

    Draws several horizontally scrolling layers at different speeds to
    create a parallax effect. Each layer has its own tiles and overlaps
    the previous layer by a percentage of the screen height.

    Must come before the Qt base class, e.g. ``class X(ScrollWidgetMixin, QWidget)``.
    Subclasses call ``_paint_scene(painter)`` from their own paint hook.
    """

    RENDERER = ""
    VSYNCED = False  # whether frames are presented on vsync, so frame times can be snapped to it

    def __init__(self, fullscreen: bool, speed_scale: Optional[float] = None,
                 layers: Optional[List[LayerConfig]] = None, frame_stats: bool = False):
        super().__init__()
        self._frame_stats = FrameStats(self.RENDERER) if frame_stats else None

        self._fullscreen = fullscreen
        self._settings = get_settings()
        # Multiplier on every layer's speed; an explicit argument overrides settings.json
        self._speed_scale = speed_scale if speed_scale is not None else self._settings.get_number("global speed")
        self._image_cache = ImageCache()
        # Image decoding and scaling run on this pool so the GUI thread never blocks on disk
        self._asset_loader = AssetLoader(self._image_cache,
                                         self._settings.get_number("asset loader threads", int))
        # Covers every way out (window close, any-key quit) so queued loads are dropped
        QApplication.instance().aboutToQuit.connect(self._asset_loader.shutdown)
        self._mediaCache = MediaCache(os.path.join(os.path.dirname(__file__), "..", ".media_cache.json"))

        # Master list; each layer receives its own copy in build_layers()
        self._meta_tiles: List[MetaTile] = []

        # Gap and swap settings come from strip_config() in HorizontalStrip.py
        tile_ctx = TileContext(asset_loader=self._asset_loader)
        self._layers: List[ScrollLayer] = [
            ScrollLayer(cfg, tile_ctx) for cfg in (layers or load_layer_configs())
        ]

        # Background color visible in gaps (if any)
        self._bg_color = QColor("#0a0a0a")

        self._configure_window()

        # Animation is time-based: each frame advances by the time it will be on screen.
        # What drives the frames differs per renderer; see _start_animation.
        self._clock = FrameClock(snap=self.VSYNCED)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self._start_animation()

        # Make the widget react to any input
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Window / monitor setup
    # ------------------------------------------------------------------
    def _configure_window(self) -> None:
        if self._fullscreen:
            screens = QApplication.screens()
            if screens:
                # virtualGeometry covers the union of all monitors
                vg = screens[0].virtualGeometry()
                self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
                self.setGeometry(vg)
            else:
                self.showFullScreen()
        else:
            self.setWindowTitle("Endless Scroll — Test Mode")
            self.resize(1280, 720)

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------
    def build_layers(self) -> None:
        """Give every layer its own tiles. Call after loading assets/videos."""
        for layer in self._layers:
            layer.set_meta_tiles(self._meta_tiles)
        self._layout_layers()

    def _layout_layers(self) -> None:
        """
        Stack the layers vertically so that layer i overlaps layer i-1 by
        overlap_i * screen height, and the whole stack exactly fills the screen.
        """
        screen_w = self.width()
        screen_h = self.height()
        if screen_w <= 0 or screen_h <= 0 or not self._layers:
            return

        count = len(self._layers)
        total_overlap = sum(l.config.overlap for l in self._layers[1:])
        band_h = max(1, int(screen_h * (1.0 + total_overlap) / count))

        y = 0.0
        for i, layer in enumerate(self._layers):
            if i > 0:
                y += band_h - layer.config.overlap * screen_h
            layer.set_geometry(int(y), screen_w, band_h)

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------
    def _refresh_period(self) -> float:
        """Seconds per display refresh of the screen the window is on (60 Hz if unknown)."""
        screen = self.screen()
        rate = screen.refreshRate() if screen is not None else 0.0
        return 1.0 / (rate if rate >= 20.0 else REFERENCE_FPS)

    def _start_animation(self) -> None:
        """Raster: a precise timer at the refresh rate. Not vsynced, so steps use real time."""
        self._timer.start(max(1, round(1000 * self._refresh_period())))

    def _on_timer(self) -> None:
        self._advance()

    def _advance(self) -> None:
        """Step the animation by the time since the last frame and repaint."""
        seconds = self._clock.advance(time.perf_counter(), self._refresh_period())
        self._tick(seconds * REFERENCE_FPS)

    def _tick(self, frames: float = 1.0) -> None:
        """Advance everything by `frames` reference frames (1/60 s each) and repaint."""
        # Hand finished background loads to their tiles before moving anything
        self._asset_loader.process_completed()
        for layer in self._layers:
            layer.tick(self._speed_scale, frames)
        self.update()  # trigger repaint

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def _paint_scene(self) -> None:
        """Paint every layer onto this widget; called from the subclass's paint hook."""
        start = time.perf_counter()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self._bg_color)

        # Back to front
        for layer in self._layers:
            layer.draw(painter)

        painter.end()
        if self._frame_stats:
            self._frame_stats.record(start, time.perf_counter(), self._clock.late_frames)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Re-scale and re-flow every layer when the window is resized."""
        super().resizeEvent(event)
        self._layout_layers()

    # ------------------------------------------------------------------
    # Close on any input (screensaver behaviour)
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Hit test all layers from front to back
        for layer in reversed(self._layers):
            tile = layer.hit_test(event.position().x(), event.position().y())
            if tile and event.button() == Qt.LeftButton:
                logger.info("Clicked tile: %s", tile.describe())
                return
            if (tile and event.button() == Qt.MiddleButton
                    and self._settings.get("archive on middle click")):
                if Path(tile.meta_tile.resource_filename).is_file():
                    self._archive(tile.meta_tile.resource_filename)
                else:
                    # e.g. procedural maze tiles have no file; don't fall through to quitting
                    logger.info("Tile %s has no file to archive.", tile.meta_tile.resource_filename)
                return

        self._shutdown()

    def _archive(self, filename: str) -> None:
        """Move an asset into the archive directory configured in settings.json."""
        archive_dir = self._settings.get("archive directory")
        if not archive_dir:
            logger.warning("Cannot archive %s: no \"archive directory\" set in settings.json.", filename)
            return
        try:
            # Without this, moving into a missing directory would rename the file instead
            os.makedirs(archive_dir, exist_ok=True)
            shutil.move(filename, archive_dir)
            logger.info("Archived asset: %s -> %s", filename, archive_dir)
        except (OSError, shutil.Error) as e:
            logger.error("Failed to archive %s to %s: %s", filename, archive_dir, e)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Wheel up speeds up the layer under the cursor; wheel down slows it down."""
        notches = event.angleDelta().y() / 120.0  # 120 = one standard wheel notch
        if notches == 0:
            return

        y = event.position().y()
        # Front to back, so the visible layer wins where layers overlap
        for index in reversed(range(len(self._layers))):
            layer = self._layers[index]
            if layer.contains_y(y):
                old_speed = layer.speed
                layer.adjust_speed(notches * self._settings.get_number("wheel speed step"))
                logger.info("Layer %d speed: %.2f -> %.2f px/frame (x%.2f global speed)",
                            index, old_speed, layer.speed, self._speed_scale)
                return

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._shutdown()

    def _shutdown(self) -> None:
        self._timer.stop()
        QApplication.quit()

    # ------------------------------------------------------------------
    # Asset loading (call before build_layers)
    # ------------------------------------------------------------------
    def load_assets(self, tile_type: str, path: Optional[str] = None) -> None:
        """Add the MetaTiles of one tile type, scanning `path` if that type needs a folder."""
        cls = TILE_TYPES.get(tile_type)
        if cls is None or cls.SCANNER is None:
            logger.error("Unknown tile type %r (known: %s); skipping %s.", tile_type, list(TILE_TYPES), path)
            return
        self._meta_tiles.extend(cls.SCANNER().scan(self._mediaCache, path))

    def load_procedural_assets(self) -> None:
        """Add the entries of every tile type that needs no folder (e.g. mazes)."""
        for tile_type, cls in TILE_TYPES.items():
            if cls.SCANNER is not None and not cls.SCANNER.NEEDS_PATH:
                self.load_assets(tile_type)


class EndlessScrollWidget(ScrollWidgetMixin, QWidget):
    """Raster renderer: QPainter on a plain QWidget, all on the CPU."""

    RENDERER = "raster"

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._paint_scene()


class GLEndlessScrollWidget(ScrollWidgetMixin, QOpenGLWidget):
    """
    OpenGL renderer: the same QPainter drawing, but through Qt's OpenGL paint
    engine on a QOpenGLWidget. Raw GL (shaders) can be mixed in between
    painter.beginNativePainting() and endNativePainting().
    """

    RENDERER = "opengl"
    VSYNCED = True

    # The frame loop runs off frameSwapped; this timer only restarts it if frames stop
    # being presented (e.g. the window is hidden, so update() never paints)
    WATCHDOG_MS = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        surface_format = QSurfaceFormat.defaultFormat()
        surface_format.setSamples(max(0, get_settings().get_number("opengl samples", int)))
        surface_format.setSwapInterval(1)  # vsync: the swap waits for the display, which paces the loop
        self.setFormat(surface_format)
        # paintGL draws the whole frame, so Qt needn't clear or keep the old one
        self.setUpdateBehavior(QOpenGLWidget.NoPartialUpdate)

    def _start_animation(self) -> None:
        """
        One frame per display refresh: each presented frame (frameSwapped) steps the
        animation and asks for the next paint. A fixed timer can't do this: a 16 ms
        timer against a 16.7 ms refresh puts two steps into one refresh about once a
        second, which shows as a hitch.
        """
        self.frameSwapped.connect(self._on_frame_swapped)
        self._timer.start(self.WATCHDOG_MS)
        self.update()

    def _on_frame_swapped(self) -> None:
        # If the driver ignores vsync, swaps return immediately; hold the loop back
        # instead of spinning. Short enough not to matter when vsync works: the
        # compositor can return a swap a few ms after the one before it.
        period = self._refresh_period()
        wait = 0.25 * period - self._clock.since_last(time.perf_counter())
        if wait > 0:
            QTimer.singleShot(max(1, round(1000 * wait)), self._advance)
        else:
            self._advance()

    def _on_timer(self) -> None:
        if self._clock.since_last(time.perf_counter()) * 1000 >= self.WATCHDOG_MS:
            self._advance()

    def initializeGL(self) -> None:  # noqa: N802
        functions = self.context().functions()
        logger.info("OpenGL renderer: %s | %s | %s",
                    functions.glGetString(GL_VENDOR), functions.glGetString(GL_RENDERER),
                    functions.glGetString(GL_VERSION))

    def paintGL(self) -> None:  # noqa: N802
        GLSupport.collect_garbage()  # GL objects released since the last frame (context is current now)
        self._paint_scene()


def opengl_available() -> bool:
    """True when this platform can create an OpenGL context (False e.g. under QT_QPA_PLATFORM=offscreen)."""
    surface = QOffscreenSurface()
    surface.create()
    context = QOpenGLContext()
    ok = context.create() and context.makeCurrent(surface)
    if ok:
        context.doneCurrent()
    return ok


def create_widget(renderer: Optional[str] = None, **kwargs) -> ScrollWidgetMixin:
    """
    Build the widget for `renderer` ("opengl" or "raster"; default: the "renderer"
    setting). OpenGL falls back to raster when no context can be created.
    """
    renderer = (renderer or get_settings().get("renderer") or "opengl").lower()
    if renderer not in ("opengl", "raster"):
        logger.error("Unknown renderer %r (known: opengl, raster); using opengl.", renderer)
        renderer = "opengl"
    if renderer == "opengl" and not opengl_available():
        logger.warning("OpenGL is not available; falling back to the raster renderer.")
        renderer = "raster"
    cls = GLEndlessScrollWidget if renderer == "opengl" else EndlessScrollWidget
    logger.info("Using the %s renderer.", renderer)
    return cls(**kwargs)


# ======================================================================
# Entry point
# ======================================================================
def main() -> None:
    Diagnostics.install()

    parser = argparse.ArgumentParser(description="VaeSlide")
    parser.add_argument("--window", action="store_true",
                        help="Run in a window instead of fullscreen (for testing)")
    parser.add_argument("--speed", type=float, default=None,
                        help="Override \"global speed\" from settings.json (multiplier on every layer's speed)")
    parser.add_argument("--renderer", choices=("opengl", "raster"), default=None,
                        help="Override \"renderer\" from settings.json")
    parser.add_argument("--frame-stats", action="store_true",
                        help="Log fps and paint time every few seconds")
    args = parser.parse_args()

    fullscreen = not args.window

    # Temp for testing: force non-fullscreen
    fullscreen = False

    surface_format = QSurfaceFormat()
    surface_format.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface_format)

    # Force no-decoration for fullscreen
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    widget = create_widget(args.renderer, fullscreen=fullscreen, speed_scale=args.speed,
                           frame_stats=args.frame_stats)

    # Asset folders from assets.json (missing and "#"-disabled folders are skipped)
    for tile_type, path in read_asset_sources():
        widget.load_assets(tile_type, path)
    widget.load_procedural_assets()

    widget.build_layers()
    widget.show()

    print("Slideshow running; press any key to exit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
