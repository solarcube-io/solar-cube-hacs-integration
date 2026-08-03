"""The shipped dashboards must reference entities that actually exist.

Entity ids are part of this integration's contract with its own dashboards: the
appliance imports them automatically and its users cannot repoint a broken card.
A change that looks purely cosmetic -- adopting ``has_entity_name``, renaming a
sensor -- silently changes generated ids on fresh installs, so the contract is
checked against a real setup rather than by eye.

Two sources of entities are legitimate:

* sensors this integration creates, and
* cost sensors ``homeassistant.components.energy`` derives from the Energy
  dashboard template this integration ships.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_cube import const as C

from homeassistant.const import CONF_NAME, CONF_TOKEN, CONF_URL
from homeassistant.helpers import entity_registry as er

DASHBOARDS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "solar_cube"
    / "dashboards"
)
DASHBOARD_FILES = sorted(DASHBOARDS.glob("*_solar_cube_*.yaml"))

ENTRY_DATA = {
    CONF_NAME: "Solar Cube",
    CONF_URL: "http://influx:8086",
    CONF_TOKEN: "tok",
    C.CONF_ORG: "solarcube",
    C.CONF_DATA_BUCKET: "db",
    C.CONF_AGENTS_BUCKET: "agents",
    C.CONF_IMPORT_DASHBOARDS: False,
    C.CONF_CONFIGURE_ENERGY_DASHBOARD: False,
    C.CONF_S1_LCD_DISPLAY: False,
    C.CONF_RUN_FRONTEND_INSTALLER: False,
}


@pytest.fixture
async def created_entity_ids(hass) -> set[str]:
    """Entity ids a fresh install actually produces."""
    with patch("custom_components.solar_cube.SolarCubeApi", autospec=True) as api_cls:
        api = api_cls.return_value
        api.async_query_last_batch = AsyncMock(return_value={})
        api.async_get_forecast = AsyncMock(return_value=[])
        api.async_get_optimal_actions = AsyncMock(return_value=[])
        api.close = MagicMock()
        entry = MockConfigEntry(
            domain=C.DOMAIN, unique_id=C.DOMAIN, title="Solar Cube", data=ENTRY_DATA
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    return {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def referenced(path: Path) -> set[str]:
    """Sensor entity ids a dashboard binds to."""
    return set(re.findall(r"entity:\s*(sensor\.[a-z0-9_]+)", path.read_text("utf-8")))


def energy_template() -> dict:
    return json.loads((DASHBOARDS / "energy.json").read_text("utf-8"))


def energy_cost_entity_ids() -> set[str]:
    """Cost sensors Home Assistant derives from our Energy dashboard template.

    When a grid flow carries a price, ``homeassistant.components.energy`` creates
    ``<stat>_cost`` / ``<stat>_compensation`` sensors. They belong to the energy
    integration rather than to this one, so they never show up among our config
    entry's registry entries -- but the panel legitimately uses them, and v0.1.9
    deleted this integration's own duplicates precisely because they exist.

    Derived with Home Assistant's own SOURCE_ADAPTERS so the rule cannot drift.
    """
    from homeassistant.components.energy.sensor import SOURCE_ADAPTERS

    derived: set[str] = set()
    for source in energy_template()["data"]["energy_sources"]:
        for adapter in SOURCE_ADAPTERS:
            if adapter.source_type != source["type"]:
                continue
            flows = source.get(adapter.flow_type) if adapter.flow_type else [source]
            for flow in flows or []:
                stat = flow.get(adapter.stat_energy_key)
                priced = (
                    flow.get(adapter.total_money_key)
                    or flow.get("entity_energy_price")
                    or flow.get("number_energy_price") is not None
                )
                if stat and priced:
                    derived.add(f"{stat}_{adapter.entity_id_suffix}")
    return derived


def energy_template_entity_ids() -> set[str]:
    """Every entity the Energy dashboard template points at."""
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str) and value.startswith("sensor."):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(energy_template())
    return found


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
async def test_every_referenced_entity_exists(path, created_entity_ids) -> None:
    available = created_entity_ids | energy_cost_entity_ids()
    missing = sorted(referenced(path) - available)
    assert not missing, f"{path.name} references entities that will not exist: {missing}"


async def test_energy_template_entities_exist(created_entity_ids) -> None:
    """Every sensor the Energy dashboard template configures must exist.

    A typo here does not raise: Home Assistant stores the preference happily and
    the corresponding graph is simply blank forever.
    """
    missing = sorted(energy_template_entity_ids() - created_entity_ids)
    assert not missing, f"energy.json points at non-existent entities: {missing}"


async def test_cost_sensors_the_panel_uses_stay_derivable() -> None:
    """The panel's import/export value rows depend on the energy template.

    If a future edit drops the price from a grid flow, Home Assistant stops
    creating these and the card silently goes unavailable.
    """
    derived = energy_cost_entity_ids()
    for entity_id in (
        "sensor.grid_buy_active_energy_total_cost",
        "sensor.grid_sell_active_energy_total_compensation",
    ):
        assert entity_id in derived, (
            f"{entity_id} is used by the panel but is no longer derivable from "
            "dashboards/energy.json"
        )


def _cards_titled(node, wanted: str, out: list) -> None:
    if isinstance(node, dict):
        if node.get("title") == wanted:
            out.extend(node["entities"])
        for value in node.values():
            _cards_titled(value, wanted, out)
    elif isinstance(node, list):
        for value in node:
            _cards_titled(value, wanted, out)


async def test_savings_card_shows_a_total() -> None:
    """The panel's savings card must end with the lifetime total."""
    for lang, title in (
        ("en", "Storage optimisation savings"),
        ("pl", "Oszczędności z pracy magazynu"),
    ):
        data = yaml.safe_load(
            (DASHBOARDS / f"panel_solar_cube_{lang}.yaml").read_text("utf-8")
        )
        found: list[dict] = []
        _cards_titled(data, title, found)
        assert found, f"{lang}: savings card not found"

        entities = [e["entity"] for e in found]
        assert entities[-1] == "sensor.solar_cube_optimised_energy_total_savings", (
            f"{lang}: total savings must be the last row, got {entities}"
        )
        assert "sensor.hourly_optimisation_savings" not in entities, (
            f"{lang}: the hourly row should have been removed"
        )
