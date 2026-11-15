from src.MediaCache import MediaCache
from src.Tile import MetaTile


class AssetManager:
    """Manages loading and caching of assets."""
    def load_assets(self, media_cache: MediaCache, path_to_assets: str) -> list[MetaTile]:
        raise NotImplementedError("Subclasses must implement load_assets method.")