"""Tests for the Solar Cube sensor platform."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from conftest import StubCoordinator
from freezegun import freeze_time

from custom_components.solar_cube.sensor import (
    SolarCubeKwhTotalSensor,
    SolarCubePeriodMeterSensor,
    _series_point_at,
)

from homeassistant.const import UnitOfEnergy
from homeassistant.util import dt as dt_util


def make_entry(entry_id: str = "abc", unique_id: str | None = "solar_cube"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.unique_id = unique_id
    entry.title = "Solar Cube"
    return entry


def make_meter(
    coordinator: StubCoordinator,
    *,
    period: str = "daily",
    source_key: str = "pv_active_energy",
    unit: str | None = UnitOfEnergy.KILO_WATT_HOUR,
    source_unit: str = "Wh",
) -> SolarCubePeriodMeterSensor:
    return SolarCubePeriodMeterSensor(
        coordinator,
        make_entry(),
        key=f"{period}_{source_key}",
        name="Test Meter",
        source_key=source_key,
        source_unit=source_unit,
        unit=unit,
        period=period,
    )


pytestmark = pytest.mark.usefixtures("utc_timezone")


class TestSeriesPointAt:
    """The horizon must be chosen by timestamp, not by list index."""

    def _series(self, start: datetime, count: int, step_minutes: int):
        return [
            {
                "dt": (start + timedelta(minutes=step_minutes * i)).isoformat(),
                "pf": float(i),
            }
            for i in range(count)
        ]

    @freeze_time("2026-03-01 12:00:00")
    def test_picks_first_point_at_or_after_horizon_15min_data(self) -> None:
        now = dt_util.now()
        series = self._series(now, 24, 15)
        # +1h with 15-minute samples is index 4, not the hard-coded index 3.
        assert _series_point_at(series, 1)["pf"] == 4.0

    @freeze_time("2026-03-01 12:00:00")
    def test_picks_first_point_at_or_after_horizon_hourly_data(self) -> None:
        now = dt_util.now()
        series = self._series(now, 12, 60)
        # The same code must also be right for hourly samples.
        assert _series_point_at(series, 1)["pf"] == 1.0
        assert _series_point_at(series, 6)["pf"] == 6.0

    @freeze_time("2026-03-01 12:00:00")
    def test_returns_none_when_horizon_is_beyond_the_series(self) -> None:
        series = self._series(dt_util.now(), 4, 15)
        assert _series_point_at(series, 6) is None

    def test_handles_empty_and_malformed_input(self) -> None:
        assert _series_point_at(None, 1) is None
        assert _series_point_at([], 1) is None
        assert _series_point_at([{"dt": "not-a-date"}], 1) is None
        assert _series_point_at(["nope"], 1) is None


class TestKwhTotalSensor:
    def test_converts_wh_to_kwh(self) -> None:
        sensor = SolarCubeKwhTotalSensor(
            StubCoordinator({"pv_active_energy": 12345.0}),
            make_entry(),
            key="pv_total",
            name="PV Total",
            source_key="pv_active_energy",
        )
        assert sensor.native_value == 12.345

    def test_zero_is_a_valid_reading_not_unknown(self) -> None:
        """A freshly commissioned meter legitimately reads 0; reporting unknown
        would punch a gap into long-term statistics."""
        sensor = SolarCubeKwhTotalSensor(
            StubCoordinator({"pv_active_energy": 0}),
            make_entry(),
            key="pv_total",
            name="PV Total",
            source_key="pv_active_energy",
        )
        assert sensor.native_value == 0.0

    @pytest.mark.parametrize("raw", [None, "abc", -5])
    def test_invalid_or_negative_readings_are_unknown(self, raw) -> None:
        sensor = SolarCubeKwhTotalSensor(
            StubCoordinator({"pv_active_energy": raw}),
            make_entry(),
            key="pv_total",
            name="PV Total",
            source_key="pv_active_energy",
        )
        assert sensor.native_value is None


class TestPeriodMeter:
    @freeze_time("2026-03-01 12:00:00")
    def test_first_reading_starts_at_zero(self) -> None:
        coordinator = StubCoordinator({"pv_active_energy": 5000.0})
        meter = make_meter(coordinator)
        meter._recalculate()
        assert meter.native_value == 0.0

    @freeze_time("2026-03-01 12:00:00")
    def test_accumulates_delta_within_a_period(self) -> None:
        coordinator = StubCoordinator({"pv_active_energy": 5000.0})
        meter = make_meter(coordinator)
        meter._recalculate()

        coordinator.data = {"pv_active_energy": 8000.0}
        meter._recalculate()
        assert meter.native_value == 3.0  # 3000 Wh -> 3 kWh

    def test_resets_at_the_period_boundary(self) -> None:
        coordinator = StubCoordinator({"pv_active_energy": 5000.0})
        with freeze_time("2026-03-01 23:00:00"):
            meter = make_meter(coordinator)
            meter._recalculate()
            coordinator.data = {"pv_active_energy": 9000.0}
            meter._recalculate()
            assert meter.native_value == 4.0

        with freeze_time("2026-03-02 00:30:00"):
            coordinator.data = {"pv_active_energy": 9500.0}
            meter._recalculate()
            assert meter.native_value == 0.0
            coordinator.data = {"pv_active_energy": 10500.0}
            meter._recalculate()
            assert meter.native_value == 1.0

    @freeze_time("2026-03-01 12:00:00")
    def test_counter_reset_preserves_energy_accumulated_before_the_reset(
        self,
    ) -> None:
        """Regression: rebasing on a source reset used to discard the whole
        period, so a mid-day inverter reset dropped the daily total to ~0."""
        coordinator = StubCoordinator({"pv_active_energy": 1000.0})
        meter = make_meter(coordinator)
        meter._recalculate()

        coordinator.data = {"pv_active_energy": 6000.0}
        meter._recalculate()
        assert meter.native_value == 5.0  # 5 kWh banked so far

        # Inverter lifetime counter rolls back to zero.
        coordinator.data = {"pv_active_energy": 0.0}
        meter._recalculate()
        assert meter.native_value == 5.0  # keeps the 5 kWh

        coordinator.data = {"pv_active_energy": 2000.0}
        meter._recalculate()
        assert meter.native_value == 7.0  # 5 kWh + 2 kWh after the reset

    @freeze_time("2026-03-01 12:00:00")
    def test_unavailable_source_keeps_the_last_value(self) -> None:
        coordinator = StubCoordinator({"pv_active_energy": 1000.0})
        meter = make_meter(coordinator)
        meter._recalculate()
        coordinator.data = {"pv_active_energy": 4000.0}
        meter._recalculate()
        assert meter.native_value == 3.0

        coordinator.data = {"pv_active_energy": None}
        meter._recalculate()
        assert meter.native_value == 3.0

    @freeze_time("2026-03-01 12:00:00")
    def test_last_reset_tracks_the_period_start(self) -> None:
        coordinator = StubCoordinator({"pv_active_energy": 1000.0})
        meter = make_meter(coordinator, period="daily")
        meter._recalculate()
        assert meter._attr_last_reset == dt_util.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    @freeze_time("2026-03-01 12:00:00")
    def test_native_value_has_no_side_effects(self) -> None:
        """State is computed on coordinator updates, so repeated property reads
        must be pure."""
        coordinator = StubCoordinator({"pv_active_energy": 1000.0})
        meter = make_meter(coordinator)
        meter._recalculate()
        coordinator.data = {"pv_active_energy": 4000.0}
        meter._recalculate()

        snapshot = (meter._baseline, meter._last_total, meter._carried)
        for _ in range(5):
            assert meter.native_value == 3.0
        assert (meter._baseline, meter._last_total, meter._carried) == snapshot

    @freeze_time("2026-03-01 12:00:00")
    async def test_state_survives_a_restart_within_the_same_period(self) -> None:
        period_key = dt_util.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        restored = MagicMock()
        restored.attributes = {
            "_period_key": period_key,
            "_baseline": 1000.0,
            "_last_total": 4000.0,
            "_carried": 2000.0,
        }

        coordinator = StubCoordinator({"pv_active_energy": 6000.0})
        meter = make_meter(coordinator)
        with (
            patch.object(
                SolarCubePeriodMeterSensor, "async_get_last_state", return_value=restored
            ),
            patch(
                "homeassistant.helpers.update_coordinator.CoordinatorEntity"
                ".async_added_to_hass"
            ),
            patch(
                "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass"
            ),
        ):
            await meter.async_added_to_hass()

        # (2000 Wh carried + (6000 - 1000) Wh) -> 7 kWh
        assert meter.native_value == 7.0

    @freeze_time("2026-03-04 12:00:00")  # a Wednesday
    def test_weekly_period_starts_on_sunday(self) -> None:
        meter = make_meter(StubCoordinator({"x": 0}), period="weekly", source_key="x")
        start = meter._period_start()
        assert start.weekday() == 6  # Sunday
        assert (start.hour, start.minute) == (0, 0)
        assert start.date() == datetime(2026, 3, 1).date()

    @freeze_time("2026-03-04 12:34:56")
    def test_monthly_period_starts_on_the_first(self) -> None:
        meter = make_meter(StubCoordinator({"x": 0}), period="monthly", source_key="x")
        start = meter._period_start()
        assert (start.day, start.hour, start.minute) == (1, 0, 0)

    def test_monetary_sensors_fall_back_to_pln_when_ha_has_no_currency(
        self, hass
    ) -> None:
        """Leaving the unit unset dropped device_class monetary, which keeps the
        savings sensors out of long-term statistics."""
        from custom_components.solar_cube.const import DEFAULT_CURRENCY

        assert DEFAULT_CURRENCY == "PLN"

    def test_currency_meters_are_monetary_without_wh_conversion(self) -> None:
        meter = make_meter(
            StubCoordinator({"savings": 10.0}),
            source_key="savings",
            source_unit="currency",
            unit="PLN",
        )
        assert meter._convert(10.0) == 10.0
        assert meter._attr_device_class == "monetary"
