"""Tests for the InfluxDB API wrapper."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from conftest import make_tables

from custom_components.solar_cube.api import (
    SolarCubeApi,
    SolarCubeApiAuthError,
    SolarCubeApiRequestError,
)


@pytest.fixture
def api() -> SolarCubeApi:
    with patch("custom_components.solar_cube.api.influxdb_client.InfluxDBClient"):
        return SolarCubeApi(url="http://influx:8086", token="tok", org="org")


class TestFluxLiterals:
    def test_quotes_and_backslashes_are_escaped(self, api: SolarCubeApi) -> None:
        assert api._flux_str_literal('a"b') == '"a\\"b"'
        assert api._flux_str_literal("a\\b") == '"a\\\\b"'

    def test_flux_string_interpolation_is_neutralised(
        self, api: SolarCubeApi
    ) -> None:
        """Flux interpolates ${...} inside string literals; json.dumps does not
        escape it, so the bucket name must not be able to open an interpolation."""
        literal = api._flux_str_literal('${1+1}')
        assert "${" not in literal.replace("\\${", "")

    def test_bucket_literal_strips_whitespace(self, api: SolarCubeApi) -> None:
        assert api._bucket_literal("  db  ") == '"db"'

    def test_field_set_literal_builds_flux_array(self, api: SolarCubeApi) -> None:
        assert api._field_set_literal(["a", "b"]) == '["a", "b"]'


class TestTokenNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  tok  ", "tok"),
            ("Token abc", "abc"),
            ("Bearer abc", "abc"),
            ("", ""),
        ],
    )
    def test_prefixes_are_stripped(self, raw: str, expected: str) -> None:
        assert SolarCubeApi._normalize_token(raw) == expected


class TestBatchQuery:
    async def test_returns_one_value_per_field(self, api: SolarCubeApi) -> None:
        tables = make_tables([("f1", 1.5, None), ("f2", 2.5, None)])
        with patch.object(api, "_async_query", return_value=tables) as query:
            result = await api.async_query_last_batch(
                bucket="db", measurement="data", fields=["f1", "f2"]
            )

        assert result == {"f1": 1.5, "f2": 2.5}
        # One round-trip for the whole batch, not one per field.
        assert query.await_count == 1

    async def test_flux_contains_every_requested_field(
        self, api: SolarCubeApi
    ) -> None:
        with patch.object(api, "_async_query", return_value=[]) as query:
            await api.async_query_last_batch(
                bucket="db", measurement="data", fields=["alpha", "beta"]
            )

        flux = query.await_args.args[0]
        assert 'contains(value: r["_field"], set: ["alpha", "beta"])' in flux
        assert "|> last()" in flux

    async def test_empty_field_list_skips_the_query(self, api: SolarCubeApi) -> None:
        with patch.object(api, "_async_query") as query:
            assert await api.async_query_last_batch("db", "data", []) == {}
        query.assert_not_called()


class TestErrorTranslation:
    def _api_exception(self, status: int) -> Exception:
        from influxdb_client.rest import ApiException

        err = ApiException(status=status)
        err.status = status
        return err

    def test_401_becomes_auth_error(self, api: SolarCubeApi) -> None:
        with pytest.raises(SolarCubeApiAuthError):
            api._raise_for_api_exception(self._api_exception(401), "ctx", "flux")

    @pytest.mark.parametrize("status", [400, 404, 500])
    def test_other_statuses_become_request_error(
        self, api: SolarCubeApi, status: int
    ) -> None:
        with pytest.raises(SolarCubeApiRequestError):
            api._raise_for_api_exception(self._api_exception(status), "ctx", "flux")

    def test_exception_details_are_length_bounded(self, api: SolarCubeApi) -> None:
        err = self._api_exception(400)
        err.body = "x" * 5000
        details = SolarCubeApi._api_exception_details(err)
        assert len(details) < 1000
        assert details.endswith("…'")


class TestForecastParsing:
    async def test_groups_records_by_timestamp_and_maps_short_keys(
        self, api: SolarCubeApi
    ) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        t1 = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
        tables = make_tables(
            [
                ("cs/forecasts/production_forecast_kwh", 1.23456, t0),
                ("cs/forecasts/consumption_forecast_kwh", 2.0, t0),
                ("cs/forecasts/production_forecast_kwh", 3.0, t1),
            ]
        )
        with patch.object(api, "_async_query", return_value=tables):
            result = await api.async_get_forecast(bucket="agents", hass_timezone="UTC")

        assert len(result) == 2
        # Values are rounded to 3 decimals and unknown fields stay None.
        assert result[0]["pf"] == 1.235
        assert result[0]["cf"] == 2.0
        assert result[0]["sf"] is None
        assert result[1]["pf"] == 3.0
        # Ordered ascending by timestamp.
        assert result[0]["dt"] < result[1]["dt"]

    async def test_unknown_timezone_falls_back_instead_of_crashing(
        self, api: SolarCubeApi
    ) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        tables = make_tables([("cs/forecasts/soc_forecast", 50, t0)])
        with patch.object(api, "_async_query", return_value=tables):
            result = await api.async_get_forecast(
                bucket="agents", hass_timezone="Not/AZone"
            )
        assert result[0]["sf"] == 50


class TestOptimalActionsParsing:
    async def test_short_keys_come_from_the_field_suffix(
        self, api: SolarCubeApi
    ) -> None:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        tables = make_tables(
            [("cs/opt_actions/gb", 0.5, t0), ("cs/opt_actions/pv_unknown", 9.0, t0)]
        )
        with patch.object(api, "_async_query", return_value=tables):
            result = await api.async_get_optimal_actions(
                bucket="agents", hass_timezone="UTC"
            )

        assert result[0]["gb"] == 0.5
        # Unexpected fields must not inject new keys into the payload.
        assert "pv_unknown" not in result[0]
        assert set(result[0]) == {"dt", "bc", "bg", "gb", "gc", "pb", "pc", "pg"}
