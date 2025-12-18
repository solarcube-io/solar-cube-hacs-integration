"""Sensor platform for Solar Cube."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import (
    SolarCubeDataCoordinator,
    SolarCubeForecastCoordinator,
    SolarCubeOptimalActionsCoordinator,
)


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


async def _async_cleanup_orphaned_entities(hass: HomeAssistant) -> None:
    """Remove entity registry entries from previous uninstalls.

    Home Assistant may keep entities from removed config entries in the registry.
    When the integration is re-installed, new entities are created and HA
    auto-suffixes entity_ids with _2/_3/... . Cleaning orphaned entries avoids that.
    """

    ent_reg = er.async_get(hass)
    active_entry_ids = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}

    # Iterate all registry entries and remove those created by our platform
    # that belong to config entries that no longer exist.
    for entity_entry in list(ent_reg.entities.values()):
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.config_entry_id and entity_entry.config_entry_id not in active_entry_ids:
            ent_reg.async_remove(entity_entry.entity_id)


@dataclass
class SolarCubeSensorEntityDescription(SensorEntityDescription):
    key: str


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    await _async_cleanup_orphaned_entities(hass)

    hass_currency_raw = getattr(hass.config, "currency", None)
    hass_currency = hass_currency_raw.strip() if isinstance(hass_currency_raw, str) and hass_currency_raw.strip() else None

    data = hass.data[DOMAIN][entry.entry_id]
    data_coordinator: SolarCubeDataCoordinator = data["data_coordinator"]
    forecast_coordinator: SolarCubeForecastCoordinator = data["forecast_coordinator"]
    optimal_coordinator: SolarCubeOptimalActionsCoordinator = data["optimal_coordinator"]

    sensors: list[SensorEntity] = []
    for definition in data_coordinator.sensor_definitions:
        unit = definition.get("unit")
        device_class = definition.get("device_class")
        if unit == "currency":
            unit = hass_currency
            # Only mark as monetary if HA currency is configured.
            if unit is None:
                device_class = None
        description = SolarCubeSensorEntityDescription(
            key=definition["key"],
            name=definition["name"],
            native_unit_of_measurement=unit,
            device_class=device_class,
            state_class=definition.get("state_class"),
        )
        sensors.append(SolarCubeValueSensor(data_coordinator, description, entry, definition))

    sensors.append(SolarCubeForecastSensor(forecast_coordinator, entry))
    sensors.append(SolarCubeOptimalActionsSensor(optimal_coordinator, entry))

    # Derived monetary totals used by the shipped dashboards.
    sensors.extend(
        [
            SolarCubeTotalValueFromPriceSensor(
                data_coordinator,
                entry,
                key="grid_buy_active_energy_total_cost",
                name="Grid Buy Active Energy Total Cost",
                energy_source_key="grid_buy_active_energy",
                price_key="buy_energy_price",
                currency=hass_currency,
            ),
            SolarCubeTotalValueFromPriceSensor(
                data_coordinator,
                entry,
                key="grid_sell_active_energy_total_compensation",
                name="Grid Sell Active Energy Total Compensation",
                energy_source_key="grid_sell_active_energy",
                price_key="sell_energy_price",
                currency=hass_currency,
            ),
        ]
    )

    # Derived/template-like forecast point sensors.
    sensors.extend(
        [
            SolarCubeForecastPointSensor(
                forecast_coordinator,
                entry,
                key="forecasted_production_1h",
                name="SolarCube Forecasted Production 1H",
                index=3,
                value_key="pf",
            ),
            SolarCubeForecastPointSensor(
                forecast_coordinator,
                entry,
                key="forecasted_consumption_1h",
                name="SolarCube Forecasted Consumption 1H",
                index=3,
                value_key="cf",
            ),
            SolarCubeForecastPointSensor(
                forecast_coordinator,
                entry,
                key="soc_forecast_1h",
                name="SolarCube SoC Forecast 1H",
                index=3,
                value_key="sf",
            ),
            SolarCubeForecastPointSensor(
                forecast_coordinator,
                entry,
                key="forecasted_production_6h",
                name="SolarCube Forecasted Production 6H",
                index=23,
                value_key="pf",
            ),
            SolarCubeForecastPointSensor(
                forecast_coordinator,
                entry,
                key="forecasted_consumption_6h",
                name="SolarCube Forecasted Consumption 6H",
                index=23,
                value_key="cf",
            ),
            SolarCubeForecastPointSensor(
                forecast_coordinator,
                entry,
                key="soc_forecast_6h",
                name="SolarCube SoC Forecast 6H",
                index=23,
                value_key="sf",
            ),
        ]
    )

    # Derived/template-like optimal action point sensors.
    for horizon_key, idx in (("1h", 3), ("6h", 23)):
        sensors.extend(
            [
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_gb_{horizon_key}",
                    name=f"SolarCube Optimal GB {horizon_key.upper()}",
                    index=idx,
                    value_key="gb",
                ),
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_bg_{horizon_key}",
                    name=f"SolarCube Optimal BG {horizon_key.upper()}",
                    index=idx,
                    value_key="bg",
                ),
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_bc_{horizon_key}",
                    name=f"SolarCube Optimal BC {horizon_key.upper()}",
                    index=idx,
                    value_key="bc",
                ),
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_gc_{horizon_key}",
                    name=f"SolarCube Optimal GC {horizon_key.upper()}",
                    index=idx,
                    value_key="gc",
                ),
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_pb_{horizon_key}",
                    name=f"SolarCube Optimal PB {horizon_key.upper()}",
                    index=idx,
                    value_key="pb",
                ),
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_pc_{horizon_key}",
                    name=f"SolarCube Optimal PC {horizon_key.upper()}",
                    index=idx,
                    value_key="pc",
                ),
                SolarCubeOptimalActionPointSensor(
                    optimal_coordinator,
                    entry,
                    key=f"optimal_pg_{horizon_key}",
                    name=f"SolarCube Optimal PG {horizon_key.upper()}",
                    index=idx,
                    value_key="pg",
                ),
            ]
        )

    # Wh → kWh totals (equivalent to the YAML template sensors).
    kwh_totals: list[tuple[str, str, str]] = [
        ("ess_discharged_energy", "ESS Discharged Energy", "ess_discharge_energy"),
        ("ess_charged_energy", "ESS Charged Energy", "ess_charge_energy"),
        ("grid_buy_active_energy_total", "Grid Buy Active Energy Total", "grid_buy_active_energy"),
        ("grid_sell_active_energy_total", "Grid Sell Active Energy Total", "grid_sell_active_energy"),
        ("pv_active_energy_total", "PV Active Energy Total", "pv_active_energy"),
        ("consumption_active_energy_total", "Consumption Active Energy Total", "consumption_active_energy"),
    ]
    for key, name, source_key in kwh_totals:
        sensors.append(
            SolarCubeKwhTotalSensor(
                data_coordinator,
                entry,
                key=key,
                name=name,
                source_key=source_key,
            )
        )

    # Period meters (replacement for utility_meter + alias templates).
    for period in ("hourly", "daily"):
        sensors.extend(
            [
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_grid_sell_energy",
                    name=f"{period.capitalize()} Grid Sell Energy",
                    source_key="grid_sell_active_energy",
                    source_unit="Wh",
                    unit="kWh",
                    period=period,
                ),
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_grid_buy_energy",
                    name=f"{period.capitalize()} Grid Buy Energy",
                    source_key="grid_buy_active_energy",
                    source_unit="Wh",
                    unit="kWh",
                    period=period,
                ),
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_pv_energy",
                    name=f"{period.capitalize()} PV Energy",
                    source_key="pv_active_energy",
                    source_unit="Wh",
                    unit="kWh",
                    period=period,
                ),
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_consumption_energy",
                    name=f"{period.capitalize()} Consumption Energy",
                    source_key="consumption_active_energy",
                    source_unit="Wh",
                    unit="kWh",
                    period=period,
                ),
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_ess_charge_energy",
                    name=f"{period.capitalize()} ESS Charge Energy",
                    source_key="ess_charge_energy",
                    source_unit="Wh",
                    unit="kWh",
                    period=period,
                ),
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_ess_discharge_energy",
                    name=f"{period.capitalize()} ESS Discharge Energy",
                    source_key="ess_discharge_energy",
                    source_unit="Wh",
                    unit="kWh",
                    period=period,
                ),
                SolarCubePeriodMeterSensor(
                    data_coordinator,
                    entry,
                    key=f"{period}_optimisation_savings",
                    name=f"{period.capitalize()} Optimisation Savings",
                    source_key="optimised_energy_total_savings",
                    source_unit="currency",
                    unit=hass_currency,
                    period=period,
                ),
            ]
        )

    sensors.extend(
        [
            SolarCubePeriodMeterSensor(
                data_coordinator,
                entry,
                key="weekly_optimisation_savings",
                name="Weekly Optimisation Savings",
                source_key="optimised_energy_total_savings",
                source_unit="currency",
                unit=hass_currency,
                period="weekly",
            ),
            SolarCubePeriodMeterSensor(
                data_coordinator,
                entry,
                key="monthly_optimisation_savings",
                name="Monthly Optimisation Savings",
                source_key="optimised_energy_total_savings",
                source_unit="currency",
                unit=hass_currency,
                period="monthly",
            ),
        ]
    )

    async_add_entities(sensors)


class SolarCubeValueSensor(CoordinatorEntity[SolarCubeDataCoordinator], SensorEntity):
    """Representation of a scalar InfluxDB-backed sensor."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: SolarCubeDataCoordinator,
        description: SolarCubeSensorEntityDescription,
        entry: ConfigEntry,
        definition: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._definition = definition
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_{description.key}"
        self._attr_name = f"{entry.title} {description.name}"

    @property
    def native_value(self):
        return _round_float(self.coordinator.data.get(self.entity_description.key))

    @property
    def extra_state_attributes(self):
        return {"last_refresh": self.coordinator.data.get("_last_update")}


class SolarCubeForecastSensor(CoordinatorEntity[SolarCubeForecastCoordinator], SensorEntity):
    """Sensor exposing forecast payload as attribute."""

    _attr_icon = "mdi:weather-sunny-alert"
    _attr_should_poll = False

    def __init__(self, coordinator: SolarCubeForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_forecast"
        self._attr_name = f"{entry.title} Energy Forecast"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        return {"forecast": self.coordinator.data}


class SolarCubeOptimalActionsSensor(CoordinatorEntity[SolarCubeOptimalActionsCoordinator], SensorEntity):
    """Sensor exposing optimal actions as attribute."""

    _attr_icon = "mdi:lightning-bolt"
    _attr_should_poll = False

    def __init__(self, coordinator: SolarCubeOptimalActionsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_optimal_actions"
        self._attr_name = f"{entry.title} Optimal Actions"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        return {"optimal_actions": self.coordinator.data}


class SolarCubeForecastPointSensor(CoordinatorEntity[SolarCubeForecastCoordinator], SensorEntity):
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: SolarCubeForecastCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        index: int,
        value_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._value_key = value_key
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_{key}"
        self._attr_name = name

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data or len(data) <= self._index:
            return None
        item = data[self._index]
        if not isinstance(item, dict):
            return None
        return _round_float(item.get(self._value_key))


class SolarCubeOptimalActionPointSensor(CoordinatorEntity[SolarCubeOptimalActionsCoordinator], SensorEntity):
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: SolarCubeOptimalActionsCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        index: int,
        value_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._value_key = value_key
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_{key}"
        self._attr_name = name

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data or len(data) <= self._index:
            return None
        item = data[self._index]
        if not isinstance(item, dict):
            return None
        return _round_float(item.get(self._value_key))


class SolarCubeKwhTotalSensor(CoordinatorEntity[SolarCubeDataCoordinator], SensorEntity):
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = "energy"
    _attr_state_class = "total_increasing"

    def __init__(
        self,
        coordinator: SolarCubeDataCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        source_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_key = source_key
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_{key}"
        self._attr_name = name

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._source_key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        # Match the YAML templates: treat non-positive as unavailable.
        if value <= 0:
            return None
        return round(value / 1000.0, 5)


class SolarCubePeriodMeterSensor(
    CoordinatorEntity[SolarCubeDataCoordinator], RestoreEntity, SensorEntity
):
    _attr_should_poll = False

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
        self._source_key = source_key
        self._source_unit = source_unit
        self._period = period
        self._attr_native_unit_of_measurement = unit
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_{key}"
        self._attr_name = name
        self._baseline: float | None = None
        self._last_total: float | None = None
        self._period_key: str | None = None
        self._attr_extra_state_attributes = {}

        if unit in ("kWh", "Wh"):
            self._attr_device_class = "energy"
            self._attr_state_class = "total"
        elif self._source_unit == "currency" and unit:
            self._attr_device_class = "monetary"
            self._attr_state_class = "total"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if not last:
            return
        attrs = last.attributes or {}
        try:
            baseline_raw = attrs.get("_baseline")
            self._baseline = float(baseline_raw) if isinstance(baseline_raw, (int, float, str)) else None
        except (TypeError, ValueError):
            self._baseline = None
        try:
            last_total_raw = attrs.get("_last_total")
            self._last_total = float(last_total_raw) if isinstance(last_total_raw, (int, float, str)) else None
        except (TypeError, ValueError):
            self._last_total = None
        pk = attrs.get("_period_key")
        self._period_key = pk if isinstance(pk, str) else None

    def _current_period_key(self) -> str:
        now = dt_util.now()
        local_now = dt_util.as_local(now)

        if self._period == "hourly":
            start = local_now.replace(minute=0, second=0, microsecond=0)
        elif self._period == "daily":
            start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif self._period == "weekly":
            # Week starts Sunday 00:00 local (matches the provided cron: 0 0 * * 7).
            days_since_sunday = (local_now.weekday() + 1) % 7
            d = (local_now - timedelta(days=days_since_sunday)).date()
            start = datetime(d.year, d.month, d.day, tzinfo=local_now.tzinfo)
        elif self._period == "monthly":
            start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = local_now
        return start.isoformat()

    def _convert(self, value: float) -> float:
        if self._source_unit == "Wh" and self._attr_native_unit_of_measurement == "kWh":
            return value / 1000.0
        return value

    @property
    def native_value(self):
        raw_total = self.coordinator.data.get(self._source_key)
        try:
            total = float(raw_total)
        except (TypeError, ValueError):
            return None

        pk = self._current_period_key()
        if self._period_key != pk or self._baseline is None:
            # Start of a new period (or first run).
            self._period_key = pk
            self._baseline = total
            self._last_total = total
            self._attr_extra_state_attributes = {
                "_period_key": self._period_key,
                "_baseline": self._baseline,
                "_last_total": self._last_total,
            }
            return 0.0

        # Handle counter resets.
        if self._last_total is not None and total < self._last_total:
            self._baseline = total

        self._last_total = total
        delta = total - (self._baseline or total)
        out = self._convert(delta)

        self._attr_extra_state_attributes = {
            "_period_key": self._period_key,
            "_baseline": self._baseline,
            "_last_total": self._last_total,
        }
        return round(max(out, 0.0), 5)


class SolarCubeTotalValueFromPriceSensor(
    CoordinatorEntity[SolarCubeDataCoordinator], SensorEntity
):
    _attr_should_poll = False
    _attr_state_class = "total"
    _attr_device_class = "monetary"

    def __init__(
        self,
        coordinator: SolarCubeDataCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        energy_source_key: str,
        price_key: str,
        currency: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._energy_source_key = energy_source_key
        self._price_key = price_key
        prefix = _unique_id_prefix(entry)
        self._attr_unique_id = f"{prefix}_{key}"
        self._attr_name = name
        # Use Home Assistant configured currency (ISO 4217 code, e.g. PLN/EUR/USD).
        self._attr_native_unit_of_measurement = currency
        if currency is None:
            self._attr_device_class = None

    @property
    def native_value(self):
        raw_energy_wh = self.coordinator.data.get(self._energy_source_key)
        raw_price = self.coordinator.data.get(self._price_key)

        try:
            energy_wh = float(raw_energy_wh)
            price_per_kwh = float(raw_price)
        except (TypeError, ValueError):
            return None

        if energy_wh <= 0:
            return None

        value = (energy_wh / 1000.0) * price_per_kwh
        return round(value, 5)
