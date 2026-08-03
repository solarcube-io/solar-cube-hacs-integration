"""Tests for the LCD renderer helpers."""
from __future__ import annotations

import pytest

from custom_components.solar_cube.sensor_definitions import DIVISIONS, scale_value
from custom_components.solar_cube.solar_lcd import (
    H,
    W,
    _encode_rgb565_portrait_py,
    _fmt_kw,
    _get_mode_state,
    _pil_to_rgb565_portrait,
    _safe_float,
)

pytest.importorskip("PIL")


def _sample_image():
    from PIL import Image

    img = Image.new("RGB", (W, H))
    pixels = img.load()
    for y in range(H):
        for x in range(W):
            pixels[x, y] = ((x * 7) % 256, (y * 3) % 256, (x + y) % 256)
    return img


class TestRgb565Encoding:
    def test_vectorised_encoder_matches_the_reference_implementation(self) -> None:
        img = _sample_image()
        assert _pil_to_rgb565_portrait(img) == _encode_rgb565_portrait_py(img)

    def test_output_size_matches_what_the_bridge_expects(self) -> None:
        assert len(_pil_to_rgb565_portrait(_sample_image())) == W * H * 2

    def test_byte_order_is_big_endian(self) -> None:
        from PIL import Image

        img = Image.new("RGB", (W, H), (255, 0, 0))
        data = _pil_to_rgb565_portrait(img)
        # Pure red in RGB565 is 0xF800.
        assert data[0] == 0xF8
        assert data[1] == 0x00


class TestScaling:
    def test_divisions_come_from_the_sensor_definitions(self) -> None:
        assert DIVISIONS["grid_voltage_l1"] == 1000.0
        assert "pv_active_power" not in DIVISIONS

    def test_scaled_value_matches_the_sensor_platform(self) -> None:
        assert scale_value("grid_voltage_l1", 230_500) == 230.5
        assert scale_value("pv_active_power", 4200) == 4200

    @pytest.mark.parametrize("bad", [None, "abc", object()])
    def test_safe_float_never_raises(self, bad) -> None:
        assert _safe_float(bad, "grid_voltage_l1") is None

    def test_safe_float_applies_the_division(self) -> None:
        assert _safe_float(230_500, "grid_voltage_l1") == 230.5


class TestFormatting:
    def test_kw_formatting_uses_absolute_values(self) -> None:
        assert _fmt_kw(-4200.0) == "4.2"
        assert _fmt_kw(4200.0) == "4.2"

    def test_missing_power_renders_a_placeholder(self) -> None:
        assert _fmt_kw(None) == "---"


class TestModeState:
    @pytest.mark.parametrize(
        ("raw", "expected_en"),
        [
            (0, "Maintain"),
            ("2", "PV charging"),
            (4.0, "Discharge to home"),
            ("10.0", "Grid charging"),
        ],
    )
    def test_known_controller_codes(self, raw, expected_en) -> None:
        label, _color = _get_mode_state(raw, "en")
        assert label == expected_en

    def test_polish_labels_are_used_when_selected(self) -> None:
        label, _ = _get_mode_state(2, "pl")
        assert label == "Ładowanie z PV"

    @pytest.mark.parametrize("raw", [None, "", "nope", 99])
    def test_unknown_codes_fall_back_to_the_error_state(self, raw) -> None:
        label, color = _get_mode_state(raw, "en")
        assert label == "System Error"
        assert color == (255, 0, 0)


class TestRenderSmoke:
    def test_renders_with_completely_empty_data(self, tmp_path) -> None:
        """The renderer must never raise: it runs on a timer and a crash would
        silently stop the display."""
        from custom_components.solar_cube.solar_lcd import _render_image_pil

        img = _render_image_pil({}, "en", str(tmp_path))
        assert img.size == (W, H)

    def test_renders_with_realistic_data(self, tmp_path) -> None:
        from custom_components.solar_cube.solar_lcd import _render_image_pil

        data = {
            "pv_active_power": 4200,
            "consumption_active_power": 1500,
            "ess_active_power": -800,
            "grid_active_power": -1900,
            "ess_soc": 76,
            "grid_voltage_l1": 231_000,
            "grid_voltage_l2": 229_000,
            "grid_voltage_l3": 230_000,
            "buy_energy_price": 0.87,
            "sell_energy_price": 0.32,
            "optimised_energy_total_savings": 12.34,
            "controller_id": 7,
        }
        img = _render_image_pil(data, "pl", str(tmp_path))
        assert img.size == (W, H)


class TestBridgeContract:
    """The Solar LCD Bridge lives in a separate repository:
    https://dev.azure.com/roygard/Solar%20Cube%20(Technology)/_git/Solar_LCD_Bridge

    These assertions are this repo's half of the interface contract documented
    in that repository's CONTRACT.md. Changing any of them without a matching
    bridge release breaks the display at runtime, so they are pinned here rather
    than merely commented.
    """

    # Mirrors DEFAULT_TOKEN in solar_lcd_bridge.py and ENV BRIDGE_TOKEN in its
    # Dockerfile.
    CONTRACT_DEFAULT_TOKEN = "solar-cube-lcd-default"
    # Mirrors EXPECTED_IMAGE_SIZE in the bridge (170 * 320 * 2).
    CONTRACT_FRAME_BYTES = 108_800

    def test_default_token_matches_the_bridge(self) -> None:
        from custom_components.solar_cube.const import DEFAULT_S1_LCD_BRIDGE_TOKEN

        assert DEFAULT_S1_LCD_BRIDGE_TOKEN == self.CONTRACT_DEFAULT_TOKEN

    def test_frame_size_matches_what_the_bridge_accepts(self) -> None:
        assert W * H * 2 == self.CONTRACT_FRAME_BYTES
        assert len(_pil_to_rgb565_portrait(_sample_image())) == self.CONTRACT_FRAME_BYTES

    def test_default_bridge_url_matches_the_compose_service_name(self) -> None:
        from custom_components.solar_cube.const import DEFAULT_S1_LCD_BRIDGE_URL

        # The bridge's inner-compose-fragment.yaml names the service
        # "solar_lcd_bridge" and exposes 8765 on the shared Docker network.
        assert DEFAULT_S1_LCD_BRIDGE_URL == "http://solar_lcd_bridge:8765"

    def test_client_posts_to_the_contract_endpoint_with_the_token_header(
        self,
    ) -> None:
        from custom_components.solar_cube.solar_lcd import S1BridgeClient

        client = S1BridgeClient("http://solar_lcd_bridge:8765", token="abc")
        assert client.image_url == "http://solar_lcd_bridge:8765/image"
        assert client.status_url == "http://solar_lcd_bridge:8765/status"
        assert client._headers == {"X-Bridge-Token": "abc"}
