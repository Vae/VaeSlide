import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "..", "settings.json")


class Settings:
    """
    User settings loaded from settings.json.

    Values missing from the file fall back to DEFAULTS, so a partial or
    missing settings.json is always valid.
    """

    DEFAULTS: dict[str, Any] = {
        "archive on middle click": True,
        "archive directory": None,  # must be set in settings.json for archiving to work
        "global speed": 1.0,  # multiplier applied to every layer's speed
        "tile gap": -1,       # px between tiles (0 = seamless, negative = overlap)
        "wheel speed step": 0.25,  # px/frame added or removed per mouse wheel notch
        "tile swap seconds": 10.0,  # on-screen time before a tile swaps to a same-aspect asset; <= 0 disables
        "tile swap edge margin": 100,
        "asset loader threads": 2,  # worker threads that decode and scale images (minimum 1)  # px; tiles closer than this to a screen edge (or off it) aren't swapped
        # Parallax layers, back to front. speed is in px/frame; overlap is a
        # fraction of screen height covered over the previous layer.
        "layers": [
            {"speed": 0.5, "overlap": 0.0},
            {"speed": 1.25, "overlap": 0.0},
            {"speed": 2.0, "overlap": 0.0},
        ],
    }

    def __init__(self, path: str = DEFAULT_SETTINGS_PATH):
        self.path = os.path.abspath(path)
        self._values: dict[str, Any] = dict(self.DEFAULTS)
        self.reload()

    def reload(self) -> None:
        """(Re)read the settings file, keeping defaults for missing keys."""
        self._values = dict(self.DEFAULTS)
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
        self._values.update(data)
        logger.debug("Loaded settings from %s", self.path)

    def get(self, key: str, default: Any = None) -> Any:
        """Return a setting; `default` is used only if the key has no built-in default either."""
        return self._values.get(key, default)

    def get_number(self, key: str, cast: type = float) -> Any:
        """Return a numeric setting converted with `cast`, falling back to DEFAULTS if invalid."""
        value = self._values.get(key)
        try:
            if isinstance(value, bool):
                raise TypeError("booleans are not numbers")
            return cast(value)
        except (TypeError, ValueError):
            default = self.DEFAULTS[key]
            logger.error("Setting \"%s\" must be a number, got %r. Using default %r.", key, value, default)
            return cast(default)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __contains__(self, key: str) -> bool:
        return key in self._values


_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Shared Settings instance, loaded on first use."""
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance
