import logging
import threading

from PySide6 import QtCore
from PySide6.QtCore import QUrl, QObject, QThread, Slot
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink, QVideoFrame

from src.Tile import Tile, MetaTile

VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".avi"}

from PySide6.QtGui import QPainter, QImage

# Keeps worker threads alive until they have fully finished, even after their tile was removed.
_retiring_workers: set = set()


class _FrameBuffer:
    """Thread-safe holder for the latest frame, shared by the worker thread and the tile.

    It deliberately does not reference the tile, so frames never have to be delivered to a
    TileVideo that might be in the middle of being destroyed.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._image = QImage()
        self._closed = False

    def set(self, img: QImage) -> None:
        with self._lock:
            if not self._closed:
                self._image = img

    def get(self) -> QImage:
        with self._lock:
            return self._image

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._image = QImage()


class _FrameProcessor(QObject):
    """Lives in the worker thread so that its slot runs there (not in the GUI thread)."""

    def __init__(self, worker: "TileVideoWorkerThread"):
        super().__init__()
        self._worker = worker

    @Slot(QVideoFrame)
    def process_frame(self, frame: QVideoFrame):
        """Converts an incoming QVideoFrame into a scaled QImage."""
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return

        target_size = self._worker.target_size()
        if target_size.isEmpty() or img.size() == target_size:
            # If the dimensions match, avoid the expensive scaling operation
            scaled = img
        else:
            # Use FastTransformation to reduce CPU load during scaling
            scaled = img.scaled(
                target_size,
                QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                QtCore.Qt.TransformationMode.FastTransformation
            )
        self._worker.frame_buffer.set(scaled)


class TileVideoWorkerThread(QThread):
    """Plays a video in its own thread and publishes scaled frames into a _FrameBuffer."""

    def __init__(self, video_filename: str, target_size: QtCore.QSize, frame_buffer: _FrameBuffer, parent=None):
        super().__init__(parent)
        self._video_filename = video_filename
        self._target_size = QtCore.QSize(target_size)
        self._size_lock = threading.Lock()
        self.frame_buffer = frame_buffer

    def target_size(self) -> QtCore.QSize:
        with self._size_lock:
            return QtCore.QSize(self._target_size)

    def set_target_size(self, size: QtCore.QSize):
        with self._size_lock:
            self._target_size = QtCore.QSize(size)

    def run(self):
        # All multimedia objects are created here so they live in (and are driven by) this thread's event loop.
        player = QMediaPlayer()
        video_sink = QVideoSink()
        processor = _FrameProcessor(self)

        player.setVideoSink(video_sink)
        video_sink.videoFrameChanged.connect(processor.process_frame)

        player.setSource(QUrl.fromLocalFile(self._video_filename))
        player.setLoops(-1)
        player.play()

        self.exec()  # Runs until quit() is called

        # Clean up in the same thread that created the objects
        player.stop()
        try:
            video_sink.videoFrameChanged.disconnect(processor.process_frame)
        except (TypeError, RuntimeError):
            pass
        player.setSource(QUrl())
        player.setVideoSink(None)
        player.deleteLater()
        video_sink.deleteLater()
        processor.deleteLater()
        # Flush the deferred deletes before the event loop goes away
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


class TileVideo(Tile):
    """Holds the pixmap for a tile and provides a generic draw method."""

    def __init__(self, meta_tile: MetaTile, x: float, h: int, video_filename: str, ):
        super().__init__(meta_tile=meta_tile, x=x, h=h)

        self._videoFilePath = video_filename
        logging.debug("Load and play video %s", video_filename)

        self._frame_buffer = _FrameBuffer()
        self._removed = False

        # Playback and frame scaling happen in a separate thread
        self.worker = TileVideoWorkerThread(video_filename, self._target_size(), self._frame_buffer)
        self.worker.setObjectName("TileVideoWorkerThread")
        self.worker.start()

    @property
    def scaled_img(self) -> QImage:
        """The most recent scaled video frame (null image if none yet)."""
        return self._frame_buffer.get()

    def _target_size(self) -> QtCore.QSize:
        return QtCore.QSize(self.width, self.height)

    def set_height(self, target_h: int):
        super().set_height(target_h)
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.set_target_size(self._target_size())

    def draw(self, painter: QPainter) -> None:
        img = self._frame_buffer.get()
        if img.isNull():
            return
        painter.drawImage(int(self.x), 0, img)

    def removed(self):
        if self._removed:
            return
        self._removed = True
        logging.debug("TileVideo removed using resource %s", self.meta_tile.resource_filename)

        # Drop any frames still being produced; nothing refers back to this tile.
        self._frame_buffer.close()

        worker = self.worker
        self.worker = None
        if worker is not None:
            # Don't block the GUI thread: let the worker clean up and finish on its own
            _retiring_workers.add(worker)

            def _on_finished(w=worker):
                _retiring_workers.discard(w)
                w.deleteLater()

            worker.finished.connect(_on_finished)
            worker.quit()