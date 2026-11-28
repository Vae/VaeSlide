"""Plain helpers shared by the tests (fixtures live in conftest.py)."""

import numpy as np
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from src import GLSupport
from src.HorizontalStrip import HorizontalStrip, StripConfig
from src.Tile import MetaTile, TileContext


def meta(name: str, width: int = 100, height: int = 100, type: str = "fake", weight: float = 1.0) -> MetaTile:
    """A MetaTile for tests."""
    return MetaTile(weight=weight, type=type, width=width, height=height,
                    resource_filename=name, json_filename="")


def make_strip(metas, width=1000, height=100, gap=0, swap=(0.0, 0.0), margin=0.0) -> HorizontalStrip:
    """A strip laid out at width x height, with the given gap and swap settings."""
    strip = HorizontalStrip(TileContext(), StripConfig(gap_px=gap, swap_seconds=swap, swap_edge_margin=margin))
    strip.set_meta_tiles(metas)
    strip.resize(width, height)
    return strip


# ----------------------------------------------------------------------
# Rendering through OpenGL vs. QPainter (GL tests skip without a context)
# ----------------------------------------------------------------------
class GLCanvas(QOpenGLWidget):
    """Runs `paint(painter)` inside paintGL on a black background, like GLEndlessScrollWidget does."""

    def __init__(self, paint, size, origin=(0, 0)):
        super().__init__()
        self._paint = paint
        self._origin = origin
        self.resize(*size)

    def paintGL(self):  # noqa: N802
        GLSupport.collect_garbage()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        painter.translate(*self._origin)
        self._paint(painter)
        painter.end()


def render_gl(paint, size, origin=(0, 0)) -> QImage:
    """`paint(painter)` drawn through the OpenGL paint engine."""
    canvas = GLCanvas(paint, size, origin)
    try:
        return canvas.grabFramebuffer().convertToFormat(QImage.Format.Format_ARGB32)
    finally:
        canvas.close()
        canvas.deleteLater()


def render_raster(paint, size, origin=(0, 0)) -> QImage:
    """`paint(painter)` drawn on a QImage (no GL: shaders fall back)."""
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.translate(*origin)
    paint(painter)
    painter.end()
    return image


def pixels(image: QImage) -> np.ndarray:
    """(h, w, 4) int16 array of an ARGB32 image, channels in BGRA memory order."""
    data = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    return data[:, :image.width() * 4].reshape(image.height(), image.width(), 4).astype(np.int16)


def mismatch(a: QImage, b: QImage, tolerance: int = 8) -> float:
    """Fraction of pixels whose channels differ by more than `tolerance`."""
    return float((np.abs(pixels(a) - pixels(b)).max(axis=2) > tolerance).mean())
