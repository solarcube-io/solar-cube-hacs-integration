"""Tests for coordinator query batching and bucket resolution."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.solar_cube.const import CONF_AGENTS_BUCKET, CONF_DATA_BUCKET
from custom_components.solar_cube.coordinator import SolarCubeDataCoordinator

DEFINITIONS = [
    {"key": "a", "measurement": "data", "field": "f/a"},
    {"key": "b", "measurement": "data", "field": "f/b"},
    {
        "key": "c",
        "measurement": "cs",
        "field": "cs/c",
        "source": "agents",
        "range_start": "-60m",
    },
    {
        "key": "d",
        "measurement": "cs",
        "field": "cs/d",
        "source": "agents",
        "range_start": "-60m",
    },
    {
        "key": "e",
        "measurement": "cs",
        "field": "cs/e",
        "source": "agents",
        "range_start": "-1h",
    },
]

ENTRY_DATA = {CONF_DATA_BUCKET: "live-db", CONF_AGENTS_BUCKET: "my-agents"}


@pytest.fixture
def coordinator(hass) -> SolarCubeDataCoordinator:
    return SolarCubeDataCoordinator(hass, MagicMock(), ENTRY_DATA, DEFINITIONS)


class TestQueryGrouping:
    def test_definitions_collapse_into_one_group_per_bucket_measurement_range(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        groups = coordinator._query_groups()
        assert set(groups) == {
            ("live-db", "data", "-5m"),
            ("my-agents", "cs", "-60m"),
            ("my-agents", "cs", "-1h"),
        }
        assert groups[("live-db", "data", "-5m")] == {"f/a": "a", "f/b": "b"}
        assert groups[("my-agents", "cs", "-60m")] == {"cs/c": "c", "cs/d": "d"}

    def test_agents_source_resolves_to_the_configured_bucket(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        """Regression: the bucket used to be hard-coded to "agents" in the sensor
        definitions, so a user-configured agents bucket was silently ignored."""
        assert coordinator._bucket_for(DEFINITIONS[2]) == "my-agents"
        assert coordinator._bucket_for(DEFINITIONS[0]) == "live-db"


class TestFetch:
    async def test_one_query_per_group_not_per_sensor(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        coordinator.api.async_query_last_batch = AsyncMock(return_value={})
        await coordinator._async_fetch()
        assert coordinator.api.async_query_last_batch.await_count == 3

    async def test_values_are_keyed_by_sensor_key(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        async def fake_batch(*, bucket, measurement, fields, range_start):
            return {field: f"{field}-value" for field in fields}

        coordinator.api.async_query_last_batch = AsyncMock(side_effect=fake_batch)
        result = await coordinator._async_fetch()

        assert result["a"] == "f/a-value"
        assert result["c"] == "cs/c-value"
        assert "_last_update" in result

    async def test_missing_points_become_none(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        coordinator.api.async_query_last_batch = AsyncMock(return_value={"f/a": 1})
        result = await coordinator._async_fetch()
        assert result["a"] == 1
        assert result["b"] is None


class TestErrorTranslation:
    async def test_auth_error_triggers_reauth(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        from custom_components.solar_cube.api import SolarCubeApiAuthError

        from homeassistant.exceptions import ConfigEntryAuthFailed

        with (
            patch.object(
                coordinator, "_async_fetch", side_effect=SolarCubeApiAuthError()
            ),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            await coordinator._async_update_data()

    async def test_request_error_becomes_update_failed(
        self, coordinator: SolarCubeDataCoordinator
    ) -> None:
        from custom_components.solar_cube.api import SolarCubeApiRequestError

        from homeassistant.helpers.update_coordinator import UpdateFailed

        with (
            patch.object(
                coordinator, "_async_fetch", side_effect=SolarCubeApiRequestError("x")
            ),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()
