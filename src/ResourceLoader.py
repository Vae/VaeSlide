import logging
from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

class ResourceLoader:
    """Loads and maintains resources from the disk."""
    def __init__(self):
        logger.debug("ResourceLoader initialized.")
        self._loaded_images = {}

    def get_pixmap(self, path):
        if path in self._loaded_images:
            logger.debug("Cache hit for %s", path)
            return self._loaded_images.get(path)
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.warning("Could not load %s, skipping.", path)
            return None
        logger.debug("Successfully loaded pixmap from %s", path)
        self._loaded_images[path] = pixmap
        return pixmap