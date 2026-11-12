import json
import os
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class MediaCache:
    """
    Persistent cache for media dimensions (width, height) to avoid 
    expensive file loading during startup.
    """

    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self._cache: dict[str, Tuple[int, int]] = {}
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.debug("Loaded media cache from %s", self.cache_file)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load media cache %s: %s. Starting with empty cache.", self.cache_file, e)
                self._cache = {}
        else:
            self._cache = {}

    def save_cache(self):
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=4)
            logger.debug("Saved media cache to %s", self.cache_file)
        except OSError as e:
            logger.error("Failed to save media cache %s: %s", self.cache_file, e)

    def get_dimensions(self, path: str) -> Optional[Tuple[int, int]]:
        """Returns (width, height) if found in cache, else None."""
        # Use absolute path to ensure consistency
        abs_path = os.path.abspath(path)
        return self._cache.get(abs_path)

    def add_dimensions(self, path: str, width: int, height: int):
        """Adds dimensions to the cache and persists it."""
        abs_path = os.path.abspath(path)
        self._cache[abs_path] = (width, height)

    def clear(self):
        """Clears the cache."""
        self._cache = {}
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
        logger.info("Media cache cleared.")
