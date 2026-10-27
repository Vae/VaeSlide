import logging
import time
from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

PURGE_TIMEOUT = 15 * 60  # 15 minutes in seconds

class ResourceLoader:
    """Loads and maintains resources from the disk."""
    def __init__(self):
        logger.debug("ResourceLoader initialized.")
        self._loaded_images = {}  # path -> (QPixmap, timestamp)

    def get_pixmap(self, path):
        self._purge_expired()
        if path in self._loaded_images:
            logger.debug("Cache hit for %s", path)
            pixmap, _ = self._loaded_images[path]
            self._loaded_images[path] = (pixmap, time.time())
            return pixmap
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.warning("Could not load %s, skipping.", path)
            return None
        logger.debug("Successfully loaded pixmap from %s", path)
        self._loaded_images[path] = (pixmap, time.time())
        return pixmap

    def _purge_expired(self):
        """Remove entries that have not been accessed within the timeout window."""
        now = time.time()
        expired = [path for path, (_, ts) in self._loaded_images.items() if now - ts > PURGE_TIMEOUT]
        for path in expired:
            logger.debug("Purging expired resource: %s", path)
            del self._loaded_images[path]