"""Tests for the config, options and reauth flows."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_cube.api import (
    SolarCubeApiAuthError,
    SolarCubeApiRequestError,
)
from custom_components.solar_cube.const import (
    CONF_AGENTS_BUCKET,
    CONF_DATA_BUCKET,
    CONF_ORG,
    CONF_RUN_FRONTEND_INSTALLER,
    CONF_S1_LCD_BRIDGE_TOKEN,
    DEFAULT_S1_LCD_BRIDGE_TOKEN,
    DOMAIN,
    INTERNAL_OPTION_KEYS,
    OPT_ORPHAN_CLEANUP_DONE,
    normalize_language,
)

from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_NAME, CONF_TOKEN, CONF_URL

USER_INPUT = {
    CONF_NAME: "Solar Cube",
    CONF_URL: "http://influx:8086",
    CONF_TOKEN: "tok",
    CONF_ORG: "solarcube",
    CONF_DATA_BUCKET: "db",
    CONF_AGENTS_BUCKET: "agents",
}


@pytest.fixture
def mock_validate():
    with patch(
        "custom_components.solar_cube.config_flow.SolarCubeApi", autospec=True
    ) as api_cls:
        api_cls.return_value.async_validate = AsyncMock(return_value=None)
        yield api_cls


class TestNormalizeLanguage:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("pl", "pl"),
            ("pl-PL", "pl"),
            ("EN-gb", "en"),
            ("de", "en"),
            (None, "en"),
            ("", "en"),
        ],
    )
    def test_locale_tags_collapse_to_supported_codes(self, value, expected) -> None:
        assert normalize_language(value) == expected

    def test_unsupported_value_uses_the_fallback(self) -> None:
        assert normalize_language("de", "pl-PL") == "pl"
        assert normalize_language("de", "fr") == "en"


class TestUserFlow:
    async def test_happy_path_creates_the_entry(self, hass, mock_validate) -> None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_TOKEN] == "tok"

    async def test_frontend_installer_defaults_to_on(self, hass) -> None:
        """Appliance users have no filesystem access and cannot install the
        dashboard cards themselves, so the dashboards would be unusable without
        this running automatically."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        schema = result["data_schema"].schema
        key = next(k for k in schema if k == CONF_RUN_FRONTEND_INSTALLER)
        assert key.default() is True

    async def test_bridge_token_defaults_to_the_shared_value(self, hass) -> None:
        """Must match the bridge image default so the LCD works unconfigured."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        schema = result["data_schema"].schema
        key = next(k for k in schema if k == CONF_S1_LCD_BRIDGE_TOKEN)
        assert key.default() == DEFAULT_S1_LCD_BRIDGE_TOKEN
        assert DEFAULT_S1_LCD_BRIDGE_TOKEN

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (SolarCubeApiAuthError(), "invalid_auth"),
            (SolarCubeApiRequestError("x"), "cannot_connect"),
            (RuntimeError("boom"), "unknown"),
        ],
    )
    async def test_validation_errors_are_surfaced(
        self, hass, mock_validate, error, expected
    ) -> None:
        mock_validate.return_value.async_validate = AsyncMock(side_effect=error)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": expected}

    async def test_client_is_closed_even_when_construction_fails(self, hass) -> None:
        """Regression: the old finally-block referenced a possibly-unbound name."""
        from custom_components.solar_cube.config_flow import (
            _async_validate_connection,
        )

        with patch(
            "custom_components.solar_cube.config_flow.SolarCubeApi",
            side_effect=RuntimeError("cannot construct"),
        ):
            assert (
                await _async_validate_connection(
                    hass, "http://x", "t", "o", "db"
                )
                == "unknown"
            )

    async def test_missing_token_is_reported(self, hass, mock_validate) -> None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_TOKEN: "   "}
        )
        assert result["errors"] == {"base": "missing_token"}

    async def test_single_instance_only(self, hass, mock_validate) -> None:
        MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestOptionsFlow:
    @pytest.fixture
    def entry(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            title="Solar Cube",
            data=USER_INPUT,
            options={OPT_ORPHAN_CLEANUP_DONE: True, CONF_TOKEN: "old"},
        )
        entry.add_to_hass(hass)
        return entry

    async def test_internal_options_survive_an_options_save(
        self, hass, entry, mock_validate
    ) -> None:
        """Regression: async_create_entry replaces the whole options mapping, so
        one-shot markers were wiped and the imports re-ran on every save."""
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_URL: "http://influx:8086",
                CONF_ORG: "solarcube",
                CONF_DATA_BUCKET: "db",
                CONF_AGENTS_BUCKET: "agents",
                "import_dashboards": True,
                "configure_energy_dashboard": False,
            },
        )

        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][OPT_ORPHAN_CLEANUP_DONE] is True
        assert INTERNAL_OPTION_KEYS & set(result["data"])

    async def test_empty_token_keeps_the_existing_one(
        self, hass, entry, mock_validate
    ) -> None:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_URL: "http://influx:8086",
                CONF_TOKEN: "",
                CONF_ORG: "solarcube",
                CONF_DATA_BUCKET: "db",
                CONF_AGENTS_BUCKET: "agents",
                "import_dashboards": True,
                "configure_energy_dashboard": False,
            },
        )
        assert result["data"][CONF_TOKEN] == "old"

    async def test_new_token_replaces_the_old_one(
        self, hass, entry, mock_validate
    ) -> None:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_URL: "http://influx:8086",
                CONF_TOKEN: "brand-new",
                CONF_ORG: "solarcube",
                CONF_DATA_BUCKET: "db",
                CONF_AGENTS_BUCKET: "agents",
                "import_dashboards": True,
                "configure_energy_dashboard": False,
            },
        )
        assert result["data"][CONF_TOKEN] == "brand-new"


class TestReauthFlow:
    async def test_successful_reauth_stores_the_token_in_options_only(
        self, hass, mock_validate
    ) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN, unique_id=DOMAIN, data=USER_INPUT, options={}
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["step_id"] == "reauth_confirm"

        with patch.object(hass.config_entries, "async_reload", AsyncMock()):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_TOKEN: "fresh-token"}
            )

        assert result["type"] is data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.options[CONF_TOKEN] == "fresh-token"
        # The stale copy in data is never read, so it is no longer duplicated.
        assert entry.data[CONF_TOKEN] == "tok"
