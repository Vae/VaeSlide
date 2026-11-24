import logging
import queue
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from src.ResourceLoader import ResourceLoader

logger = logging.getLogger(__name__)

ImageCallback = Callable[[Optional[QImage]], None]


class AssetLoader:
    """
    Loads and scales images on a pool of "asset_loader" worker threads.

    Callers on the GUI thread call request_scaled_image(), which returns
    immediately. Workers decode and scale the image. The callback runs later
    on the GUI thread, inside process_completed(), which the widget calls
    once per frame. The callback gets the scaled QImage, or None if loading
    failed.
    """

    def __init__(self, resource_loader: ResourceLoader, num_threads: int):
        self._resource_loader = resource_loader
        self._num_threads = max(1, int(num_threads))
        self._executor = ThreadPoolExecutor(max_workers=self._num_threads,
                                            thread_name_prefix="asset_loader")
        # (callback, future) pairs finished by workers, waiting for the GUI thread
        self._completed: "queue.SimpleQueue[tuple[ImageCallback, Future]]" = queue.SimpleQueue()
        self._shut_down = False
        logger.info("AssetLoader started with %d asset_loader thread(s).", self._num_threads)

    # ------------------------------------------------------------------
    # GUI thread API
    # ------------------------------------------------------------------
    def request_scaled_image(self, path: str, height: int, callback: ImageCallback,
                             transform: Optional[Callable[[QImage], QImage]] = None) -> None:
        """
        Load `path` and scale it to `height` in the background.
        `transform`, if given, also runs on the worker thread after scaling.
        """
        if self._shut_down:
            return
        future = self._executor.submit(self._load_scaled, path, height, transform)
        future.add_done_callback(lambda f: self._completed.put((callback, f)))

    def process_completed(self) -> None:
        """Deliver finished loads to their callbacks. Call from the GUI thread."""
        while True:
            try:
                callback, future = self._completed.get_nowait()
            except queue.Empty:
                return
            if future.cancelled():
                continue
            exc = future.exception()
            if exc is not None:
                logger.error("Asset load failed: %s", exc, exc_info=exc)
                callback(None)
            else:
                callback(future.result())

    def shutdown(self) -> None:
        """Drop queued loads. Loads already running finish in the background."""
        self._shut_down = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------
    def _load_scaled(self, path: str, height: int,
                     transform: Optional[Callable[[QImage], QImage]]) -> Optional[QImage]:
        image = self._resource_loader.get_image(path)
        if image is None:
            return None
        scaled = image.scaledToHeight(height, Qt.SmoothTransformation)
        if transform is not None:
            scaled = transform(scaled)
        return scaled
