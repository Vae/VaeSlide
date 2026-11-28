"""Smoke test: the real widget runs its tick loop with procedural (maze) tiles only."""

import logging

import pytest

from src.ScrollLayer import LayerConfig
from src import VaeScroll
from src.VaeScroll import EndlessScrollWidget


@pytest.fixture
def widget(qapp, use_settings):
    use_settings({"tile maze": {"count": 6, "maze size": [8, 5], "speed": 500}})
    w = EndlessScrollWidget(fullscreen=False, speed_scale=1.0,
                            layers=[LayerConfig(speed=3.0), LayerConfig(speed=-2.0, overlap=0.1)])
    w._timer.stop()  # the test drives _tick() itself
    w.resize(800, 400)
    yield w
    w._asset_loader.shutdown()
    w.close()


def test_ticks_fill_every_layer(widget):
    widget.load_procedural_assets()
    widget.build_layers()
    for _ in range(120):
        widget._tick()
    widget.grab()  # paints once through paintEvent
    for layer in widget._layers:
        tiles = layer.layout.tiles
        assert tiles and all(t.meta_tile.type in ("maze", "3d pipes") for t in tiles)
    # Layers overlap by their configured fraction of the screen height
    back, front = widget._layers
    assert front.y < back.y + back.height


def test_unknown_tile_type_is_reported(widget, caplog):
    with caplog.at_level(logging.ERROR):
        widget.load_assets("hologram", "C:/somewhere")
    assert "Unknown tile type" in caplog.text
    assert widget._meta_tiles == []


def test_opengl_falls_back_to_raster_without_a_context(qapp, monkeypatch):
    monkeypatch.setattr(VaeScroll, "opengl_available", lambda: False)
    w = VaeScroll.create_widget("opengl", fullscreen=False, layers=[LayerConfig(speed=1.0)])
    try:
        assert type(w) is VaeScroll.EndlessScrollWidget
    finally:
        w._timer.stop()
        w._asset_loader.shutdown()
        w.close()


def test_renderer_setting_selects_the_widget(qapp, use_settings):
    use_settings({"renderer": "raster"})
    w = VaeScroll.create_widget(fullscreen=False, layers=[LayerConfig(speed=1.0)])
    try:
        assert w.RENDERER == "raster"
    finally:
        w._timer.stop()
        w._asset_loader.shutdown()
        w.close()


def test_opengl_widget_paints(qapp, use_settings):
    if not VaeScroll.opengl_available():
        pytest.skip("no OpenGL context on this platform (e.g. QT_QPA_PLATFORM=offscreen)")
    use_settings({"tile maze": {"count": 4, "maze size": [8, 5], "speed": 500}})
    w = VaeScroll.GLEndlessScrollWidget(fullscreen=False, layers=[LayerConfig(speed=3.0)])
    try:
        w._timer.stop()
        w.resize(400, 200)
        w.load_procedural_assets()
        w.build_layers()
        for _ in range(30):
            w._tick()
        image = w.grabFramebuffer()
        assert not image.isNull() and image.width() == 400
    finally:
        w._asset_loader.shutdown()
        w.close()


def test_wheel_speed_change(widget):
    layer = widget._layers[1]  # negative speed: "faster" means more negative
    layer.adjust_speed(0.5)
    assert layer.speed == pytest.approx(-2.5)
    layer.adjust_speed(-3.0)
    assert layer.speed == pytest.approx(0.5)  # slowing past zero reverses
