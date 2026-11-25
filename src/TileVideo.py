import logging
import os
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore
from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

from PySide6.QtGui import QPainter, QImage

from moviepy import VideoFileClip

from src.AssetScanner import AssetScanner
from src.MediaCache import MediaCache
from src.Settings import get_settings, section_config
from src.Tile import Tile, TileContext, MetaTile, Weight

# .gif is played through QMediaPlayer like the others
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".avi", ".gif"}
SETTINGS_SECTION = "tile video"  # this module's section in settings.json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoConfig:
    swap_seconds: tuple[float, float]  # (min, max) on-screen seconds before a swap
    weight_per_loaded_video: float     # >= 0; 1.0 = no effect
    max_loaded_videos: int             # < 0 = no limit


@section_config
def video_config() -> VideoConfig:
    """Validated "tile video" settings, read once."""
    section = get_settings().section(SETTINGS_SECTION)
    factor = section.get_number("weight per loaded video")
    if factor < 0:
        logger.error("Setting \"%s.weight per loaded video\" must be >= 0, got %r. Using 0.",
                     SETTINGS_SECTION, factor)
        factor = 0.0
    return VideoConfig(
        swap_seconds=section.get_range("tile swap seconds"),
        weight_per_loaded_video=factor,
        max_loaded_videos=section.get_number("max loaded videos", int),
    )


def get_video_dim(file_path: str, media_cache: MediaCache = None):
    if media_cache:
        cached = media_cache.get_dimensions(file_path)
        if cached:
            return cached

    with VideoFileClip(file_path) as clip:
        width, height = clip.size
    if media_cache:
        media_cache.add_dimensions(file_path, width, height)
    return width, height


class TileVideoScanner(AssetScanner):
    """Builds video MetaTiles from a folder; dimensions come from moviepy (cached)."""

    def scan(self, media_cache: MediaCache, path: Optional[str]) -> list[MetaTile]:
        if not path or not os.path.isdir(path):
            logger.warning("Video folder '%s' not found.", path)
            return []

        video_files = sorted(
            f for f in os.listdir(path)
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
        )

        meta_tiles = []
        for video_file in video_files:
            full_path = os.path.join(path, video_file).replace('\\', '/')
            try:
                width, height = get_video_dim(full_path, media_cache)
            except Exception as exc:  # moviepy raises a variety of errors for unreadable files
                logger.warning("Skipping %s: could not read its size (%s)", full_path, exc)
                continue
            meta_tiles.append(MetaTile(weight=Weight(10000.0), type=TileVideo.TYPE, json_filename="",
                                       resource_filename=full_path, width=width, height=height))

        logger.info("Loaded %d video files from %s", len(meta_tiles), path)
        media_cache.save_cache()
        return meta_tiles


class TileVideo(Tile):
    """Plays a looping video with its own QMediaPlayer and draws the latest frame."""

    TYPE = "video"
    SCANNER = TileVideoScanner

    # Number of TileVideo instances that exist and haven't been removed, across
    # all strips (each one owns a QMediaPlayer, so this tracks decoding load)
    active_count = 0

    def __init__(self, meta_tile: MetaTile, x: float, h: int, ctx: TileContext):
        super().__init__(meta_tile, x, h, ctx)
        TileVideo.active_count += 1
        self._counted = True

        video_filename = meta_tile.resource_filename
        self._videoFilePath = video_filename

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
    def can_select(cls, replacing: Tile | None = None) -> bool:
        cap = video_config().max_loaded_videos
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
        return video_config().weight_per_loaded_video ** TileVideo.active_count

    def swap_seconds(self, default: tuple[float, float]) -> tuple[float, float]:
        return video_config().swap_seconds  # from settings.json "tile video" section

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