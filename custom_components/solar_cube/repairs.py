"""Repairs for Solar Cube.

Shows a "Restart required" warning in Settings → Repairs with a fix action that
restarts Home Assistant.
"""
from __future__ import annotations

from typing import Any

from .const import ISSUE_RESTART_REQUIRED

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult


class RestartRequiredRepairFlow(RepairsFlow):
    """Confirm, then restart Home Assistant."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            await self.hass.services.async_call(
                "homeassistant", "restart", {}, blocking=False
            )
            return self.async_create_entry(title="", data={})

        # The issue description already explains what happens; an empty schema
        # renders a plain confirm dialog.
        return self.async_show_form(step_id="confirm", data_schema=None)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a fix flow for a given Repairs issue."""

    if issue_id != ISSUE_RESTART_REQUIRED:
        raise ValueError(f"Unknown issue_id: {issue_id}")

    return RestartRequiredRepairFlow()
