"""
Shared fixtures.

Every test runs against its own settings (built-in defaults unless the test
overrides them), never the user's settings.json. Qt runs headless.
"""

import json
import os

# Must be set before any Qt module creates the application
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import src.Settings as settings_module
from src.Settings import Settings, reset_config_caches
from src.Tile import Tile
from src.TileTypes import TILE_TYPES
from src.TileVideo import TileVideo


@pytest.fixture(scope="session")
def qapp():
    """The QApplication; needed by tests that paint or create widgets."""
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def use_settings(tmp_path, monkeypatch):
    """
    Install isolated settings. Call the fixture with a dict to override
    values, e.g. use_settings({"tile maze": {"speed": 50}}). Without a call,
    the test sees the built-in defaults.
    """
    def install(values: dict | None = None) -> Settings:
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(values or {}), encoding="utf-8")
        settings = Settings(str(path))
        monkeypatch.setattr(settings_module, "_instance", settings)
        reset_config_caches()
        return settings

    install()
    yield install
    reset_config_caches()


@pytest.fixture(autouse=True)
def reset_video_count(monkeypatch):
    """TileVideo.active_count is process-wide; start every test at 0."""
    monkeypatch.setattr(TileVideo, "active_count", 0)


@pytest.fixture
def tile_type(monkeypatch):
    """
    Factory that registers a lightweight Tile type (no Qt, no files) in
    TILE_TYPES for the duration of the test, e.g. Fake = tile_type("fake").

    Class attributes control its behaviour: allow (can_select), multiplier
    (selection_weight_multiplier), start_ready (is_ready of new tiles).
    Every instance is recorded in `created`.
    """
    def make(name: str = "fake") -> type:
        class FakeTile(Tile):
            TYPE = name
            SCANNER = None
            allow = True
            multiplier = 1.0
            start_ready = True
            created: list = []

            def __init__(self, meta_tile, x, h, ctx):
                super().__init__(meta_tile, x, h, ctx)
                self.ready = type(self).start_ready
                self.swappable = True
                self.removed_called = False
                type(self).created.append(self)

            @classmethod
            def can_select(cls, replacing=None):
                return cls.allow

            @classmethod
            def selection_weight_multiplier(cls):
                return cls.multiplier

            def is_ready(self):
                return self.ready

            def can_swap(self):
                return self.swappable

            def removed(self):
                self.removed_called = True

        FakeTile.created = []
        FakeTile.__name__ = f"FakeTile_{name}"
        monkeypatch.setitem(TILE_TYPES, name, FakeTile)
        return FakeTile

    return make

