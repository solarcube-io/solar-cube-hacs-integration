"""API helpers for Solar Cube."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import influxdb_client
from influxdb_client.rest import ApiException

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Fields pulled by the forecast query, mapped to their short payload keys.
FORECAST_FIELDS: dict[str, str] = {
    "cs/schedule/controller": "ctr",
    "cs/schedule/target_soc": "ts",
    "cs/forecasts/consumption_forecast_kwh": "cf",
    "cs/forecasts/production_forecast_kwh": "pf",
    "cs/forecasts/soc_forecast": "sf",
    "cs/prices/buy_total_price_per_kwh": "bp",
    "cs/prices/sell_price_per_kwh": "sp",
}

# Fields pulled by the optimal-actions query; the short key is the field suffix.
OPTIMAL_ACTION_FIELDS: tuple[str, ...] = (
    "cs/opt_actions/bc",
    "cs/opt_actions/bg",
    "cs/opt_actions/gb",
    "cs/opt_actions/gc",
    "cs/opt_actions/pb",
    "cs/opt_actions/pc",
    "cs/opt_actions/pg",
)


class SolarCubeApiAuthError(Exception):
    """Raised when InfluxDB returns an authentication error (401)."""


class SolarCubeApiRequestError(Exception):
    """Raised when an InfluxDB request fails for non-auth reasons."""


class SolarCubeApi:
    """Lightweight wrapper around influxdb-client."""

    def __init__(self, url: str, token: str, org: str) -> None:
        self._client = influxdb_client.InfluxDBClient(
            url=url,
            token=self._normalize_token(token),
            org=org,
            connection_pool_maxsize=64,
        )
        self._query_api = self._client.query_api()

    @staticmethod
    def _normalize_token(token: str) -> str:
        token = (token or "").strip()
        for prefix in ("Token ", "Bearer "):
            if token.startswith(prefix):
                return token[len(prefix) :].strip()
        return token

    @staticmethod
    def _flux_str_literal(value: str) -> str:
        """Return a Flux string literal (double-quoted) for a Python string.

        ``json.dumps`` handles quotes, backslashes and control characters. Flux
        additionally performs ``${...}`` interpolation inside string literals, so
        the dollar sign is escaped separately to keep the value inert.
        """

        return json.dumps(value).replace("${", "\\${")

    def _bucket_literal(self, bucket: str) -> str:
        return self._flux_str_literal((bucket or "").strip())

    def _field_set_literal(self, fields: Iterable[str]) -> str:
        """Return a Flux array literal of quoted field names."""

        return "[" + ", ".join(self._flux_str_literal(f) for f in fields) + "]"

    @staticmethod
    def _raise_for_api_exception(err: ApiException, context: str, flux: str) -> None:
        """Translate an InfluxDB ApiException into a Solar Cube error."""

        status = getattr(err, "status", None)
        if status == 401:
            raise SolarCubeApiAuthError("Unauthorized") from err
        if status == 400:
            _LOGGER.error(
                "InfluxDB rejected Flux (%s). details=%s flux=%s",
                context,
                SolarCubeApi._api_exception_details(err),
                flux,
            )
        raise SolarCubeApiRequestError(str(err)) from err

    async def _async_query(self, flux: str, context: str) -> Any:
        """Run a Flux query in a worker thread with uniform error handling."""

        _LOGGER.debug("Influx %s flux=%s", context, flux)
        try:
            return await asyncio.to_thread(self._query_api.query, flux)
        except ApiException as err:
            self._raise_for_api_exception(err, context, flux)
            raise  # pragma: no cover - _raise_for_api_exception always raises

    async def async_validate(self, bucket: str | None = None) -> None:
        """Validate credentials by performing an authenticated call.

        Prefer validating via a lightweight query when a bucket is known,
        because some tokens might not have permission to list buckets.
        """
        if bucket:
            flux = (
                f"from(bucket: {self._bucket_literal(bucket)}) "
                "|> range(start: -1m) "
                "|> limit(n: 1)"
            )
            await self._async_query(flux, "validate")
            return

        try:
            buckets_api = self._client.buckets_api()
            await asyncio.to_thread(buckets_api.find_buckets)
        except ApiException as err:
            self._raise_for_api_exception(err, "validate", "<find_buckets>")

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _api_exception_details(err: ApiException) -> str:
        status = getattr(err, "status", None)
        reason = getattr(err, "reason", None)
        body = getattr(err, "body", None)
        # Keep log lines bounded.
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", errors="replace")
        if isinstance(body, str) and len(body) > 800:
            body = body[:800] + "…"
        return f"status={status} reason={reason} body={body!r}"

    async def async_query_last_batch(
        self,
        bucket: str,
        measurement: str,
        fields: Sequence[str],
        range_start: str = "-5m",
    ) -> dict[str, Any]:
        """Return the latest value for each requested field in one query.

        Replaces the previous one-query-per-sensor approach: ``last()`` runs per
        table and the group key includes ``_field``, so a single round-trip
        yields one record per field.
        """

        if not fields:
            return {}

        flux = (
            f"from(bucket: {self._bucket_literal(bucket)}) "
            f"|> range(start: {range_start}) "
            f'|> filter(fn: (r) => r["_measurement"] == {self._flux_str_literal(measurement)}) '
            f'|> filter(fn: (r) => contains(value: r["_field"], set: {self._field_set_literal(fields)})) '
            "|> last()"
        )
        result = await self._async_query(flux, "query_last_batch")

        values: dict[str, Any] = {}
        for table in result:
            for record in table.records:
                values[record.get_field()] = record.get_value()
        return values

    @staticmethod
    def _record_local_time(record: Any, tz: Any) -> datetime:
        record_time = record.get_time()
        if isinstance(record_time, str):
            record_time = datetime.fromisoformat(record_time)
        return record_time.astimezone(tz)

    @staticmethod
    def _rounded(value: Any) -> Any:
        return round(value, 3) if isinstance(value, (float, int)) else value

    async def async_get_forecast(
        self, bucket: str, hass_timezone: str
    ) -> list[dict[str, Any]]:
        """Return the hourly forecast series ordered by timestamp."""

        flux = (
            f"from(bucket: {self._bucket_literal(bucket)})\n"
            "  |> range(start: now(), stop: 32h)\n"
            '  |> filter(fn: (r) => r["_measurement"] == "cs")\n'
            "  |> filter(fn: (r) => contains(value: r[\"_field\"], set: "
            f"{self._field_set_literal(FORECAST_FIELDS)}))"
        )
        result = await self._async_query(flux, "forecast")

        tz = dt_util.get_time_zone(hass_timezone) or dt_util.DEFAULT_TIME_ZONE
        empty: dict[str, Any] = dict.fromkeys(FORECAST_FIELDS.values())
        forecast_data: dict[str, dict[str, Any]] = {}

        for table in result:
            for record in table.records:
                hour_key = self._record_local_time(record, tz).isoformat()
                bucket_row = forecast_data.setdefault(hour_key, dict(empty))
                short_key = FORECAST_FIELDS.get(record.get_field())
                if short_key is not None:
                    bucket_row[short_key] = self._rounded(record.get_value())

        return [
            {"dt": hour_key, **data}
            for hour_key, data in sorted(forecast_data.items())
        ]

    async def async_get_optimal_actions(
        self, bucket: str, hass_timezone: str
    ) -> list[dict[str, Any]]:
        """Return the optimal-action series ordered by timestamp."""

        flux = (
            f"from(bucket: {self._bucket_literal(bucket)})\n"
            "  |> range(start: now(), stop: 32h)\n"
            '  |> filter(fn: (r) => r["_measurement"] == "cs")\n'
            "  |> filter(fn: (r) => contains(value: r[\"_field\"], set: "
            f"{self._field_set_literal(OPTIMAL_ACTION_FIELDS)}))"
        )
        result = await self._async_query(flux, "optimal_actions")

        tz = dt_util.get_time_zone(hass_timezone) or dt_util.DEFAULT_TIME_ZONE
        empty: dict[str, Any] = {
            field.rsplit("/", 1)[-1]: None for field in OPTIMAL_ACTION_FIELDS
        }
        actions: dict[str, dict[str, Any]] = {}

        for table in result:
            for record in table.records:
                hour_key = self._record_local_time(record, tz).isoformat()
                action_row = actions.setdefault(hour_key, dict(empty))
                short_key = record.get_field().rsplit("/", 1)[-1]
                if short_key in action_row:
                    action_row[short_key] = self._rounded(record.get_value())

        return [
            {"dt": hour_key, **data} for hour_key, data in sorted(actions.items())
        ]
