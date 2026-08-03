"""Tests for setup helpers: automations merge, energy store, data layout."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import custom_components.solar_cube as solar_cube
from custom_components.solar_cube.const import (
    DATA_AUTOMATIONS_IMPORTED,
    DATA_ENTRIES,
    DOMAIN,
)

SHIPPED = [
    {"id": "solar_cube_1", "alias": "Solar Cube One", "action": []},
    {"id": "solar_cube_2", "alias": "Solar Cube Two", "action": []},
]


@pytest.fixture
def config_dir(hass, tmp_path: Path) -> Path:
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture
def shipped_automations(tmp_path: Path):
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "automations.yaml").write_text(
        yaml.safe_dump(SHIPPED), encoding="utf-8"
    )
    with patch.object(solar_cube, "PACKAGED_DASHBOARDS_DIR", packaged):
        yield packaged


class TestEnsureAutomations:
    async def test_creates_the_file_when_missing(
        self, hass, config_dir, shipped_automations
    ) -> None:
        assert await solar_cube._async_ensure_automations(hass) is True

        written = yaml.safe_load((config_dir / "automations.yaml").read_text())
        assert [a["id"] for a in written] == ["solar_cube_1", "solar_cube_2"]

    async def test_preserves_existing_user_automations(
        self, hass, config_dir, shipped_automations
    ) -> None:
        mine = [{"id": "mine", "alias": "My Automation", "action": []}]
        (config_dir / "automations.yaml").write_text(yaml.safe_dump(mine))

        assert await solar_cube._async_ensure_automations(hass) is True

        written = yaml.safe_load((config_dir / "automations.yaml").read_text())
        assert [a["id"] for a in written] == ["mine", "solar_cube_1", "solar_cube_2"]

    async def test_is_idempotent_by_id(
        self, hass, config_dir, shipped_automations
    ) -> None:
        existing = [dict(SHIPPED[0])]
        (config_dir / "automations.yaml").write_text(yaml.safe_dump(existing))

        assert await solar_cube._async_ensure_automations(hass) is True

        written = yaml.safe_load((config_dir / "automations.yaml").read_text())
        assert [a["id"] for a in written] == ["solar_cube_1", "solar_cube_2"]

    async def test_no_change_reports_false_and_leaves_the_file_alone(
        self, hass, config_dir, shipped_automations
    ) -> None:
        (config_dir / "automations.yaml").write_text(yaml.safe_dump(SHIPPED))
        before = (config_dir / "automations.yaml").read_text()

        assert await solar_cube._async_ensure_automations(hass) is False
        assert (config_dir / "automations.yaml").read_text() == before

    async def test_runs_only_once_per_runtime(
        self, hass, config_dir, shipped_automations
    ) -> None:
        assert await solar_cube._async_ensure_automations(hass) is True
        assert await solar_cube._async_ensure_automations(hass) is False
        assert hass.data[DOMAIN][DATA_AUTOMATIONS_IMPORTED] is True

    async def test_corrupt_existing_yaml_does_not_destroy_the_file(
        self, hass, config_dir, shipped_automations
    ) -> None:
        (config_dir / "automations.yaml").write_text("{[ not: valid: yaml")

        # Unparseable input is treated as "no known automations"; the shipped ones
        # are appended rather than the file being silently emptied.
        await solar_cube._async_ensure_automations(hass)
        written = yaml.safe_load((config_dir / "automations.yaml").read_text())
        assert [a["id"] for a in written] == ["solar_cube_1", "solar_cube_2"]

    async def test_non_list_yaml_is_ignored(
        self, hass, config_dir, shipped_automations
    ) -> None:
        (config_dir / "automations.yaml").write_text(yaml.safe_dump({"a": 1}))
        assert await solar_cube._async_ensure_automations(hass) is True


class TestEnergyDashboard:
    @pytest.fixture
    def template(self, tmp_path: Path):
        packaged = tmp_path / "packaged"
        packaged.mkdir(exist_ok=True)
        (packaged / "energy.json").write_text(
            json.dumps(
                {
                    "data": {
                        "energy_sources": [{"type": "grid"}],
                        "device_consumption": [],
                        "device_consumption_water": [],
                    }
                }
            )
        )
        with patch.object(solar_cube, "PACKAGED_DASHBOARDS_DIR", packaged):
            yield packaged

    async def test_creates_the_store_when_absent(
        self, hass, config_dir, template
    ) -> None:
        assert await solar_cube._async_configure_energy_dashboard(hass) is True

        stored = json.loads((config_dir / ".storage" / "energy").read_text())
        assert stored["key"] == "energy"
        assert stored["data"]["energy_sources"] == [{"type": "grid"}]

    async def test_preserves_unrelated_fields(
        self, hass, config_dir, template
    ) -> None:
        storage = config_dir / ".storage"
        storage.mkdir(parents=True)
        (storage / "energy").write_text(
            json.dumps(
                {
                    "version": 1,
                    "minor_version": 2,
                    "key": "energy",
                    "data": {
                        "energy_sources": [{"type": "old"}],
                        "device_consumption": [{"stat_consumption": "sensor.x"}],
                    },
                }
            )
        )

        assert await solar_cube._async_configure_energy_dashboard(hass) is True

        stored = json.loads((storage / "energy").read_text())
        assert stored["data"]["energy_sources"] == [{"type": "grid"}]
        # The user's device list must survive.
        assert stored["data"]["device_consumption"] == [
            {"stat_consumption": "sensor.x"}
        ]

    async def test_writes_a_backup_and_prunes_old_ones(
        self, hass, config_dir, template
    ) -> None:
        storage = config_dir / ".storage"
        storage.mkdir(parents=True)
        (storage / "energy").write_text(json.dumps({"data": {}}))
        for i in range(5):
            (storage / f"energy.bak.{1000 + i}").write_text("old")

        await solar_cube._async_configure_energy_dashboard(hass)

        backups = sorted(storage.glob("energy.bak.*"))
        assert len(backups) <= solar_cube.MAX_BACKUPS

    async def test_second_run_is_a_no_op(self, hass, config_dir, template) -> None:
        assert await solar_cube._async_configure_energy_dashboard(hass) is True
        assert await solar_cube._async_configure_energy_dashboard(hass) is False

    async def test_corrupt_store_is_left_untouched(
        self, hass, config_dir, template
    ) -> None:
        storage = config_dir / ".storage"
        storage.mkdir(parents=True)
        (storage / "energy").write_text("{ not json")

        assert await solar_cube._async_configure_energy_dashboard(hass) is False
        assert (storage / "energy").read_text() == "{ not json"


class TestDomainDataLayout:
    def test_entries_live_under_their_own_key(self, hass) -> None:
        """Regression: bookkeeping flags used to share the dict with entry ids,
        which made the "any entries left?" check in unload always false."""
        domain_data = solar_cube._domain_data(hass)
        domain_data[DATA_AUTOMATIONS_IMPORTED] = True
        domain_data[DATA_ENTRIES]["entry-1"] = {"api": None}

        assert set(domain_data[DATA_ENTRIES]) == {"entry-1"}

        domain_data[DATA_ENTRIES].pop("entry-1")
        assert not domain_data[DATA_ENTRIES]


class TestOneShotOptions:
    def test_no_change_does_not_arm_the_suppressor(self, hass) -> None:
        """Regression: arming it unconditionally left a stale count that
        swallowed the user's next genuine options change."""
        entry = MagicMock()
        entry.entry_id = "e1"
        entry.options = {"flag": False}

        state: dict = {}
        solar_cube._domain_data(hass)[DATA_ENTRIES]["e1"] = state

        with patch.object(hass.config_entries, "async_update_entry") as update:
            solar_cube._apply_one_shot_options(hass, entry, {"flag": False})

        update.assert_not_called()
        assert solar_cube.SUPPRESSED_RELOADS not in state

    def test_real_change_arms_the_suppressor_and_updates(self, hass) -> None:
        entry = MagicMock()
        entry.entry_id = "e1"
        entry.options = {"flag": True}

        state: dict = {}
        solar_cube._domain_data(hass)[DATA_ENTRIES]["e1"] = state

        with patch.object(hass.config_entries, "async_update_entry") as update:
            solar_cube._apply_one_shot_options(hass, entry, {"flag": False})

        update.assert_called_once()
        assert state[solar_cube.SUPPRESSED_RELOADS] == 1

    async def test_reload_listener_consumes_one_suppression_per_write(
        self, hass
    ) -> None:
        """Regression: a boolean suppressor let a second one-shot write reload
        the entry mid-setup. Setup performs several, so it must be a count."""
        entry = MagicMock()
        entry.entry_id = "e1"
        state = {solar_cube.SUPPRESSED_RELOADS: 2}
        solar_cube._domain_data(hass)[DATA_ENTRIES]["e1"] = state

        with patch.object(hass.config_entries, "async_reload") as reload:
            await solar_cube._async_reload_entry(hass, entry)
            await solar_cube._async_reload_entry(hass, entry)
            reload.assert_not_called()

            # Anything beyond the suppressed writes is a real user change.
            await solar_cube._async_reload_entry(hass, entry)
            reload.assert_called_once_with("e1")

    def test_several_one_shot_writes_suppress_all_their_callbacks(
        self, hass
    ) -> None:
        entry = MagicMock()
        entry.entry_id = "e1"
        entry.options = {}
        state: dict = {}
        solar_cube._domain_data(hass)[DATA_ENTRIES]["e1"] = state

        def apply(_entry, options=None, **_kw):
            _entry.options = options

        with patch.object(
            hass.config_entries, "async_update_entry", side_effect=apply
        ):
            solar_cube._apply_one_shot_options(hass, entry, {"a": False})
            solar_cube._apply_one_shot_options(hass, entry, {"b": False})

        assert state[solar_cube.SUPPRESSED_RELOADS] == 2


class TestInstallerRetry:
    """Appliance users cannot re-trigger the install, so a failed run must not
    disable itself permanently."""

    @pytest.fixture
    def entry(self, hass):
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        from custom_components.solar_cube.const import CONF_RUN_FRONTEND_INSTALLER

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            title="Solar Cube",
            options={CONF_RUN_FRONTEND_INSTALLER: True},
        )
        config_entry.add_to_hass(hass)
        solar_cube._domain_data(hass)[DATA_ENTRIES][config_entry.entry_id] = {}
        return config_entry

    async def test_failure_keeps_the_flag_on_for_the_next_restart(
        self, hass, entry
    ) -> None:
        from custom_components.solar_cube.const import CONF_RUN_FRONTEND_INSTALLER

        with (
            patch.object(
                solar_cube,
                "_async_run_frontend_installer",
                return_value=(1, "", "network unreachable"),
            ),
            patch.object(solar_cube, "_notify_dependency_install") as notify,
            patch.object(solar_cube, "_report_restart_required") as restart,
        ):
            await solar_cube._async_run_installer_once(hass, entry)

        assert entry.options[CONF_RUN_FRONTEND_INSTALLER] is True
        notify.assert_called_once()
        # No restart prompt for an install that did not actually happen.
        restart.assert_not_called()

    async def test_success_disables_the_flag_and_asks_for_a_restart(
        self, hass, entry
    ) -> None:
        from custom_components.solar_cube.const import CONF_RUN_FRONTEND_INSTALLER

        with (
            patch.object(
                solar_cube,
                "_async_run_frontend_installer",
                return_value=(0, "installed 12 cards", ""),
            ),
            patch.object(solar_cube, "_notify_dependency_install") as notify,
            patch.object(solar_cube, "_report_restart_required") as restart,
        ):
            await solar_cube._async_run_installer_once(hass, entry)

        assert entry.options[CONF_RUN_FRONTEND_INSTALLER] is False
        notify.assert_not_called()
        restart.assert_called_once()
