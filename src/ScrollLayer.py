import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from PySide6.QtGui import QPainter

from src.ResourceLoader import ResourceLoader
from src.Tile import MetaTile, Tile
from src.HorizontalStrip import HorizontalStrip

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayerConfig:
    """
    speed:   scroll speed of this layer in pixels per frame.
    overlap: fraction of the screen height this layer overlaps the
             previous (further back) layer. Ignored for the first layer.
    """
    speed: float
    overlap: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayerConfig":
        """Build from a settings.json entry like {"speed": 2.0, "overlap": 0.1}."""
        return cls(speed=float(data["speed"]), overlap=float(data.get("overlap", 0.0)))


def parse_layer_configs(raw: Any) -> List[LayerConfig]:
    """
    Convert the "layers" list from settings.json into LayerConfigs.
    Invalid entries are skipped with an error.
    """
    if not isinstance(raw, list):
        logger.error("\"layers\" in settings.json must be a list, got %r.", type(raw).__name__)
        return []

    configs = []
    for i, entry in enumerate(raw):
        try:
            if not isinstance(entry, dict):
                raise TypeError("entry must be an object")
            configs.append(LayerConfig.from_dict(entry))
        except (KeyError, TypeError, ValueError) as e:
            logger.error("Skipping invalid layer %d in settings.json (%r): %s", i, entry, e)
    return configs


class ScrollLayer:
    """One parallax layer: a TileLayout, a vertical offset and a speed."""

    def __init__(self, config: LayerConfig, resource_loader: ResourceLoader, gap_px: int = -1):
        self.config = config
        self.layout = HorizontalStrip(resource_loader, gap_px)
        self.y = 0

    def set_meta_tiles(self, meta_tiles: List[MetaTile]) -> None:
        self.layout.set_meta_tiles(meta_tiles)

    def set_geometry(self, y: int, width: int, height: int) -> None:
        self.y = y
        self.layout.resize(width, height)

    def tick(self, speed_scale: float = 1.0) -> None:
        self.layout.scroll(self.config.speed * speed_scale)

    def hit_test(self, x: float, y: float) -> Optional[Tile]:
        """Check if the given (x, y) coordinates are within any tile in this layer."""
        tile = self.layout.hit_test(x, y - self.y)
        return tile

    def draw(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(0, self.y)
        self.layout.draw(painter)
        painter.restore()

    def removed(self):
        self.layout.removed()