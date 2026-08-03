"""Config flow for Solar Cube HEMS."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol

from .api import SolarCubeApi, SolarCubeApiAuthError, SolarCubeApiRequestError
from .const import (
    CONF_AGENTS_BUCKET,
    CONF_CONFIGURE_ENERGY_DASHBOARD,
    CONF_DATA_BUCKET,
    CONF_IMPORT_DASHBOARDS,
    CONF_LANGUAGE,
    CONF_ORG,
    CONF_REAPPLY_DASHBOARDS,
    CONF_RUN_FRONTEND_INSTALLER,
    CONF_S1_LCD_BRIDGE_TOKEN,
    CONF_S1_LCD_BRIDGE_URL,
    CONF_S1_LCD_DISPLAY,
    DEFAULT_AGENTS_BUCKET,
    DEFAULT_CONFIGURE_ENERGY_DASHBOARD,
    DEFAULT_DATA_BUCKET,
    DEFAULT_IMPORT_DASHBOARDS,
    DEFAULT_NAME,
    DEFAULT_ORG,
    DEFAULT_REAPPLY_DASHBOARDS,
    DEFAULT_RUN_FRONTEND_INSTALLER,
    DEFAULT_S1_LCD_BRIDGE_TOKEN,
    DEFAULT_S1_LCD_BRIDGE_URL,
    DEFAULT_S1_LCD_DISPLAY,
    DEFAULT_URL,
    DOMAIN,
    INTERNAL_OPTION_KEYS,
    SUPPORTED_LANGUAGES,
    normalize_language,
)

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util.yaml import Secrets, load_yaml_dict


async def _async_validate_connection(
    hass: Any, url: str, token: str, org: str, bucket: str | None
) -> str | None:
    """Validate InfluxDB credentials. Returns an error key, or None on success.

    The client is always closed, including when construction itself fails.
    """

    api: SolarCubeApi | None = None
    try:
        api = SolarCubeApi(url=url, token=token, org=org)
        await api.async_validate(bucket=bucket)
    except SolarCubeApiAuthError:
        return "invalid_auth"
    except SolarCubeApiRequestError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"
    finally:
        if api is not None:
            await hass.async_add_executor_job(api.close)
    return None


class SolarCubeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solar Cube."""

    VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    async def _async_token_from_configuration_yaml(self) -> str:
        """Load influxdb_token from configuration.yaml (best-effort)."""

        config_dir = Path(self.hass.config.config_dir)
        config_path = config_dir / "configuration.yaml"

        def _read() -> str:
            if not config_path.is_file():
                return ""
            try:
                data = load_yaml_dict(str(config_path), Secrets(config_dir))
            except Exception:  # noqa: BLE001
                return ""

            if not isinstance(data, dict):
                return ""

            token = data.get("influxdb_token")
            return token.strip() if isinstance(token, str) else ""

        return await self.hass.async_add_executor_job(_read)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token = (user_input.get(CONF_TOKEN) or "").strip()
            if not token:
                token = await self._async_token_from_configuration_yaml()

            if not token:
                errors["base"] = "missing_token"
            elif error := await _async_validate_connection(
                self.hass,
                url=user_input[CONF_URL],
                token=token,
                org=user_input[CONF_ORG],
                bucket=user_input.get(CONF_DATA_BUCKET) or DEFAULT_DATA_BUCKET,
            ):
                errors["base"] = error

            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                entry_data = dict(user_input)
                entry_data[CONF_TOKEN] = token
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, DEFAULT_NAME),
                    data=entry_data,
                )

        default_language = normalize_language(
            getattr(self.hass.config, "language", None)
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
                vol.Optional(CONF_TOKEN, default=""): str,
                vol.Required(CONF_ORG, default=DEFAULT_ORG): str,
                vol.Optional(CONF_DATA_BUCKET, default=DEFAULT_DATA_BUCKET): str,
                vol.Optional(CONF_AGENTS_BUCKET, default=DEFAULT_AGENTS_BUCKET): str,
                vol.Optional(
                    CONF_IMPORT_DASHBOARDS, default=DEFAULT_IMPORT_DASHBOARDS
                ): bool,
                vol.Optional(CONF_LANGUAGE, default=default_language): vol.In(
                    SUPPORTED_LANGUAGES
                ),
                # Opt-in: this downloads third-party JavaScript from GitHub and
                # registers it as a Lovelace resource. See the README warning.
                vol.Optional(
                    CONF_RUN_FRONTEND_INSTALLER,
                    default=DEFAULT_RUN_FRONTEND_INSTALLER,
                ): bool,
                vol.Optional(
                    CONF_CONFIGURE_ENERGY_DASHBOARD,
                    default=DEFAULT_CONFIGURE_ENERGY_DASHBOARD,
                ): bool,
                vol.Optional(
                    CONF_S1_LCD_DISPLAY, default=DEFAULT_S1_LCD_DISPLAY
                ): bool,
                vol.Optional(
                    CONF_S1_LCD_BRIDGE_URL, default=DEFAULT_S1_LCD_BRIDGE_URL
                ): str,
                vol.Optional(
                    CONF_S1_LCD_BRIDGE_TOKEN, default=DEFAULT_S1_LCD_BRIDGE_TOKEN
                ): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle a re-authentication flow initiated by Home Assistant."""
        entry_id = self.context.get("entry_id")
        self._reauth_entry = (
            self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm and perform re-authentication."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="unknown")

        current = {**entry.data, **entry.options}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if error := await _async_validate_connection(
                self.hass,
                url=current[CONF_URL],
                token=token,
                org=current[CONF_ORG],
                bucket=current.get(CONF_DATA_BUCKET, DEFAULT_DATA_BUCKET),
            ):
                errors["base"] = error
            else:
                # entry.options overrides entry.data in async_setup_entry, so the
                # token is stored in options only -- keeping a stale copy in data
                # would duplicate the secret without ever being read.
                self.hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_TOKEN: token}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SolarCubeOptionsFlowHandler(config_entry)


class SolarCubeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Solar Cube."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        current = {**self._entry.data, **self._entry.options}

        if user_input is not None:
            # Treat an empty token as "keep existing" to avoid leaking it via defaults.
            token = (user_input.get(CONF_TOKEN) or "").strip()
            candidate_token = token or current.get(CONF_TOKEN, "")

            if error := await _async_validate_connection(
                self.hass,
                url=user_input[CONF_URL],
                token=candidate_token,
                org=user_input[CONF_ORG],
                bucket=user_input[CONF_DATA_BUCKET],
            ):
                errors["base"] = error

            if not errors:
                # Preserve internal bookkeeping options; async_create_entry replaces
                # the whole options mapping, so anything omitted here is lost.
                new_options: dict[str, Any] = {
                    key: value
                    for key, value in self._entry.options.items()
                    if key in INTERNAL_OPTION_KEYS
                }
                new_options.update(
                    {
                        CONF_URL: user_input[CONF_URL],
                        CONF_ORG: user_input[CONF_ORG],
                        CONF_DATA_BUCKET: user_input[CONF_DATA_BUCKET],
                        CONF_AGENTS_BUCKET: user_input[CONF_AGENTS_BUCKET],
                        CONF_IMPORT_DASHBOARDS: user_input[CONF_IMPORT_DASHBOARDS],
                        CONF_CONFIGURE_ENERGY_DASHBOARD: user_input[
                            CONF_CONFIGURE_ENERGY_DASHBOARD
                        ],
                        CONF_LANGUAGE: normalize_language(
                            user_input.get(CONF_LANGUAGE),
                            current.get(CONF_LANGUAGE),
                        ),
                        CONF_RUN_FRONTEND_INSTALLER: user_input.get(
                            CONF_RUN_FRONTEND_INSTALLER,
                            DEFAULT_RUN_FRONTEND_INSTALLER,
                        ),
                        # Always taken from this submission: a one-shot action,
                        # never carried over from the stored options.
                        CONF_REAPPLY_DASHBOARDS: user_input.get(
                            CONF_REAPPLY_DASHBOARDS, DEFAULT_REAPPLY_DASHBOARDS
                        ),
                        CONF_S1_LCD_DISPLAY: user_input.get(
                            CONF_S1_LCD_DISPLAY, DEFAULT_S1_LCD_DISPLAY
                        ),
                        CONF_S1_LCD_BRIDGE_URL: user_input.get(
                            CONF_S1_LCD_BRIDGE_URL, DEFAULT_S1_LCD_BRIDGE_URL
                        ),
                        CONF_S1_LCD_BRIDGE_TOKEN: user_input.get(
                            CONF_S1_LCD_BRIDGE_TOKEN, DEFAULT_S1_LCD_BRIDGE_TOKEN
                        ),
                    }
                )
                if token:
                    new_options[CONF_TOKEN] = token
                elif CONF_TOKEN in self._entry.options:
                    new_options[CONF_TOKEN] = self._entry.options[CONF_TOKEN]

                new_title = user_input.get(CONF_NAME) or self._entry.title
                if new_title != self._entry.title:
                    self.hass.config_entries.async_update_entry(
                        self._entry, title=new_title
                    )

                return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=self._entry.title): str,
                vol.Required(
                    CONF_URL, default=current.get(CONF_URL, DEFAULT_URL)
                ): str,
                vol.Optional(CONF_TOKEN, default=""): str,
                vol.Required(
                    CONF_ORG, default=current.get(CONF_ORG, DEFAULT_ORG)
                ): str,
                vol.Required(
                    CONF_DATA_BUCKET,
                    default=current.get(CONF_DATA_BUCKET, DEFAULT_DATA_BUCKET),
                ): str,
                vol.Required(
                    CONF_AGENTS_BUCKET,
                    default=current.get(CONF_AGENTS_BUCKET, DEFAULT_AGENTS_BUCKET),
                ): str,
                vol.Required(
                    CONF_IMPORT_DASHBOARDS,
                    default=current.get(
                        CONF_IMPORT_DASHBOARDS, DEFAULT_IMPORT_DASHBOARDS
                    ),
                ): bool,
                # One-shot, so it always offers itself unticked.
                vol.Optional(
                    CONF_REAPPLY_DASHBOARDS, default=DEFAULT_REAPPLY_DASHBOARDS
                ): bool,
                vol.Optional(
                    CONF_LANGUAGE,
                    default=normalize_language(
                        current.get(CONF_LANGUAGE),
                        getattr(self.hass.config, "language", None),
                    ),
                ): vol.In(SUPPORTED_LANGUAGES),
                vol.Optional(
                    CONF_RUN_FRONTEND_INSTALLER,
                    default=current.get(
                        CONF_RUN_FRONTEND_INSTALLER, DEFAULT_RUN_FRONTEND_INSTALLER
                    ),
                ): bool,
                vol.Required(
                    CONF_CONFIGURE_ENERGY_DASHBOARD,
                    default=current.get(
                        CONF_CONFIGURE_ENERGY_DASHBOARD,
                        DEFAULT_CONFIGURE_ENERGY_DASHBOARD,
                    ),
                ): bool,
                vol.Optional(
                    CONF_S1_LCD_DISPLAY,
                    default=current.get(CONF_S1_LCD_DISPLAY, DEFAULT_S1_LCD_DISPLAY),
                ): bool,
                vol.Optional(
                    CONF_S1_LCD_BRIDGE_URL,
                    default=current.get(
                        CONF_S1_LCD_BRIDGE_URL, DEFAULT_S1_LCD_BRIDGE_URL
                    ),
                ): str,
                vol.Optional(
                    CONF_S1_LCD_BRIDGE_TOKEN,
                    default=current.get(
                        CONF_S1_LCD_BRIDGE_TOKEN, DEFAULT_S1_LCD_BRIDGE_TOKEN
                    ),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
