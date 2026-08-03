"""Sensor platform for Solar Cube."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .const import DATA_ENTRIES, DEFAULT_CURRENCY, DEFAULT_NAME, DOMAIN
from .coordinator import (
    LAST_UPDATE_KEY,
    SolarCubeDataCoordinator,
    SolarCubeForecastCoordinator,
    SolarCubeOptimalActionsCoordinator,
)
from .sensor_definitions import scale_value

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

# Horizons exposed as "point" sensors, in hours.
FORECAST_HORIZONS: tuple[int, ...] = (1, 6)

OPTIMAL_ACTION_KEYS: tuple[str, ...] = ("gb", "bg", "bc", "gc", "pb", "pc", "pg")

FORECAST_POINT_SENSORS: tuple[tuple[str, str, str], ...] = (
    # (key prefix, display name, forecast payload key)
    ("forecasted_production", "Forecasted Production", "pf"),
    ("forecasted_consumption", "Forecasted Consumption", "cf"),
    ("soc_forecast", "SoC Forecast", "sf"),
)

# Wh -> kWh lifetime totals: (key, display name, coordinator source key)
KWH_TOTALS: tuple[tuple[str, str, str], ...] = (
    ("ess_discharged_energy", "ESS Discharged Energy", "ess_discharge_energy"),
    ("ess_charged_energy", "ESS Charged Energy", "ess_charge_energy"),
    (
        "grid_buy_active_energy_total",
        "Grid Buy Active Energy Total",
        "grid_buy_active_energy",
    ),
    (
        "grid_sell_active_energy_total",
        "Grid Sell Active Energy Total",
        "grid_sell_active_energy",
    ),
    ("pv_active_energy_total", "PV Active Energy Total", "pv_active_energy"),
    (
        "consumption_active_energy_total",
        "Consumption Active Energy Total",
        "consumption_active_energy",
    ),
)

# Energy period meters: (key suffix, display name, coordinator source key)
ENERGY_PERIOD_METERS: tuple[tuple[str, str, str], ...] = (
    ("grid_sell_energy", "Grid Sell Energy", "grid_sell_active_energy"),
    ("grid_buy_energy", "Grid Buy Energy", "grid_buy_active_energy"),
    ("pv_energy", "PV Energy", "pv_active_energy"),
    ("consumption_energy", "Consumption Energy", "consumption_active_energy"),
    ("ess_charge_energy", "ESS Charge Energy", "ess_charge_energy"),
    ("ess_discharge_energy", "ESS Discharge Energy", "ess_discharge_energy"),
)

SAVINGS_SOURCE_KEY = "optimised_energy_total_savings"


def _round_float(value: Any) -> Any:
    """Round floats to at most 5 decimal places to avoid float artifacts."""

    if isinstance(value, float):
        # Use string formatting to avoid representations like 0.000555800000000186.
        return float(f"{value:.5f}")
    return value


def _unique_id_prefix(entry: ConfigEntry) -> str:
    """Return a stable unique_id prefix for entities.

    Using entry.entry_id causes entity duplication after uninstall/reinstall
    because entry_id changes. entry.unique_id is stable for this integration.
    """

    return entry.unique_id or entry.entry_id


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the single device all Solar Cube entities belong to."""

    return DeviceInfo(
        identifiers={(DOMAIN, _unique_id_prefix(entry))},
        name=entry.title or DEFAULT_NAME,
        manufacturer="Solar Cube",
        model="Solar Cube HEMS",
    )


def _series_point_at(
    series: list[dict[str, Any]] | None, hours: int
) -> dict[str, Any] | None:
    """Return the first series entry at or after ``now + hours``.

    The series is ordered ascending by timestamp. Selecting by timestamp rather
    than by list index keeps the horizon correct regardless of the sample
    resolution InfluxDB happens to return.
    """

    if not series:
        return None

    target = dt_util.now() + timedelta(hours=hours)
    for item in series:
        if not isinstance(item, dict):
            continue
        timestamp = dt_util.parse_datetime(str(item.get("dt", "")))
        if timestamp is None:
            continue
        if dt_util.as_local(timestamp) >= target:
            return item
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Solar Cube sensors from a config entry."""

    # A configured Home Assistant currency wins; otherwise fall back rather
    # than publish the savings sensors with no unit and no device class, which
    # keeps them out of long-term statistics and the Energy dashboard.
    hass_currency_raw = getattr(hass.config, "currency", None)
    hass_currency = (
        hass_currency_raw.strip()
        if isinstance(hass_currency_raw, str) and hass_currency_raw.strip()
        else DEFAULT_CURRENCY
    )

    data = hass.data[DOMAIN][DATA_ENTRIES][entry.entry_id]
    data_coordinator: SolarCubeDataCoordinator = data["data_coordinator"]
    forecast_coordinator: SolarCubeForecastCoordinator = data["forecast_coordinator"]
    optimal_coordinator: SolarCubeOptimalActionsCoordinator = data["optimal_coordinator"]

    sensors: list[SensorEntity] = []

    for definition in data_coordinator.sensor_definitions:
        unit = definition.get("unit")
        device_class = definition.get("device_class")
        if unit == "currency":
            unit = hass_currency
        description = SensorEntityDescription(
            key=definition["key"],
            name=definition["name"],
            native_unit_of_measurement=unit,
            device_class=device_class,
            state_class=definition.get("state_class"),
        )
        sensors.append(
            SolarCubeValueSensor(data_coordinator, description, entry, definition)
        )

    sensors.append(SolarCubeForecastSensor(forecast_coordinator, entry))
    sensors.append(SolarCubeOptimalActionsSensor(optimal_coordinator, entry))

    # Derived forecast point sensors, selected by horizon rather than by index.
    for hours in FORECAST_HORIZONS:
        label = f"{hours}H"
        for key_prefix, name, value_key in FORECAST_POINT_SENSORS:
            sensors.append(
                SolarCubeSeriesPointSensor(
                    forecast_coordinator,
                    entry,
                    key=f"{key_prefix}_{hours}h",
                    name=f"{name} {label}",
                    hours=hours,
                    value_key=value_key,
                )
            )
        for action_key in OPTIMAL_ACTION_KEYS:
            sensors.append(
                SolarCubeSeriesPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_{action_key}_{hours}h",
                    name=f"Optimal {action_key.upper()} {label}",
                    hours=hours,
                    value_key=action_key,
                )
            )

    # Wh -> kWh lifetime totals (equivalent to the previous YAML template sensors).
    for key, name, source_key in KWH_TOTALS:
        sensors.append(
            SolarCubeKwhTotalSensor(
                data_coordinator, entry, key=key, name=name, source_key=source_key
            )
        )

    # Period meters (replacement for utility_meter + alias templates).
    for period in ("hourly", "daily"):
        for key_suffix, name, source_key in ENERGY_PERIOD_METERS:
            sensors.append(
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_{key_suffix}",
                    name=f"{period.capitalize()} {name}",
                    source_key=source_key,
                    source_unit="Wh",
                    unit=UnitOfEnergy.KILO_WATT_HOUR,
                    period=period,
                )
            )

    for period in ("hourly", "daily", "weekly", "monthly"):
        sensors.append(
            SolarCubePeriodMeterSensor(
                data_coordinator,
                entry,
                key=f"{period}_optimisation_savings",
                name=f"{period.capitalize()} Optimisation Savings",
                source_key=SAVINGS_SOURCE_KEY,
                source_unit="currency",
                unit=hass_currency,
                period=period,
            )
        )

    async_add_entities(sensors)


class SolarCubeEntity(CoordinatorEntity[Any], SensorEntity):
    """Common base: device grouping, stable unique_id, no polling.

    ``has_entity_name`` is deliberately NOT set. It would prefix every entity
    with the device name and therefore change generated entity_ids
    (sensor.hourly_optimisation_savings -> sensor.solar_cube_hourly_...).
    Existing installs keep their registry entries either way, but a fresh
    install would then produce ids the shipped dashboards do not reference, and
    appliance users cannot repoint cards themselves. Entity ids are part of this
    integration's contract with its dashboards; the device grouping below works
    independently of it.
    """

    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{_unique_id_prefix(entry)}_{key}"
        self._attr_device_info = _device_info(entry)


class SolarCubeValueSensor(SolarCubeEntity):
    """Representation of a scalar InfluxDB-backed sensor."""

    def __init__(
        self,
        coordinator: SolarCubeDataCoordinator,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        definition: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._attr_name = f"{entry.title} {description.name}"
        self._definition = definition

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        value = scale_value(
            self.entity_description.key, data.get(self.entity_description.key)
        )
        return _round_float(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {"last_refresh": data.get(LAST_UPDATE_KEY)}


class SolarCubeForecastSensor(SolarCubeEntity):
    """Sensor exposing the forecast payload as an attribute."""

    _attr_icon = "mdi:weather-sunny-alert"
    # The payload is large and changes wholesale every 30 minutes; keeping it out
    # of the recorder avoids needless database growth.
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self, coordinator: SolarCubeForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "forecast")
        self._attr_name = f"{entry.title} Energy Forecast"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"forecast": self.coordinator.data}


class SolarCubeOptimalActionsSensor(SolarCubeEntity):
    """Sensor exposing optimal actions as an attribute."""

    _attr_icon = "mdi:lightning-bolt"
    _unrecorded_attributes = frozenset({"optimal_actions"})

    def __init__(
        self,
        coordinator: SolarCubeOptimalActionsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry, "optimal_actions")
        self._attr_name = f"{entry.title} Optimal Actions"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"optimal_actions": self.coordinator.data}


class SolarCubeSeriesPointSensor(SolarCubeEntity):
    """A single point of a time series, selected by horizon in hours."""

    def __init__(
        self,
        coordinator: SolarCubeForecastCoordinator | SolarCubeOptimalActionsCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        hours: int,
        value_key: str,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_name = name
        self._hours = hours
        self._value_key = value_key

    @property
    def native_value(self) -> Any:
        item = _series_point_at(self.coordinator.data, self._hours)
        if item is None:
            return None
        return _round_float(item.get(self._value_key))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = _series_point_at(self.coordinator.data, self._hours)
        return {"forecast_time": item.get("dt") if item else None}


class SolarCubeKwhTotalSensor(SolarCubeEntity):
    """Lifetime Wh counter re-published in kWh."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: SolarCubeDataCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        source_key: str,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_name = name
        self._source_key = source_key

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        try:
            value = float(data.get(self._source_key))
        except (TypeError, ValueError):
            return None
        # A lifetime counter cannot be negative; zero is valid (e.g. at
        # commissioning) and must not be reported as unknown, which would
        # otherwise punch a gap into long-term statistics.
        if value < 0:
            return None
        return round(value / 1000.0, 5)


class SolarCubePeriodMeterSensor(
    CoordinatorEntity[SolarCubeDataCoordinator], RestoreEntity, SensorEntity
):
    """Accumulates the delta of a monotonic source counter over a period.

    State is recomputed on coordinator updates rather than inside ``native_value``
    so that the entity has no side effects when Home Assistant reads properties.
    """

    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"_baseline", "_last_total", "_carried"})

    def __init__(
        self,
        coordinator: SolarCubeDataCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        source_key: str,
        source_unit: str,
        unit: str | None,
        period: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{_unique_id_prefix(entry)}_{key}"
        self._attr_device_info = _device_info(entry)
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._source_key = source_key
        self._source_unit = source_unit
        self._period = period

        self._baseline: float | None = None
        self._last_total: float | None = None
        self._carried: float = 0.0
        self._period_key: str | None = None
        self._value: float | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

        if unit in (UnitOfEnergy.KILO_WATT_HOUR, UnitOfEnergy.WATT_HOUR):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL
        elif source_unit == "currency" and unit:
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_state_class = SensorStateClass.TOTAL

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            self._recalculate()
            return

        attrs = last.attributes or {}
        self._baseline = _as_float(attrs.get("_baseline"))
        self._last_total = _as_float(attrs.get("_last_total"))
        self._carried = _as_float(attrs.get("_carried")) or 0.0
        period_key = attrs.get("_period_key")
        self._period_key = period_key if isinstance(period_key, str) else None
        self._recalculate()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._recalculate()
        super()._handle_coordinator_update()

    def _period_start(self) -> datetime:
        """Return the local start of the current accumulation period."""

        local_now = dt_util.now()

        if self._period == "hourly":
            return local_now.replace(minute=0, second=0, microsecond=0)
        if self._period == "daily":
            return local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._period == "weekly":
            # Week starts Sunday 00:00 local (matches the shipped cron: 0 0 * * 7).
            days_since_sunday = (local_now.weekday() + 1) % 7
            midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            return midnight - timedelta(days=days_since_sunday)
        if self._period == "monthly":
            return local_now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        return local_now

    def _convert(self, value: float) -> float:
        if (
            self._source_unit == "Wh"
            and self._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        ):
            return value / 1000.0
        return value

    def _recalculate(self) -> None:
        """Recompute the period total from the current source counter."""

        data = self.coordinator.data or {}
        try:
            total = float(data.get(self._source_key))
        except (TypeError, ValueError):
            # Source temporarily unavailable: keep the last known value.
            return

        period_start = self._period_start()
        period_key = period_start.isoformat()

        if period_key != self._period_key:
            # New period (or first run): start from zero.
            self._period_key = period_key
            self._baseline = total
            self._carried = 0.0
        elif self._baseline is None:
            self._baseline = total
        elif self._last_total is not None and total < self._last_total:
            # The source counter reset. Bank what was accumulated before the
            # reset instead of discarding it, then rebase onto the new counter.
            self._carried += max(self._last_total - self._baseline, 0.0)
            self._baseline = total

        self._last_total = total
        delta = self._carried + max(total - self._baseline, 0.0)

        self._value = round(max(self._convert(delta), 0.0), 5)
        self._attr_last_reset = period_start
        self._attr_extra_state_attributes = {
            "_period_key": self._period_key,
            "_baseline": self._baseline,
            "_last_total": self._last_total,
            "_carried": self._carried,
        }

    @property
    def native_value(self) -> float | None:
        return self._value


def _as_float(value: Any) -> float | None:
    """Best-effort float conversion used when restoring persisted state."""

    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
