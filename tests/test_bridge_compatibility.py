"""Startup compatibility probe and the Repairs issue it raises.

Version skew between this integration and the Solar LCD Bridge used to surface
only as a rejected frame, logged at debug. The appliance's users read neither
logs nor rejected frames, so the bridge is probed once at startup and anything
unusable is reported in Settings -> Repairs.
"""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_cube.const import DOMAIN, ISSUE_LCD_BRIDGE
from custom_components.solar_cube.solar_lcd import (
    FRAME_BYTES,
    PROTOCOL_VERSION,
    BridgeStatus,
    SolarCubeLCDController,
)

from homeassistant.helpers import issue_registry as ir

URLOPEN = "custom_components.solar_cube.solar_lcd.urllib.request.urlopen"


@pytest.fixture
def controller(hass) -> SolarCubeLCDController:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Solar Cube")
    entry.add_to_hass(hass)
    return SolarCubeLCDController(hass, entry, "en", "http://bridge:8765")


def status_response(payload: dict, code: int = 200):
    """Patch urlopen with a context-manager response carrying ``payload``."""
    resp = MagicMock()
    resp.status = code
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda _s: resp
    resp.__exit__ = lambda *_a: False
    return patch(URLOPEN, return_value=resp)


def current_issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LCD_BRIDGE)


class TestCompatibilityProbe:
    def test_matching_bridge_is_accepted(self, controller) -> None:
        with status_response(
            {"ok": True, "protocol": PROTOCOL_VERSION, "frame_bytes": FRAME_BYTES}
        ):
            status = controller._client.fetch_status()

        assert status.reachable
        assert status.panel_connected
        assert status.fatal is None

    def test_protocol_skew_is_fatal(self, controller) -> None:
        with status_response(
            {"ok": True, "protocol": 99, "frame_bytes": FRAME_BYTES}
        ):
            status = controller._client.fetch_status()

        assert status.fatal is not None
        assert "protocol 99" in status.fatal

    def test_frame_geometry_skew_is_fatal(self, controller) -> None:
        with status_response(
            {"ok": True, "protocol": PROTOCOL_VERSION, "frame_bytes": 4096}
        ):
            status = controller._client.fetch_status()

        assert status.fatal is not None
        assert "4096-byte frames" in status.fatal

    def test_bridge_without_a_protocol_field_is_tolerated(self, controller) -> None:
        """An older bridge cannot be checked; drive it rather than refuse it."""
        with status_response({"ok": False, "usb_device": "04d9:fd01"}):
            status = controller._client.fetch_status()

        assert status.reachable
        assert status.fatal is None

    def test_unreachable_bridge_is_transient_not_fatal(self, controller) -> None:
        with patch(URLOPEN, side_effect=urllib.error.URLError("refused")):
            status = controller._client.fetch_status()

        assert not status.reachable
        assert status.fatal is None

    def test_rejected_token_on_status_is_fatal(self, controller) -> None:
        with patch(
            URLOPEN,
            side_effect=urllib.error.HTTPError("u", 401, "nope", {}, None),
        ):
            status = controller._client.fetch_status()

        assert status.fatal is not None
        assert "token" in status.fatal

    def test_unusable_url_short_circuits(self, hass) -> None:
        entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Solar Cube")
        entry.add_to_hass(hass)
        bad = SolarCubeLCDController(hass, entry, "en", "not-a-url")

        with patch(URLOPEN) as urlopen:
            status = bad._client.fetch_status()

        urlopen.assert_not_called()
        assert status.fatal is not None


class TestRepairsIssueLifecycle:
    async def test_probe_failure_raises_an_issue(self, hass, controller) -> None:
        with patch.object(
            controller._client,
            "fetch_status",
            return_value=BridgeStatus(reachable=True, fatal="protocol 99 vs 1"),
        ):
            await controller._async_probe()

        issue = current_issue(hass)
        assert issue is not None
        assert issue.translation_placeholders["reason"] == "protocol 99 vs 1"
        assert issue.translation_placeholders["url"] == "http://bridge:8765"

    async def test_healthy_probe_raises_nothing(self, hass, controller) -> None:
        with patch.object(
            controller._client,
            "fetch_status",
            return_value=BridgeStatus(
                reachable=True, panel_connected=True, protocol=PROTOCOL_VERSION
            ),
        ):
            await controller._async_probe()

        assert current_issue(hass) is None

    async def test_unreachable_probe_raises_nothing(self, hass, controller) -> None:
        """A bridge that has not started yet is not a configuration error."""
        with patch.object(
            controller._client,
            "fetch_status",
            return_value=BridgeStatus(reachable=False),
        ):
            await controller._async_probe()

        assert current_issue(hass) is None

    async def test_issue_clears_once_a_frame_is_accepted(
        self, hass, controller
    ) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(
                controller._client, "send_image", return_value=(False, "bad token")
            ),
        ):
            await controller._tick()
        assert current_issue(hass) is not None

        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(True, None)),
        ):
            await controller._tick()
        assert current_issue(hass) is None

    async def test_transient_outage_does_not_raise_an_issue(
        self, hass, controller
    ) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(False, None)),
        ):
            for _ in range(controller.QUIET_FAILURES * 2):
                await controller._tick()

        assert current_issue(hass) is None, (
            "an unplugged panel is not a configuration error"
        )

    async def test_stopping_the_controller_clears_the_issue(
        self, hass, controller
    ) -> None:
        controller._raise_issue("something")
        assert current_issue(hass) is not None

        await controller.async_stop()
        assert current_issue(hass) is None

    def test_the_issue_is_translated_in_every_language(self) -> None:
        from pathlib import Path

        translations = (
            Path(__file__).resolve().parent.parent
            / "custom_components"
            / "solar_cube"
            / "translations"
        )
        for path in translations.glob("*.json"):
            issue = json.loads(path.read_text("utf-8"))["issues"][ISSUE_LCD_BRIDGE]
            assert issue["title"], path.name
            # Both placeholders must survive translation or the card shows raw
            # braces to the user.
            assert "{reason}" in issue["description"], path.name
            assert "{url}" in issue["description"], path.name
