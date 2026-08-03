"""The battery tile must name the direction the battery is actually going.

`ess_active_power` is positive while discharging and negative while charging --
the convention the panel dashboard already uses (power-flow-card-plus battery
with `invert_state: false`). The LCD had the comparison the wrong way round, so
the tile read "CHARGING" throughout a discharge, most visibly in DCH2 where the
battery discharges to the grid.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from PIL import ImageDraw

from custom_components.solar_cube.solar_lcd import _render_image_pil

LANGUAGES = ("en", "pl")

LABELS = {
    "en": {"charging": "CHARGING", "discharging": "DISCHARGING"},
    "pl": {"charging": "ŁADOWANIE", "discharging": "ROZŁADOWANIE"},
}


def drawn_text(data: dict, lang: str, config_dir: str) -> list[str]:
    """Every string the renderer draws, in order."""
    seen: list[str] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        seen.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    with patch.object(ImageDraw.ImageDraw, "text", spy):
        _render_image_pil(data, lang, config_dir)
    return seen


def frame(ess_w: float, controller_id: int) -> dict:
    return {
        "ess_active_power": ess_w,
        "ess_soc": 55.0,
        "controller_id": controller_id,
        "pv_active_power": 500.0,
        "grid_active_power": -200.0,
        "consumption_active_power": 300.0,
        "buy_energy_price": 0.85,
        "sell_energy_price": 0.42,
        "target_soc": 0.8,
    }


@pytest.mark.parametrize("lang", LANGUAGES)
def test_discharging_to_grid_says_discharging(lang, tmp_path) -> None:
    """DCH2: the reported case."""
    text = drawn_text(frame(1500.0, 20), lang, str(tmp_path))
    assert LABELS[lang]["discharging"] in text
    assert LABELS[lang]["charging"] not in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_discharging_to_home_says_discharging(lang, tmp_path) -> None:
    """DCH1: same sign, same label."""
    text = drawn_text(frame(1500.0, 4), lang, str(tmp_path))
    assert LABELS[lang]["discharging"] in text


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("controller_id", [2, 10])
def test_charging_says_charging(lang, controller_id, tmp_path) -> None:
    """CH1 from PV and CH2 from the grid both draw negative power."""
    text = drawn_text(frame(-1500.0, controller_id), lang, str(tmp_path))
    assert LABELS[lang]["charging"] in text
    assert LABELS[lang]["discharging"] not in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_zero_is_not_reported_as_discharging(lang, tmp_path) -> None:
    """The tile has no idle state and reads 0.0 kW either way; this only pins
    that inverting the comparison did not flip the resting label."""
    text = drawn_text(frame(0.0, 0), lang, str(tmp_path))
    assert LABELS[lang]["charging"] in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_missing_reading_still_renders(lang, tmp_path) -> None:
    data = frame(0.0, 0)
    data["ess_active_power"] = None
    assert drawn_text(data, lang, str(tmp_path))


def test_the_dashboard_uses_the_same_convention() -> None:
    """If the panel card ever set invert_state: true the two would disagree."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for lang in LANGUAGES:
        path = (
            root / "custom_components" / "solar_cube" / "dashboards"
            / f"panel_solar_cube_{lang}.yaml"
        )
        text = path.read_text("utf-8")
        assert "invert_state: false" in text, path.name


# Controller states that charge the battery, and those that discharge it.
CHARGING_MODES = {"2", "10"}
DISCHARGING_MODES = {"4", "20"}


def _scenarios() -> dict:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "tools" / "preview_lcd.py"
    spec = importlib.util.spec_from_file_location("preview_lcd", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SCENARIOS


def test_preview_scenarios_agree_with_their_controller_mode() -> None:
    """The scenarios were authored against the inverted renderer, so every
    charging preview showed "DISCHARGING" once the sign was corrected."""
    wrong = []
    for name, (_description, data) in _scenarios().items():
        ess = data.get("ess_active_power")
        mode = str(data.get("controller_id"))
        if ess is None or not ess:
            continue
        if mode in CHARGING_MODES and ess > 0:
            wrong.append(f"{name}: {mode} charges but ess_active_power is +{ess}")
        if mode in DISCHARGING_MODES and ess < 0:
            wrong.append(f"{name}: {mode} discharges but ess_active_power is {ess}")
    assert not wrong, wrong
