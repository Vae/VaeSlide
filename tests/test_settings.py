import json

import pytest

from src.Settings import Settings, SettingsSection, get_settings, reset_config_caches, section_config


def write_settings(tmp_path, data) -> Settings:
    path = tmp_path / "s.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return Settings(str(path))


def test_missing_file_uses_defaults(tmp_path):
    settings = Settings(str(tmp_path / "nope.json"))
    assert settings.get("global speed") == Settings.DEFAULTS["global speed"]
    assert settings.section("tile maze").get("algorithm") == "random"


@pytest.mark.parametrize("contents", ["{not json", "[1, 2, 3]"])
def test_invalid_file_uses_defaults(tmp_path, contents):
    settings = write_settings(tmp_path, contents)
    assert settings.get("tile gap") == Settings.DEFAULTS["tile gap"]


def test_partial_file_overrides_only_given_keys(tmp_path):
    settings = write_settings(tmp_path, {"global speed": 2.5, "tile video": {"max loaded videos": 3}})
    assert settings.get("global speed") == 2.5
    assert settings.get("tile gap") == Settings.DEFAULTS["tile gap"]
    video = settings.section("tile video")
    assert video.get("max loaded videos") == 3
    # Other keys in the same section keep their defaults (merged key by key)
    assert video.get("weight per loaded video") == Settings.DEFAULTS["tile video"]["weight per loaded video"]


def test_section_that_is_not_an_object_keeps_defaults(tmp_path):
    settings = write_settings(tmp_path, {"tile video": 5})
    assert settings.section("tile video").get("max loaded videos") == -1


def test_defaults_are_not_mutated_by_overrides(tmp_path):
    write_settings(tmp_path, {"tile maze": {"count": 99}})
    assert Settings.DEFAULTS["tile maze"]["count"] == 2


def test_unknown_keys_are_kept(tmp_path):
    settings = write_settings(tmp_path, {"my new key": [1, 2]})
    assert settings.get("my new key") == [1, 2]
    assert "my new key" in settings


@pytest.mark.parametrize("value, expected", [
    (3, 3.0),
    (2.5, 2.5),
    ("4", 4.0),     # strings that parse as numbers are accepted
    ("abc", 1.0),   # falls back to the default
    (None, 1.0),
    (True, 1.0),    # booleans are not numbers
])
def test_get_number(value, expected):
    section = SettingsSection({"k": value}, {"k": 1.0})
    assert section.get_number("k") == expected


def test_get_number_int_cast():
    assert SettingsSection({"k": 7.9}, {"k": 1}).get_number("k", int) == 7


@pytest.mark.parametrize("value, expected", [
    ([0.5, 1.5], (0.5, 1.5)),
    ([3, 1], (1.0, 3.0)),      # reversed bounds are swapped
    (4, (4.0, 4.0)),           # a single number means a fixed value
    (2.25, (2.25, 2.25)),
    ([1], (8.0, 12.0)),        # everything below falls back to the default
    ([1, 2, 3], (8.0, 12.0)),
    ([1, "x"], (8.0, 12.0)),
    ("x", (8.0, 12.0)),
    (True, (8.0, 12.0)),
    ([True, 2], (8.0, 12.0)),
    (None, (8.0, 12.0)),
])
def test_get_range(value, expected):
    section = SettingsSection({"k": value}, {"k": [8.0, 12.0]})
    assert section.get_range("k") == expected


def test_section_config_caches_until_reset(use_settings):
    calls = []

    @section_config
    def config():
        calls.append(1)
        return get_settings().get("global speed")

    use_settings({"global speed": 3.0})
    assert config() == 3.0
    assert config() == 3.0
    assert len(calls) == 1

    use_settings({"global speed": 4.0})  # installs new settings and resets every cache
    assert config() == 4.0
    reset_config_caches()
    config()
    assert len(calls) == 3
