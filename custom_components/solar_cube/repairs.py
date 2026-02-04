"""Repairs for Solar Cube.

Used to show a HACS-like "Restart required" warning in Settings → Repairs,
with a fix action that can restart Home Assistant.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowHandler, FlowResult


class _RestartRequiredFlow(FlowHandler):
    VERSION = 1

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            await self.hass.services.async_call(
                "homeassistant",
                "restart",
                {},
                blocking=False,
            )
            return self.async_create_entry(title="", data={})

        # A simple confirm form: the issue description already explains what happens.
        return self.async_show_form(step_id="init")


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None = None
) -> FlowHandler:
    """Create a fix flow for a given Repairs issue."""

    if issue_id != "restart_required":
        raise ValueError(f"Unknown issue_id: {issue_id}")

    return _RestartRequiredFlow(hass)
