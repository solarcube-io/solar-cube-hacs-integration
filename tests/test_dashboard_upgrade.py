"""Shipped dashboards must be able to reach an install that already has them.

The original import was one-shot: once a dashboard existed, a newer shipped copy
could never replace it, and there was no way to clear the marker from the UI.
Deleting the dashboard did not help either, because the marker was checked first.
These tests pin the four cases an upgrade has to tell apart.
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.solar_cube as sc
from custom_components.solar_cube import const as C

from homeassistant.const import CONF_NAME, CONF_TOKEN, CONF_URL
from homeassistant.helpers import issue_registry as ir

BASE_DATA = {
    CONF_NAME: "Solar Cube",
    CONF_URL: "http://influx:8086",
    CONF_TOKEN: "tok",
    C.CONF_ORG: "solarcube",
    C.CONF_DATA_BUCKET: "db",
    C.CONF_AGENTS_BUCKET: "agents",
    C.CONF_IMPORT_DASHBOARDS: True,
    C.CONF_CONFIGURE_ENERGY_DASHBOARD: False,
    C.CONF_S1_LCD_DISPLAY: False,
    C.CONF_RUN_FRONTEND_INSTALLER: False,
}

SHIPPED_V1 = {"views": [{"title": "v1", "cards": []}]}
SHIPPED_V2 = {"views": [{"title": "v2", "cards": []}]}
USER_EDITED = {"views": [{"title": "mine", "cards": [{"type": "markdown"}]}]}


class FakeStore:
    """Stands in for LovelaceStorage: one config document per url_path."""

    documents: ClassVar[dict[str, dict]] = {}

    def __init__(self, hass, item):
        self.url_path = item["url_path"]

    async def async_load(self, force):
        from homeassistant.components.lovelace.dashboard import ConfigNotFound

        if self.url_path not in self.documents:
            raise ConfigNotFound
        return self.documents[self.url_path]

    async def async_save(self, config):
        self.documents[self.url_path] = config


@pytest.fixture
def lovelace(hass, tmp_path):
    """Patch out Lovelace so the seeding logic can be exercised directly."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    FakeStore.documents = {}
    hass.config.config_dir = str(tmp_path)

    packaged = tmp_path / "packaged"
    packaged.mkdir()
    for spec in C.DASHBOARD_SPECS["en"]:
        (packaged / spec["filename"]).write_text(yaml.safe_dump(SHIPPED_V1))

    hass.data[LOVELACE_DATA] = MagicMock(dashboards={})

    created: list[dict] = []
    collection = MagicMock()
    collection.async_load = AsyncMock()
    collection.async_items = MagicMock(side_effect=lambda: list(created))

    async def _create(payload):
        item = {**payload, "id": payload["url_path"]}
        created.append(item)
        return item

    collection.async_create_item = AsyncMock(side_effect=_create)

    with (
        patch.object(sc, "PACKAGED_DASHBOARDS_DIR", packaged),
        patch("homeassistant.components.lovelace.dashboard.DashboardsCollection",
              return_value=collection),
        patch("homeassistant.components.lovelace.dashboard.LovelaceStorage", FakeStore),
        patch.object(sc, "async_register_built_in_panel", MagicMock()),
    ):
        yield {"dir": packaged, "collection": collection}


def ship(lovelace, config) -> None:
    for spec in C.DASHBOARD_SPECS["en"]:
        (lovelace["dir"] / spec["filename"]).write_text(yaml.safe_dump(config))


def make_entry(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=C.DOMAIN, unique_id=C.DOMAIN, title="Solar Cube",
        data=BASE_DATA, options=options,
    )
    entry.add_to_hass(hass)
    sc._domain_data(hass)[C.DATA_ENTRIES][entry.entry_id] = {}
    return entry


async def seed(hass, entry, reapply: bool = False) -> bool:
    return await sc._async_ensure_storage_dashboards(
        hass, entry, {**entry.data, **entry.options}, reapply=reapply
    )


def first_url() -> str:
    return C.DASHBOARD_SPECS["en"][0]["url_path"]


class TestFirstInstall:
    async def test_absent_dashboards_are_created_and_seeded(
        self, hass, lovelace
    ) -> None:
        entry = make_entry(hass)
        assert await seed(hass, entry) is True
        assert FakeStore.documents[first_url()] == SHIPPED_V1
        assert entry.options[C.OPT_SEEDED_DASHBOARDS][first_url()]


class TestUpgrade:
    async def test_unchanged_shipped_copy_is_left_alone(self, hass, lovelace) -> None:
        entry = make_entry(hass)
        await seed(hass, entry)
        before = dict(entry.options[C.OPT_SEEDED_DASHBOARDS])

        assert await seed(hass, entry) is False
        assert entry.options[C.OPT_SEEDED_DASHBOARDS] == before

    async def test_newer_shipped_copy_refreshes_an_untouched_dashboard(
        self, hass, lovelace
    ) -> None:
        """The case that was impossible before: an existing install picking up a
        corrected dashboard."""
        entry = make_entry(hass)
        await seed(hass, entry)
        assert FakeStore.documents[first_url()] == SHIPPED_V1

        ship(lovelace, SHIPPED_V2)
        assert await seed(hass, entry) is True
        assert FakeStore.documents[first_url()] == SHIPPED_V2

    async def test_a_user_edited_dashboard_is_not_overwritten(
        self, hass, lovelace
    ) -> None:
        entry = make_entry(hass)
        await seed(hass, entry)
        FakeStore.documents[first_url()] = USER_EDITED

        ship(lovelace, SHIPPED_V2)
        await seed(hass, entry)
        assert FakeStore.documents[first_url()] == USER_EDITED

    async def test_a_user_edited_dashboard_raises_a_repairs_issue(
        self, hass, lovelace
    ) -> None:
        entry = make_entry(hass)
        await seed(hass, entry)
        FakeStore.documents[first_url()] = USER_EDITED

        ship(lovelace, SHIPPED_V2)
        await seed(hass, entry)

        issue = ir.async_get(hass).async_get_issue(C.DOMAIN, C.ISSUE_DASHBOARDS_OUTDATED)
        assert issue is not None
        assert issue.translation_placeholders["dashboards"]

    async def test_reapply_overwrites_a_user_edited_dashboard(
        self, hass, lovelace
    ) -> None:
        entry = make_entry(hass)
        await seed(hass, entry)
        FakeStore.documents[first_url()] = USER_EDITED

        ship(lovelace, SHIPPED_V2)
        assert await seed(hass, entry, reapply=True) is True
        assert FakeStore.documents[first_url()] == SHIPPED_V2

    async def test_the_issue_clears_once_everything_is_current(
        self, hass, lovelace
    ) -> None:
        entry = make_entry(hass)
        await seed(hass, entry)
        FakeStore.documents[first_url()] = USER_EDITED
        ship(lovelace, SHIPPED_V2)
        await seed(hass, entry)
        assert ir.async_get(hass).async_get_issue(
            C.DOMAIN, C.ISSUE_DASHBOARDS_OUTDATED
        )

        await seed(hass, entry, reapply=True)
        assert (
            ir.async_get(hass).async_get_issue(C.DOMAIN, C.ISSUE_DASHBOARDS_OUTDATED)
            is None
        )


class TestDeletedByUser:
    async def test_a_deleted_dashboard_is_recreated(self, hass, lovelace) -> None:
        """What the user tried by hand: delete it and expect a re-import."""
        entry = make_entry(hass)
        await seed(hass, entry)

        from homeassistant.components.lovelace.const import LOVELACE_DATA

        FakeStore.documents.clear()
        hass.data[LOVELACE_DATA].dashboards.clear()
        lovelace["collection"].async_items.side_effect = list

        assert await seed(hass, entry) is True
        assert FakeStore.documents[first_url()] == SHIPPED_V1


class TestLegacyInstall:
    async def test_an_install_predating_fingerprints_is_adopted_not_clobbered(
        self, hass, lovelace
    ) -> None:
        """Existing installs carry the old marker and no fingerprints. Their
        dashboards must be adopted, not silently replaced."""
        entry = make_entry(hass, **{C.OPT_STORAGE_DASHBOARDS_IMPORTED: True})
        FakeStore.documents[first_url()] = USER_EDITED

        await seed(hass, entry)

        assert FakeStore.documents[first_url()] == USER_EDITED
        assert first_url() in entry.options[C.OPT_SEEDED_DASHBOARDS]

    async def test_reapply_updates_a_legacy_install(self, hass, lovelace) -> None:
        entry = make_entry(hass, **{C.OPT_STORAGE_DASHBOARDS_IMPORTED: True})
        FakeStore.documents[first_url()] = USER_EDITED

        assert await seed(hass, entry, reapply=True) is True
        assert FakeStore.documents[first_url()] == SHIPPED_V1


class TestAutomations:
    @pytest.fixture
    def packaged(self, hass, tmp_path):
        hass.config.config_dir = str(tmp_path)
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        with patch.object(sc, "PACKAGED_DASHBOARDS_DIR", pkg):
            yield pkg

    async def test_existing_automations_are_left_alone_by_default(
        self, hass, packaged, tmp_path
    ) -> None:
        (packaged / "automations.yaml").write_text(
            yaml.safe_dump([{"id": "sc1", "alias": "Old", "action": []}])
        )
        await sc._async_ensure_automations(hass)

        (packaged / "automations.yaml").write_text(
            yaml.safe_dump([{"id": "sc1", "alias": "New", "action": []}])
        )
        sc._domain_data(hass).pop(C.DATA_AUTOMATIONS_IMPORTED, None)
        await sc._async_ensure_automations(hass)

        written = yaml.safe_load((tmp_path / "automations.yaml").read_text())
        assert written[0]["alias"] == "Old"

    async def test_reapply_updates_a_shipped_automation_by_id(
        self, hass, packaged, tmp_path
    ) -> None:
        (packaged / "automations.yaml").write_text(
            yaml.safe_dump([{"id": "sc1", "alias": "Old", "action": []}])
        )
        await sc._async_ensure_automations(hass)

        (packaged / "automations.yaml").write_text(
            yaml.safe_dump([{"id": "sc1", "alias": "New", "action": []}])
        )
        assert await sc._async_ensure_automations(hass, update=True) is True

        written = yaml.safe_load((tmp_path / "automations.yaml").read_text())
        assert written[0]["alias"] == "New"

    async def test_reapply_does_not_touch_the_users_own_automations(
        self, hass, packaged, tmp_path
    ) -> None:
        (tmp_path / "automations.yaml").write_text(
            yaml.safe_dump([{"id": "mine", "alias": "Mine", "action": []}])
        )
        (packaged / "automations.yaml").write_text(
            yaml.safe_dump([{"id": "sc1", "alias": "Shipped", "action": []}])
        )
        await sc._async_ensure_automations(hass, update=True)

        written = yaml.safe_load((tmp_path / "automations.yaml").read_text())
        assert {a["id"] for a in written} == {"mine", "sc1"}
        assert next(a for a in written if a["id"] == "mine")["alias"] == "Mine"
