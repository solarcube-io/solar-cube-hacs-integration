"""Coordinators for Solar Cube."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SolarCubeApi, SolarCubeApiAuthError, SolarCubeApiRequestError
from .const import (
    CONF_AGENTS_BUCKET,
    CONF_DATA_BUCKET,
    DOMAIN,
    FORECAST_UPDATE_INTERVAL,
    OPTIMAL_ACTIONS_UPDATE_INTERVAL,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class SolarCubeDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for simple scalar values."""

    def __init__(self, hass: HomeAssistant, api: SolarCubeApi, entry_data: dict[str, Any], sensor_definitions: list[dict[str, Any]]) -> None:
        self.api = api
        self.entry_data = entry_data
        self.sensor_definitions = sensor_definitions
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_data",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            values: dict[str, Any] = {}
            data_bucket = self.entry_data.get(CONF_DATA_BUCKET)
            for definition in self.sensor_definitions:
                bucket = definition.get("bucket", data_bucket)
                value = await self.api.async_query_last(
                    bucket=bucket,
                    measurement=definition["measurement"],
                    field=definition["field"],
                    range_start=definition.get("range_start", "-5m"),
                )
                values[definition["key"]] = value
            values["_last_update"] = datetime.utcnow().isoformat()
            return values
        except SolarCubeApiAuthError as err:
            raise ConfigEntryAuthFailed("InfluxDB unauthorized") from err
        except SolarCubeApiRequestError as err:
            raise UpdateFailed(str(err)) from err


class SolarCubeForecastCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator for forecast data."""

    def __init__(self, hass: HomeAssistant, api: SolarCubeApi, entry_data: dict[str, Any]) -> None:
        self.api = api
        self.entry_data = entry_data
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_forecast",
            update_interval=FORECAST_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            return await self.api.async_get_forecast(
                bucket=self.entry_data[CONF_AGENTS_BUCKET],
                hass_timezone=self.hass.config.time_zone,
            )
        except SolarCubeApiAuthError as err:
            raise ConfigEntryAuthFailed("InfluxDB unauthorized") from err
        except SolarCubeApiRequestError as err:
            raise UpdateFailed(str(err)) from err


class SolarCubeOptimalActionsCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator for optimal actions data."""

    def __init__(self, hass: HomeAssistant, api: SolarCubeApi, entry_data: dict[str, Any]) -> None:
        self.api = api
        self.entry_data = entry_data
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_optimal_actions",
            update_interval=OPTIMAL_ACTIONS_UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            return await self.api.async_get_optimal_actions(
                bucket=self.entry_data[CONF_AGENTS_BUCKET],
                hass_timezone=self.hass.config.time_zone,
            )
        except SolarCubeApiAuthError as err:
            raise ConfigEntryAuthFailed("InfluxDB unauthorized") from err
        except SolarCubeApiRequestError as err:
            raise UpdateFailed(str(err)) from err
