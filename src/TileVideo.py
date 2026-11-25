import logging
import os

from PySide6 import QtCore
from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

from moviepy import VideoFileClip

from src.AssetManager import AssetManager
from src.MediaCache import MediaCache
from src.Settings import get_settings
from src.Tile import Tile, MetaTile, Weight

VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".avi"}
SETTINGS_SECTION = "tile video"  # this module's section in settings.json

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
            metaTile = MetaTile(weight=Weight(10000.0), type="video", json_filename="", resource_filename=full_path,
                                width=width,
                                height=height)
            meta_tiles.append(metaTile)

        logger.info(f"Loaded {len(video_files)} video files from {path_to_videos}")
        media_cache.save_cache()
        return meta_tiles

class TileVideo(Tile):
    """Holds the pixmap for a tile and provides a generic draw method."""

    # Number of TileVideo instances that exist and haven't been removed, across
    # all strips (each one owns a QMediaPlayer, so this tracks decoding load)
    active_count = 0
    # "tile video" -> "weight per loaded video", read once on first use
    _weight_per_loaded_video: float | None = None
    # "tile video" -> "max loaded videos", read once on first use
    _max_loaded_videos: int | None = None

    def __init__(self, meta_tile: MetaTile, x: float, h: int, video_filename: str, ):
        super().__init__(meta_tile=meta_tile, x=x, h=h)
        TileVideo.active_count += 1
        self._counted = True

        self._videoFilePath = video_filename
        self._swap_seconds = get_settings().section(SETTINGS_SECTION).get_range("tile swap seconds")

        self.scaled_img = QImage()
        self.player = QMediaPlayer()

        # Set up the video sink to intercept frames
        self.video_sink = QVideoSink()
        self.player.setVideoSink(self.video_sink)

        # Connect frame updates to our processing method
        self.video_sink.videoFrameChanged.connect(self.process_frame)

        # Track whether the video has played all the way through at least once,
        # so it isn't swapped out mid-playback (see can_swap)
        self.played_once = False
        self._last_position = 0
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_error)

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

    def _on_position_changed(self, position: int) -> None:
        # Playback loops forever (setLoops(-1)), so a full playthrough shows up
        # as the position jumping back towards the start.
        if position < self._last_position:
            if not self.played_once:
                logger.debug("Video played through once: %s", self._videoFilePath)
            self.played_once = True
        self._last_position = position

    def _on_media_status_changed(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.played_once = True
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.played_once = True  # will never play; don't block swapping

    def _on_error(self, error, error_string: str) -> None:
        logger.warning("Video error for %s: %s", self._videoFilePath, error_string)
        self.played_once = True  # don't keep a broken video on screen forever

    @classmethod
    def max_loaded_videos(cls) -> int:
        """"tile video" -> "max loaded videos"; negative means no limit. Read once."""
        if cls._max_loaded_videos is None:
            TileVideo._max_loaded_videos = get_settings().section(SETTINGS_SECTION).get_number(
                "max loaded videos", int)
        return cls._max_loaded_videos

    @classmethod
    def can_select(cls, replacing: Tile | None = None) -> bool:
        cap = cls.max_loaded_videos()
        if cap < 0:
            return True
        count = TileVideo.active_count
        if isinstance(replacing, TileVideo) and replacing._counted:
            count -= 1  # a video -> video swap frees its own slot
        return count < cap

    @classmethod
    def selection_weight_multiplier(cls) -> float:
        """
        weight_per_loaded_video ** active_count: each video already loaded
        multiplies the chance of picking another video by the setting.
        1.0 leaves weights unchanged; 0.5 halves the odds per loaded video.
        """
        if cls._weight_per_loaded_video is None:
            factor = get_settings().section(SETTINGS_SECTION).get_number("weight per loaded video")
            if factor < 0:
                logger.error("\"%s.weight per loaded video\" must be >= 0, got %r. Using 0.",
                             SETTINGS_SECTION, factor)
                factor = 0.0
            TileVideo._weight_per_loaded_video = factor
        return cls._weight_per_loaded_video ** TileVideo.active_count

    def swap_seconds(self, default: tuple[float, float]) -> tuple[float, float]:
        return self._swap_seconds  # from settings.json "tile video" section

    def can_swap(self) -> bool:
        return self.played_once

    def is_ready(self) -> bool:
        return self.scaled_img is not None and not self.scaled_img.isNull()

    def draw(self, painter: QPainter) -> None:
        if self.scaled_img is None or self.scaled_img.isNull():
            return
        painter.drawImage(int(self.x), 0, self.scaled_img)

    def removed(self):
        if self._counted:
            self._counted = False
            TileVideo.active_count -= 1
        self.player.stop()
        self.scaled_img = None