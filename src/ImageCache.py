import logging
import threading
import time
from typing import Dict, Optional, Tuple

from PySide6.QtGui import QImage

logger = logging.getLogger(__name__)

PURGE_TIMEOUT = 15 * 60  # 15 minutes


class ImageCache:
    """
    Loads full-resolution images from disk and caches them.

    Thread-safe: called from the asset_loader worker threads. It deals in
    QImage rather than QPixmap because QPixmap may only be created on the
    GUI thread.
    """

    def __init__(self):
        logger.debug("ImageCache initialized.")
        self._lock = threading.Lock()
        self._loaded_images: Dict[str, Tuple[QImage, float]] = {}  # path -> (image, last access)

    def get_image(self, path: str) -> Optional[QImage]:
        with self._lock:
            self._purge_expired()
            entry = self._loaded_images.get(path)
            if entry is not None:
                self._loaded_images[path] = (entry[0], time.time())
                logger.debug("Cache hit for %s", path)
                return entry[0]

        # Decode outside the lock so workers can load different files in parallel.
        # Two workers may occasionally load the same file at once; that's harmless.
        image = QImage(path)
        if image.isNull():
            logger.warning("Could not load %s, skipping.", path)
            return None
        logger.debug("Successfully loaded image from %s", path)
        with self._lock:
            self._loaded_images[path] = (image, time.time())
        return image

    def _purge_expired(self) -> None:
        """Remove entries not accessed within the timeout window. Caller holds the lock."""
        now = time.time()
        expired = [path for path, (_, ts) in self._loaded_images.items() if now - ts > PURGE_TIMEOUT]
        for path in expired:
            logger.debug("Purging expired resource: %s", path)
            del self._loaded_images[path]
