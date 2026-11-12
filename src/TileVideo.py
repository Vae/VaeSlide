from PySide6 import QtCore
from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

from src.Tile import Tile, MetaTile

VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".avi"}

from PySide6.QtGui import QPainter, QImage


class TileVideo(Tile):
    """Holds the pixmap for a tile and provides a generic draw method."""

    def __init__(self, meta_tile: MetaTile, x: float, h: int, video_filename: str, ):
        super().__init__(meta_tile=meta_tile, x=x, h=h)

        self._videoFilePath = video_filename

        self.scaled_img = QImage()
        self.player = QMediaPlayer()

        # Set up the video sink to intercept frames
        self.video_sink = QVideoSink()
        self.player.setVideoSink(self.video_sink)

        # Connect frame updates to our processing method
        self.video_sink.videoFrameChanged.connect(self.process_frame)

        # Load and play source
        self.player.setSource(QUrl.fromLocalFile(video_filename))
        self.player.setLoops(-1)
        self.player.play()

    def process_frame(self, frame):
        """Converts incoming QVideoFrame into a paintable QImage."""
        if frame.isValid():
            img = frame.toImage()
            if img.isNull():
                return

            target_size = QtCore.QSize(self.width, self._height)
            if img.size() == target_size:
                # If the dimensions match, avoid the expensive scaling operation
                self.scaled_img = img
            else:
                # Use FastTransformation to reduce CPU load during scaling
                self.scaled_img = img.scaled(
                    target_size,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    QtCore.Qt.TransformationMode.FastTransformation
                )

    def draw(self, painter: QPainter) -> None:
        if self.scaled_img.isNull():
            return
        painter.drawImage(int(self.x), 0, self.scaled_img)

    def removed(self):
        self.player.stop()
        self.scaled_img = None