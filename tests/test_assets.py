"""assets.json parsing, the image/video scanners and the tile type registry."""

import json

import pytest
from PIL import Image

from src.AssetScanner import AssetScanner, read_asset_sources
from src.MediaCache import MediaCache
from src.Tile import TileContext
from src.TileImage import TileImageScanner
from src.TileTypes import TILE_TYPES, create_tile
from src.TileVideo import TileVideoScanner
from helpers import meta


def posix(path) -> str:
    return str(path).replace("\\", "/")


# ----------------------------------------------------------------------
# assets.json
# ----------------------------------------------------------------------
def write_assets(tmp_path, data) -> str:
    path = tmp_path / "assets.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_sources_and_legacy_keys(tmp_path):
    pics, vids = tmp_path / "pics", tmp_path / "vids"
    pics.mkdir()
    vids.mkdir()
    path = write_assets(tmp_path, {
        "sources": [{"type": "maze", "path": posix(pics)}],
        "image_paths": [posix(pics)],
        "video_paths": [str(vids)],  # backslashes are normalized
    })
    assert read_asset_sources(path) == [
        ("maze", posix(pics)),
        ("image", posix(pics)),
        ("video", posix(vids)),
    ]


def test_disabled_missing_and_invalid_sources_are_skipped(tmp_path):
    pics = tmp_path / "pics"
    pics.mkdir()
    path = write_assets(tmp_path, {
        "sources": ["not an object", {"type": "image"}, {"path": posix(pics)}, {"type": 3, "path": posix(pics)}],
        "image_paths": ["#" + posix(pics), posix(tmp_path / "missing"), 42],
    })
    assert read_asset_sources(path) == []


@pytest.mark.parametrize("contents", [None, "{broken", "[]"])
def test_missing_or_invalid_assets_file(tmp_path, contents):
    path = tmp_path / "assets.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    assert read_asset_sources(str(path)) == []


# ----------------------------------------------------------------------
# Image scanner
# ----------------------------------------------------------------------
@pytest.fixture
def media_cache(tmp_path):
    return MediaCache(str(tmp_path / "cache" / "media.json"))


def make_image(folder, name, size=(300, 200), sidecar=None):
    Image.new("RGB", size, "red").save(folder / name)
    if sidecar is not None:
        stem = name.rsplit(".", 1)[0]
        (folder / f"{stem}.json").write_text(
            sidecar if isinstance(sidecar, str) else json.dumps(sidecar), encoding="utf-8")


def scan_images(folder, media_cache):
    return {m.resource_filename.rsplit("/", 1)[1]: m for m in TileImageScanner().scan(media_cache, posix(folder))}


def test_image_scan_reads_sizes_and_filters_extensions(tmp_path, media_cache):
    make_image(tmp_path, "a.png", (300, 200))
    make_image(tmp_path, "b.JPG", (64, 48))
    make_image(tmp_path, "c.bmp", (10, 20))
    (tmp_path / "notes.txt").write_text("not an image")
    found = scan_images(tmp_path, media_cache)
    assert sorted(found) == ["a.png", "b.JPG", "c.bmp"]
    assert (found["a.png"].width, found["a.png"].height) == (300, 200)
    assert found["a.png"].type == "image"
    assert found["a.png"].weight == 10000.0  # default weight without a sidecar
    # Sizes are cached for the next startup
    assert media_cache.get_dimensions(found["b.JPG"].resource_filename) == (64, 48)


def test_sidecar_weight_and_dimensions(tmp_path, media_cache):
    make_image(tmp_path, "a.png", (300, 200), sidecar={"weight": 5, "width": 1920, "height": 1080})
    a = scan_images(tmp_path, media_cache)["a.png"]
    assert a.weight == 5
    assert (a.width, a.height) == (1920, 1080)  # the sidecar wins over the file


@pytest.mark.parametrize("sidecar", [{"weight": 5}, {"weight": 5, "width": "x", "height": 0}])
def test_sidecar_without_usable_dimensions_falls_back_to_the_file(tmp_path, media_cache, sidecar):
    make_image(tmp_path, "a.png", (300, 200), sidecar=sidecar)
    a = scan_images(tmp_path, media_cache)["a.png"]
    assert a.weight == 5
    assert (a.width, a.height) == (300, 200)


def test_bad_sidecar_json_defaults_weight_to_1(tmp_path, media_cache):
    make_image(tmp_path, "a.png", (300, 200), sidecar="{broken")
    a = scan_images(tmp_path, media_cache)["a.png"]
    assert a.weight == 1
    assert (a.width, a.height) == (300, 200)


def test_unreadable_image_is_skipped(tmp_path, media_cache):
    (tmp_path / "broken.png").write_bytes(b"not a png")
    make_image(tmp_path, "ok.png")
    assert list(scan_images(tmp_path, media_cache)) == ["ok.png"]


@pytest.mark.parametrize("scanner", [TileImageScanner, TileVideoScanner])
def test_missing_folder_returns_empty_list(tmp_path, media_cache, scanner):
    assert scanner().scan(media_cache, posix(tmp_path / "missing")) == []


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------
def test_registry_entries_are_consistent():
    assert set(TILE_TYPES) == {"image", "video", "maze", "3d pipes"}
    for name, cls in TILE_TYPES.items():
        assert cls.TYPE == name
        assert issubclass(cls.SCANNER, AssetScanner)


def test_create_tile_uses_the_registry(tile_type):
    Fake = tile_type("fake")
    tile = create_tile(meta("m"), 5.0, 50, TileContext())
    assert isinstance(tile, Fake)
    assert (tile.x, tile.height, tile.width) == (5.0, 50, 50)
    assert create_tile(meta("m", type="unknown"), 0, 50, TileContext()) is None
