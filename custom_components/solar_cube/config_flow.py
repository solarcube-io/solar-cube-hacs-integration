"""Config flow for Solar Cube HEMS."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_URL, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util.yaml import Secrets, load_yaml_dict

from .api import SolarCubeApi, SolarCubeApiAuthError, SolarCubeApiRequestError

from .const import (
    CONF_AGENTS_BUCKET,
    CONF_CONFIGURE_ENERGY_DASHBOARD,
    CONF_DATA_BUCKET,
    CONF_IMPORT_DASHBOARDS,
    CONF_ORG,
    CONF_RUN_FRONTEND_INSTALLER,
    DEFAULT_AGENTS_BUCKET,
    DEFAULT_CONFIGURE_ENERGY_DASHBOARD,
    DEFAULT_DATA_BUCKET,
    DEFAULT_IMPORT_DASHBOARDS,
    DEFAULT_NAME,
    DEFAULT_ORG,
    DEFAULT_URL,
    DOMAIN,
)

@config_entries.HANDLERS.register(DOMAIN)
class SolarCubeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solar Cube."""

    VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    async def _async_token_from_configuration_yaml(self) -> str:
        """Load influxdb_token from configuration.yaml (best-effort)."""

        config_path = Path(self.hass.config.config_dir) / "configuration.yaml"
        if not config_path.exists():
            return ""

        def _read() -> str:
            try:
                data = load_yaml_dict(
                    str(config_path),
                    Secrets(Path(self.hass.config.config_dir)),
                )
            except Exception:  # noqa: BLE001
                return ""

            if not isinstance(data, dict):
                return ""

            token = data.get("influxdb_token")
            return token.strip() if isinstance(token, str) else ""

        return await self.hass.async_add_executor_job(_read)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token = (user_input.get(CONF_TOKEN) or "").strip()
            if not token:
                token = await self._async_token_from_configuration_yaml()

            if not token:
                errors["base"] = "missing_token"
            else:
                try:
                    api = SolarCubeApi(
                        url=user_input[CONF_URL],
                        token=token,
                        org=user_input[CONF_ORG],
                    )
                    await api.async_validate(bucket=user_input.get(CONF_DATA_BUCKET) or DEFAULT_DATA_BUCKET)
                except SolarCubeApiAuthError:
                    errors["base"] = "invalid_auth"
                except SolarCubeApiRequestError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    errors["base"] = "unknown"
                finally:
                    try:
                        api.close()
                    except Exception:  # noqa: BLE001
                        pass

            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                entry_data = dict(user_input)
                entry_data[CONF_TOKEN] = token
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, DEFAULT_NAME),
                    data=entry_data,
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
                vol.Optional(
                    CONF_RUN_FRONTEND_INSTALLER,
                    default=True,
                ): bool,
                vol.Optional(
                    CONF_CONFIGURE_ENERGY_DASHBOARD,
                    default=DEFAULT_CONFIGURE_ENERGY_DASHBOARD,
                ): bool,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, user_input: dict[str, Any]) -> FlowResult:
        """Handle a re-authentication flow initiated by Home Assistant."""
        entry_id = self.context.get("entry_id")
        self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm and perform re-authentication."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            try:
                api = SolarCubeApi(
                    url=entry.options.get(CONF_URL, entry.data[CONF_URL]),
                    token=user_input[CONF_TOKEN],
                    org=entry.options.get(CONF_ORG, entry.data[CONF_ORG]),
                )
                data_bucket = entry.options.get(CONF_DATA_BUCKET, entry.data.get(CONF_DATA_BUCKET, DEFAULT_DATA_BUCKET))
                await api.async_validate(bucket=data_bucket)
            except SolarCubeApiAuthError:
                errors["base"] = "invalid_auth"
            except SolarCubeApiRequestError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            finally:
                try:
                    api.close()
                except Exception:  # noqa: BLE001
                    pass

            if not errors:
                # Store token in options because entry.options override entry.data in async_setup_entry.
                new_options = {**entry.options, CONF_TOKEN: user_input[CONF_TOKEN]}
                new_data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
                self.hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema({vol.Required(CONF_TOKEN): str})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        return SolarCubeOptionsFlowHandler(config_entry)


class SolarCubeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Solar Cube."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        current = {**self._entry.data, **self._entry.options}

        if user_input is not None:
            # Treat empty token as "keep existing" to avoid leaking it via defaults.
            token = (user_input.get(CONF_TOKEN) or "").strip()
            candidate_token = token or current.get(CONF_TOKEN, "")

            try:
                api = SolarCubeApi(
                    url=user_input[CONF_URL],
                    token=candidate_token,
                    org=user_input[CONF_ORG],
                )
                await api.async_validate(bucket=user_input[CONF_DATA_BUCKET])
            except SolarCubeApiAuthError:
                errors["base"] = "invalid_auth"
            except SolarCubeApiRequestError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            finally:
                try:
                    api.close()
                except Exception:  # noqa: BLE001
                    pass

            if not errors:
                # Persist most fields as options (override entry.data).
                new_options = {
                    **self._entry.options,
                    CONF_URL: user_input[CONF_URL],
                    CONF_ORG: user_input[CONF_ORG],
                    CONF_DATA_BUCKET: user_input[CONF_DATA_BUCKET],
                    CONF_AGENTS_BUCKET: user_input[CONF_AGENTS_BUCKET],
                    CONF_IMPORT_DASHBOARDS: user_input[CONF_IMPORT_DASHBOARDS],
                    CONF_CONFIGURE_ENERGY_DASHBOARD: user_input.get(
                        CONF_CONFIGURE_ENERGY_DASHBOARD,
                        current.get(CONF_CONFIGURE_ENERGY_DASHBOARD, DEFAULT_CONFIGURE_ENERGY_DASHBOARD),
                    ),
                }
                # Store installer hook flag in options so it can be toggled later.
                new_options[CONF_RUN_FRONTEND_INSTALLER] = user_input.get(CONF_RUN_FRONTEND_INSTALLER, True)
                if token:
                    new_options[CONF_TOKEN] = token

                new_title = user_input.get(CONF_NAME) or self._entry.title
                # Update the entry title; options are returned via async_create_entry.
                if new_title != self._entry.title:
                    self.hass.config_entries.async_update_entry(self._entry, title=new_title)

                return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=self._entry.title): str,
                vol.Required(CONF_URL, default=current.get(CONF_URL, DEFAULT_URL)): str,
                vol.Optional(CONF_TOKEN, default=""): str,
                vol.Required(CONF_ORG, default=current.get(CONF_ORG, DEFAULT_ORG)): str,
                vol.Required(CONF_DATA_BUCKET, default=current.get(CONF_DATA_BUCKET, DEFAULT_DATA_BUCKET)): str,
                vol.Required(CONF_AGENTS_BUCKET, default=current.get(CONF_AGENTS_BUCKET, DEFAULT_AGENTS_BUCKET)): str,
                vol.Required(CONF_IMPORT_DASHBOARDS, default=current.get(CONF_IMPORT_DASHBOARDS, DEFAULT_IMPORT_DASHBOARDS)): bool,
                vol.Optional(
                    CONF_RUN_FRONTEND_INSTALLER,
                    default=current.get(CONF_RUN_FRONTEND_INSTALLER, True),
                ): bool,
                vol.Required(
                    CONF_CONFIGURE_ENERGY_DASHBOARD,
                    default=current.get(CONF_CONFIGURE_ENERGY_DASHBOARD, DEFAULT_CONFIGURE_ENERGY_DASHBOARD),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
