"""Tests for the repairs fix flow and the LCD controller lifecycle."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_cube.const import DATA_ENTRIES, DOMAIN
from custom_components.solar_cube.repairs import (
    RestartRequiredRepairFlow,
    async_create_fix_flow,
)
from custom_components.solar_cube.solar_lcd import (
    S1BridgeClient,
    SolarCubeLCDController,
)

from homeassistant.components.repairs import RepairsFlow
from homeassistant.data_entry_flow import FlowResultType


class TestRepairsFlow:
    async def test_returns_a_real_repairs_flow(self, hass) -> None:
        """It must be a RepairsFlow, not a bare FlowHandler: the repairs manager
        assigns issue_id/data onto the instance it gets back."""
        flow = await async_create_fix_flow(hass, "restart_required", None)
        assert isinstance(flow, RepairsFlow)

    async def test_unknown_issue_id_is_rejected(self, hass) -> None:
        with pytest.raises(ValueError, match="Unknown issue_id"):
            await async_create_fix_flow(hass, "something_else", None)

    async def test_shows_a_confirm_step_then_restarts(self, hass) -> None:
        flow = RestartRequiredRepairFlow()
        flow.hass = hass

        result = await flow.async_step_init()
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "confirm"

        with patch(
            "homeassistant.core.ServiceRegistry.async_call"
        ) as service:
            result = await flow.async_step_confirm({})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        service.assert_called_once_with(
            "homeassistant", "restart", {}, blocking=False
        )

    def test_confirm_step_is_translated_in_every_language(self) -> None:
        import json
        from pathlib import Path

        translations = (
            Path(__file__).resolve().parent.parent
            / "custom_components"
            / "solar_cube"
            / "translations"
        )
        for path in translations.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            step = data["issues"]["restart_required"]["fix_flow"]["step"]["confirm"]
            assert step["title"], path.name
            assert step["description"], path.name


class TestBridgeClient:
    def test_token_is_sent_as_a_header_when_configured(self) -> None:
        client = S1BridgeClient("http://bridge:8765/", token="s3cret")
        assert client.image_url == "http://bridge:8765/image"
        assert client._headers == {"X-Bridge-Token": "s3cret"}

    def test_no_header_when_no_token(self) -> None:
        assert S1BridgeClient("http://bridge:8765")._headers == {}


class TestLcdController:
    @pytest.fixture
    def controller(self, hass) -> SolarCubeLCDController:
        entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Solar Cube")
        entry.add_to_hass(hass)
        return SolarCubeLCDController(hass, entry, "en", "http://bridge:8765")

    async def test_start_uses_a_tracked_background_task(
        self, hass, controller
    ) -> None:
        """A raw loop.create_task() would not be cancelled at shutdown."""
        with patch.object(
            controller._entry, "async_create_background_task"
        ) as create_task:
            controller.start()
        create_task.assert_called_once()
        assert create_task.call_args.kwargs["name"] == f"{DOMAIN}_lcd_refresh"

    async def test_start_is_idempotent(self, hass, controller) -> None:
        with patch.object(
            controller._entry, "async_create_background_task"
        ) as create_task:
            controller.start()
            controller.start()
        assert create_task.call_count == 1

    async def test_stop_cancels_and_awaits_the_task(self, hass, controller) -> None:
        controller.start()
        assert controller._task is not None

        await controller.async_stop()
        assert controller._task is None
        assert controller._running is False

    async def test_stop_is_safe_when_never_started(self, hass, controller) -> None:
        await controller.async_stop()

    async def test_tick_survives_missing_coordinator_data(
        self, hass, controller
    ) -> None:
        """The loop runs on a timer; a KeyError here would kill the display."""
        hass.data.setdefault(DOMAIN, {}).setdefault(DATA_ENTRIES, {})

        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(True, None)) as send,
        ):
            await controller._tick()

        send.assert_called_once()

    async def test_tick_does_not_post_when_rendering_fails(
        self, hass, controller
    ) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                side_effect=RuntimeError("no fonts"),
            ),
            patch.object(controller._client, "send_image") as send,
        ):
            await controller._tick()

        send.assert_not_called()

    async def test_loop_keeps_running_after_a_failing_tick(
        self, hass, controller
    ) -> None:
        """One bad tick must not end the refresh loop."""
        calls = 0

        async def flaky_tick() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")

        async def fake_sleep(_seconds: float) -> None:
            # Let the loop run a few iterations, then ask it to finish.
            if calls >= 3:
                controller._running = False

        controller._running = True
        with (
            patch.object(controller, "_tick", side_effect=flaky_tick),
            patch(
                "custom_components.solar_cube.solar_lcd.asyncio.sleep",
                side_effect=fake_sleep,
            ),
        ):
            await controller._loop()

        assert calls == 3


class TestControllerStoppedOnUnload:
    async def test_unload_awaits_the_controller(self, hass) -> None:
        import custom_components.solar_cube as solar_cube

        entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Solar Cube")
        entry.add_to_hass(hass)

        controller = MagicMock()
        controller.async_stop = MagicMock(return_value=asyncio.sleep(0))

        solar_cube._domain_data(hass)[DATA_ENTRIES][entry.entry_id] = {
            "lcd_controller": controller,
            "api": None,
        }

        with patch.object(
            hass.config_entries, "async_unload_platforms", return_value=True
        ):
            assert await solar_cube.async_unload_entry(hass, entry)

        controller.async_stop.assert_called_once()


class TestBridgeErrorVisibility:
    """A misconfigured bridge must be reported, not retried in silence.

    The bridge distinguishes a rejected token and a rejected frame from a
    missing panel; discarding that distinction leaves the user with a blank
    display and nothing above debug level in the log.
    """

    @pytest.fixture
    def controller(self, hass) -> SolarCubeLCDController:
        entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Solar Cube")
        entry.add_to_hass(hass)
        return SolarCubeLCDController(hass, entry, "en", "http://bridge:8765")

    def _http_error(self, code: int, reason: str):
        import urllib.error

        return urllib.error.HTTPError("http://b/image", code, reason, {}, None)

    def test_rejected_token_is_reported_as_fatal(self, controller) -> None:
        with patch(
            "custom_components.solar_cube.solar_lcd.urllib.request.urlopen",
            side_effect=self._http_error(401, "Invalid or missing X-Bridge-Token"),
        ):
            ok, fatal = controller._client.send_image(b"x")
        assert ok is False
        assert fatal is not None and "token" in fatal

    def test_rejected_frame_is_reported_as_fatal(self, controller) -> None:
        with patch(
            "custom_components.solar_cube.solar_lcd.urllib.request.urlopen",
            side_effect=self._http_error(400, "Unexpected size 10, expected 108800"),
        ):
            ok, fatal = controller._client.send_image(b"x")
        assert ok is False
        assert fatal is not None and "different versions" in fatal

    def test_missing_panel_is_transient_not_fatal(self, controller) -> None:
        with patch(
            "custom_components.solar_cube.solar_lcd.urllib.request.urlopen",
            side_effect=self._http_error(503, "USB device not available"),
        ):
            ok, fatal = controller._client.send_image(b"x")
        assert (ok, fatal) == (False, None)

    def test_unreachable_bridge_is_transient(self, controller) -> None:
        import urllib.error

        with patch(
            "custom_components.solar_cube.solar_lcd.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            ok, fatal = controller._client.send_image(b"x")
        assert (ok, fatal) == (False, None)

    async def test_fatal_reason_is_logged_once_not_every_tick(
        self, hass, controller, caplog
    ) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(
                controller._client,
                "send_image",
                return_value=(False, "the bridge rejected our token (HTTP 401)"),
            ),
        ):
            for _ in range(5):
                await controller._tick()

        assert caplog.text.count("LCD display disabled") == 1

    async def test_sustained_outage_warns_once(self, hass, controller, caplog) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(False, None)),
        ):
            for _ in range(controller.QUIET_FAILURES * 2):
                await controller._tick()

        assert caplog.text.count("has not responded") == 1

    async def test_recovery_resets_the_counters(self, hass, controller) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(False, None)),
        ):
            await controller._tick()
        assert controller._failures == 1

        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(True, None)),
        ):
            await controller._tick()
        assert controller._failures == 0
        assert controller._reported_fatal is None


class TestRefreshEconomy:
    """The loop ticks every 5 s but the data changes every 30 s."""

    @pytest.fixture
    def controller(self, hass) -> SolarCubeLCDController:
        entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Solar Cube")
        entry.add_to_hass(hass)
        c = SolarCubeLCDController(hass, entry, "en", "http://bridge:8765")
        hass.data.setdefault(DOMAIN, {}).setdefault(DATA_ENTRIES, {})[entry.entry_id] = {
            "data_coordinator": MagicMock(data={"pv_active_power": 1000})
        }
        return c

    async def test_unchanged_data_is_not_re_rendered(self, hass, controller) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ) as render,
            patch.object(controller._client, "send_image", return_value=(True, None)),
        ):
            for _ in range(6):
                await controller._tick()

        assert render.call_count == 1, (
            f"re-rendered an identical frame {render.call_count} times"
        )

    async def test_changed_data_is_re_rendered(self, hass, controller) -> None:
        state = hass.data[DOMAIN][DATA_ENTRIES][controller._entry.entry_id]
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ) as render,
            patch.object(controller._client, "send_image", return_value=(True, None)),
        ):
            await controller._tick()
            state["data_coordinator"] = MagicMock(data={"pv_active_power": 2000})
            await controller._tick()

        assert render.call_count == 2

    async def test_heartbeat_resends_after_the_idle_window(
        self, hass, controller
    ) -> None:
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ) as render,
            patch.object(controller._client, "send_image", return_value=(True, None)),
        ):
            await controller._tick()
            controller._last_sent_at -= controller.HEARTBEAT_INTERVAL + 1
            await controller._tick()

        assert render.call_count == 2, "an unchanged frame must still self-heal"

    async def test_interval_backs_off_while_the_bridge_is_down(
        self, hass, controller
    ) -> None:
        from custom_components.solar_cube.solar_lcd import LCD_REFRESH_INTERVAL

        assert controller._interval() == LCD_REFRESH_INTERVAL
        with (
            patch(
                "custom_components.solar_cube.solar_lcd._render_image",
                return_value=b"\x00\x00",
            ),
            patch.object(controller._client, "send_image", return_value=(False, None)),
        ):
            for _ in range(controller.BACKOFF_AFTER):
                await controller._tick()

        assert controller._interval() == controller.BACKOFF_INTERVAL

    async def test_misconfiguration_backs_off_immediately(
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

        assert controller._interval() == controller.BACKOFF_INTERVAL


class TestBridgeUrlValidation:
    @pytest.mark.parametrize(
        ("url", "fragment"),
        [
            ("", "no Solar LCD Bridge URL"),
            ("solar_lcd_bridge:8765", "must start with http"),
            ("http://", "has no host"),
            ("ftp://host/x", "must start with http"),
        ],
    )
    def test_bad_urls_report_a_reason_instead_of_raising(self, url, fragment) -> None:
        """Regression: an empty URL raised ValueError out of send_image, which
        the loop swallowed at debug level -- an invisible misconfiguration."""
        ok, fatal = S1BridgeClient(url, token="t").send_image(b"x")
        assert ok is False
        assert fatal is not None and fragment in fatal

    def test_a_good_url_has_no_error(self) -> None:
        assert S1BridgeClient("http://solar_lcd_bridge:8765").url_error is None
