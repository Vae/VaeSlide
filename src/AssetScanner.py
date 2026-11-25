import json
import logging
import os
from typing import List, Optional, Tuple

from src.MediaCache import MediaCache
from src.Tile import MetaTile

logger = logging.getLogger(__name__)

DEFAULT_ASSETS_PATH = os.path.join(os.path.dirname(__file__), "..", "assets.json")

# Old assets.json keys -> tile type, still read alongside "sources"
_LEGACY_KEYS = {"image_paths": "image", "video_paths": "video"}


class AssetScanner:
    """
    Builds the MetaTiles for one tile type, usually by scanning a folder.

    Each Tile subclass names its scanner in its SCANNER attribute.
    """

    # False for procedural types (e.g. mazes) that create entries from
    # settings alone; they are added once at startup without a folder.
    NEEDS_PATH = True

    def scan(self, media_cache: MediaCache, path: Optional[str]) -> List[MetaTile]:
        raise NotImplementedError("Subclasses must implement scan().")


def read_asset_sources(assets_path: str = DEFAULT_ASSETS_PATH) -> List[Tuple[str, str]]:
    """
    Read (tile type, folder) pairs from assets.json.

    Format:
        {"sources": [{"type": "image", "path": "C:/pics"}, ...]}
    The older {"image_paths": [...], "video_paths": [...]} lists are also read.
    Paths starting with "#" are disabled. Folders that don't exist are skipped.
    """
    try:
        with open(assets_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Could not load %s: %s", os.path.abspath(assets_path), e)
        return []
    if not isinstance(data, dict):
        logger.error("%s must contain a JSON object.", os.path.abspath(assets_path))
        return []

    raw: List[Tuple[object, object]] = []
    for entry in data.get("sources", []):
        if isinstance(entry, dict):
            raw.append((entry.get("type"), entry.get("path")))
        else:
            logger.error("Skipping invalid assets.json source %r (expected {\"type\", \"path\"}).", entry)
    for key, tile_type in _LEGACY_KEYS.items():
        raw.extend((tile_type, path) for path in data.get(key, []))

    sources = []
    for tile_type, path in raw:
        if not isinstance(tile_type, str) or not isinstance(path, str):
            logger.error("Skipping invalid assets.json source: type %r, path %r.", tile_type, path)
            continue
        if path.startswith("#"):
            continue  # disabled
        if not os.path.isdir(path):
            logger.warning("Skipping %s folder that doesn't exist: %s", tile_type, path)
            continue
        sources.append((tile_type, path.replace("\\", "/")))
    return sources
