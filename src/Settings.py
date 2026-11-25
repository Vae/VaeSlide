import copy
import functools
import json
import logging
import os
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "..", "settings.json")


class SettingsSection:
    """
    Read access to one level of settings: the top level, or a nested dict
    such as "tile video". Values fall back to the matching defaults.
    """

    def __init__(self, values: dict[str, Any], defaults: dict[str, Any], path: str = ""):
        self._values = values
        self._defaults = defaults
        self._path = path  # e.g. "tile video", used in log messages

    def _name(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def get(self, key: str, default: Any = None) -> Any:
        """Return a setting; `default` is used only if the key has no built-in default either."""
        return self._values.get(key, default)

    def get_number(self, key: str, cast: type = float) -> Any:
        """Return a numeric setting converted with `cast`, falling back to the default if invalid."""
        value = self._values.get(key)
        try:
            if isinstance(value, bool):
                raise TypeError("booleans are not numbers")
            return cast(value)
        except (TypeError, ValueError):
            default = self._defaults[key]
            logger.error("Setting \"%s\" must be a number, got %r. Using default %r.",
                         self._name(key), value, default)
            return cast(default)

    def get_range(self, key: str) -> tuple[float, float]:
        """
        Return a [min, max] setting as a (min, max) tuple of floats. A single
        number is accepted as [n, n]. Reversed bounds are swapped. Falls back
        to the default if the value is invalid.
        """
        value = self._values.get(key)
        parsed = _parse_range(value)
        if parsed is None:
            default = self._defaults[key]
            logger.error("Setting \"%s\" must be [min, max] numbers, got %r. Using default %r.",
                         self._name(key), value, default)
            parsed = _parse_range(default)
        return parsed

    def section(self, key: str) -> "SettingsSection":
        """A nested settings dict, e.g. settings.section("tile video")."""
        return SettingsSection(self._values.get(key, {}), self._defaults.get(key, {}), self._name(key))

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __contains__(self, key: str) -> bool:
        return key in self._values


class Settings(SettingsSection):
    """
    User settings loaded from settings.json.

    Values missing from the file fall back to DEFAULTS, so a partial or
    missing settings.json is always valid. Nested dicts (sections) are merged
    key by key, so a section only needs the keys it overrides.
    """

    DEFAULTS: dict[str, Any] = {
        "archive on middle click": True,
        "archive directory": None,  # must be set in settings.json for archiving to work
        "global speed": 1.0,  # multiplier applied to every layer's speed
        "tile gap": -1,       # px between tiles (0 = seamless, negative = overlap)
        "wheel speed step": 0.25,  # px/frame added or removed per mouse wheel notch
        # [min, max] on-screen seconds before a tile swaps to a same-aspect asset.
        # Each tile picks a random time in this range (fractions allowed); max <= 0 disables
        "tile swap seconds": [8.0, 12.0],
        "tile swap edge margin": 100,  # px; tiles closer than this to a screen edge (or off it) aren't swapped
        "asset loader threads": 2,  # worker threads that decode and scale images (minimum 1)
        # Parallax layers, back to front. speed is in px/frame; overlap is a
        # fraction of screen height covered over the previous layer.
        "layers": [
            {"speed": 0.5, "overlap": 0.0},
            {"speed": 1.25, "overlap": 0.0},
            {"speed": 2.0, "overlap": 0.0},
        ],
        # Settings for video tiles (TileVideo)
        "tile video": {
            "tile swap seconds": [8.0, 12.0],  # [min, max], like the top-level one, but for video tiles
            # Video odds are multiplied by this once per video tile currently loaded.
            # 1.0 = no effect; 0.5 = each loaded video halves the odds; 0 = one video at a time
            "weight per loaded video": 1.0,
            # Hard cap on video tiles loaded at once, across all layers
            # (includes off-screen buffer tiles and pending swaps). -1 = no limit; 0 = no videos
            "max loaded videos": -1,
        },
        # Settings for procedural maze tiles (TileMaze)
        "tile maze": {
            "count": 2,          # maze entries in the selection pool; 0 = no mazes
            "weight": 1.0,       # selection weight of each entry (like an image's weight)
            "maze size": [32, 18],  # [cols, rows] in cells, 2..500 each
            # "random" or one of: recursive backtracker, prim, kruskal, aldous-broder,
            # wilson, hunt and kill, binary tree, sidewinder
            "algorithm": "random",
            "speed": 120,        # generation/solving steps per second
            "wall size": 0.35,   # wall thickness as a fraction of one cell (0.05..0.9)
            "solve": True,       # animate the solution path after generating
            "hold seconds": 5,   # minimum time a finished maze is shown before it may be swapped
            "flash seconds": 3,  # after solving, flash the solution this long; no swap until it ends
        },
    }

    def __init__(self, path: str = DEFAULT_SETTINGS_PATH):
        self.path = os.path.abspath(path)
        super().__init__(copy.deepcopy(self.DEFAULTS), self.DEFAULTS)
        self.reload()

    def reload(self) -> None:
        """(Re)read the settings file, keeping defaults for missing keys."""
        self._values = copy.deepcopy(self.DEFAULTS)
        if not os.path.exists(self.path):
            logger.info("No settings file at %s, using defaults.", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Could not load settings %s: %s. Using defaults.", self.path, e)
            return
        if not isinstance(data, dict):
            logger.error("Settings file %s must contain a JSON object. Using defaults.", self.path)
            return
        _merge(self._values, data)
        logger.debug("Loaded settings from %s", self.path)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_range(value: Any) -> Optional[tuple[float, float]]:
    """[min, max] or a single number -> (min, max) with min <= max; None if invalid."""
    if _is_number(value):
        return float(value), float(value)
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_number(v) for v in value):
        lo, hi = float(value[0]), float(value[1])
        return (lo, hi) if lo <= hi else (hi, lo)
    return None


def _merge(target: dict[str, Any], overrides: dict[str, Any], path: str = "") -> None:
    """Merge `overrides` into `target`, recursing into sections that are dicts in both."""
    for key, value in overrides.items():
        name = f"{path}.{key}" if path else key
        current = target.get(key)
        if isinstance(current, dict):
            if isinstance(value, dict):
                _merge(current, value, name)
            else:
                logger.error("Setting \"%s\" must be an object, got %r. Using defaults.", name, value)
        else:
            target[key] = value


_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Shared Settings instance, loaded on first use."""
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance


# ----------------------------------------------------------------------
# Cached per-module config
# ----------------------------------------------------------------------
_config_caches: list = []


def section_config(fn: Callable[[], T]) -> Callable[[], T]:
    """
    Decorator for a module's config accessor, e.g. `maze_config()`.

    The accessor reads and validates its settings section into a frozen
    dataclass. The decorator runs it once and returns the cached result after
    that. reset_config_caches() clears every cache made by this decorator.
    """
    cached = functools.cache(fn)
    _config_caches.append(cached)
    return cached


def reset_config_caches() -> None:
    """Forget every cached config so the next access re-reads the settings (tests, reloads)."""
    for cached in _config_caches:
        cached.cache_clear()
