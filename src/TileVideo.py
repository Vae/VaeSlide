import logging
import os

from PySide6 import QtCore
from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

from moviepy import VideoFileClip

from src.AssetManager import AssetManager
from src.MediaCache import MediaCache
from src.Tile import Tile, MetaTile, Weight

VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".avi"}

from PySide6.QtGui import QPainter, QImage
logger = logging.getLogger(__name__)

def get_video_dim(file_path: str, media_cache: MediaCache = None):
    if media_cache:
        cached = media_cache.get_dimensions(file_path)
        if cached:
            return cached

    clip = VideoFileClip(file_path)

    # Get dimensions using .size (returns tuple) or .w and .h
    width, height = clip.size
    if media_cache:
        media_cache.add_dimensions(file_path, width, height)
    return width, height

class TileVideoAssetManager(AssetManager):
    """Manages the loading and caching of video assets for TileVideo objects."""
    def load_assets(self, media_cache: MediaCache, path_to_assets: str) -> list[MetaTile]:
        path_to_videos = path_to_assets
        meta_tiles = []
        video_files = sorted(
            f for f in os.listdir(path_to_videos)
            if f.lower().endswith(".mp4") or f.lower().endswith(".avi") or f.lower().endswith(
                ".mov") or f.lower().endswith(".gif")
        )

        for video_file in video_files:
            # full_path = os.path.join(video_dir, video_file)
            full_path = os.path.join(path_to_videos, video_file).replace('\\', '/')
            width, height = get_video_dim(full_path, media_cache)
            metaTile = MetaTile(weight=Weight(500.0), type="video", json_filename="", resource_filename=full_path,
                                width=width,
                                height=height)
            meta_tiles.append(metaTile)

        logger.info(f"Loaded {len(video_files)} video files from {path_to_videos}")
        media_cache.save_cache()
        return meta_tiles

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

    def is_ready(self) -> bool:
        return self.scaled_img is not None and not self.scaled_img.isNull()

    def draw(self, painter: QPainter) -> None:
        if self.scaled_img is None or self.scaled_img.isNull():
            return
        painter.drawImage(int(self.x), 0, self.scaled_img)

    def removed(self):
        self.player.stop()
        self.scaled_img = None