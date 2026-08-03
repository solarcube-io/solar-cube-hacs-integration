"""Constants for the Solar Cube HEMS integration."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Final

DOMAIN: Final = "solar_cube"

DEFAULT_NAME: Final = "Solar Cube"
DEFAULT_URL: Final = "http://influxdb2:8086"
DEFAULT_ORG: Final = "solarcube"
DEFAULT_DATA_BUCKET: Final = "db"
DEFAULT_AGENTS_BUCKET: Final = "agents"
DEFAULT_IMPORT_DASHBOARDS: Final = True
DEFAULT_CONFIGURE_ENERGY_DASHBOARD: Final = True
# On by default: Solar Cube is shipped as an appliance whose users have no
# filesystem access and no way to install the dashboard cards themselves, so the
# dashboards would be unusable without this. Every download is version-pinned in
# tools/install_frontend_deps.sh and listed in the README.
DEFAULT_RUN_FRONTEND_INSTALLER: Final = True

CONF_DATA_BUCKET: Final = "data_bucket"
CONF_AGENTS_BUCKET: Final = "agents_bucket"
CONF_ORG: Final = "org"
CONF_IMPORT_DASHBOARDS: Final = "import_dashboards"
CONF_RUN_FRONTEND_INSTALLER: Final = "run_frontend_installer"
CONF_CONFIGURE_ENERGY_DASHBOARD: Final = "configure_energy_dashboard"
CONF_LANGUAGE: Final = "language"

# Solar Cube is sold into a PLN market, so monetary sensors fall back to PLN
# when Home Assistant has no currency configured. A configured currency always
# wins. Must stay a valid ISO 4217 code: Home Assistant rejects anything else
# for device_class "monetary", which is why the LCD's "zl" symbol is
# display-only and never used as a unit.
DEFAULT_CURRENCY: Final = "PLN"

SUPPORTED_LANGUAGES: Final = {"pl": "Polski", "en": "English"}
DEFAULT_LANGUAGE: Final = "en"

# Keys used inside hass.data[DOMAIN]. Per-entry state lives under DATA_ENTRIES so
# that module-level bookkeeping flags can never be mistaken for a config entry.
DATA_ENTRIES: Final = "entries"
DATA_AUTOMATIONS_IMPORTED: Final = "automations_imported"
DATA_STORAGE_DASHBOARDS_IMPORTED: Final = "storage_dashboards_imported"
DATA_LOVELACE_RETRY_SCHEDULED: Final = "lovelace_retry_scheduled"
DATA_RESTART_NOTIFICATION_SHOWN: Final = "restart_notification_shown"

# Persisted in entry.options so one-shot imports never repeat across restarts.
OPT_STORAGE_DASHBOARDS_IMPORTED: Final = "_storage_dashboards_imported"
OPT_ORPHAN_CLEANUP_DONE: Final = "_orphan_cleanup_done"

# Fingerprint of the dashboard config this integration last wrote, per url_path.
# Lets an upgrade tell three cases apart: nothing changed, the shipped copy
# changed while the user's copy is untouched (safe to refresh), and the user has
# edited theirs (must not be overwritten without being asked).
OPT_SEEDED_DASHBOARDS: Final = "_seeded_dashboards"

# One-shot: overwrite the shipped dashboards, the Energy dashboard and the
# shipped automations, discarding local edits to them.
CONF_REAPPLY_DASHBOARDS: Final = "reapply_dashboards"
DEFAULT_REAPPLY_DASHBOARDS: Final = False

# Option keys that are internal bookkeeping and must not be surfaced in, or
# dropped by, the options flow.
INTERNAL_OPTION_KEYS: Final = frozenset(
    {
        OPT_STORAGE_DASHBOARDS_IMPORTED,
        OPT_ORPHAN_CLEANUP_DONE,
        OPT_SEEDED_DASHBOARDS,
    }
)

ISSUE_RESTART_REQUIRED: Final = "restart_required"
# Raised when the LCD bridge is configured but unusable. Appliance users do
# not read logs, so this surfaces in Settings -> Repairs.
ISSUE_LCD_BRIDGE: Final = "lcd_bridge_problem"
# Raised when a shipped dashboard has been updated but the user's copy was
# edited, so it was left alone rather than overwritten.
ISSUE_DASHBOARDS_OUTDATED: Final = "dashboards_outdated"

# Dashboards and dependencies are bundled with the integration so they are available
# even when installed via HACS (which installs only custom_components/<domain>).
PACKAGED_DASHBOARDS_DIR: Final = Path(__file__).parent / "dashboards"
DASHBOARD_DEPENDENCIES_PATH: Final = PACKAGED_DASHBOARDS_DIR / "dependencies.json"

# Lovelace Storage dashboards created on first setup, per language.
# url_path values must be slug-like and contain a hyphen (Home Assistant validation).
DASHBOARD_SPECS: Final[dict[str, tuple[dict[str, str], ...]]] = {
    "en": (
        {
            "url_path": "panel-solar-cube",
            "title": "Solar Cube",
            "icon": "mdi:solar-panel",
            "filename": "panel_solar_cube_en.yaml",
        },
        {
            "url_path": "historia-solar-cube",
            "title": "Solar Cube History",
            "icon": "mdi:history",
            "filename": "history_solar_cube_en.yaml",
        },
        {
            "url_path": "prognozy-solar-cube",
            "title": "Solar Cube Forecasts",
            "icon": "mdi:weather-sunny-alert",
            "filename": "forecasts_solar_cube_en.yaml",
        },
    ),
    "pl": (
        {
            "url_path": "panel-solar-cube",
            "title": "Solar Cube",
            "icon": "mdi:solar-panel",
            "filename": "panel_solar_cube_pl.yaml",
        },
        {
            "url_path": "historia-solar-cube",
            "title": "Solar Cube Historia",
            "icon": "mdi:history",
            "filename": "history_solar_cube_pl.yaml",
        },
        {
            "url_path": "prognozy-solar-cube",
            "title": "Solar Cube Prognozy",
            "icon": "mdi:weather-sunny-alert",
            "filename": "forecasts_solar_cube_pl.yaml",
        },
    ),
}

UPDATE_INTERVAL: Final = timedelta(seconds=30)
FORECAST_UPDATE_INTERVAL: Final = timedelta(minutes=30)
OPTIMAL_ACTIONS_UPDATE_INTERVAL: Final = timedelta(minutes=30)

# Solar Cube PRO S1 LCD Display option.
# NOTE: the option keys below still read 's1_lcd_*'. They are persisted in
# config entries, so renaming them would orphan every existing install.
CONF_S1_LCD_DISPLAY: Final = "s1_lcd_display"
DEFAULT_S1_LCD_DISPLAY: Final = False

CONF_S1_LCD_BRIDGE_URL: Final = "s1_lcd_bridge_url"
# Default uses the docker service name from the bridge's compose fragment
# (works in docker-compose / DinD setups). For a host-side bridge, use:
# http://host.docker.internal:8765
DEFAULT_S1_LCD_BRIDGE_URL: Final = "http://solar_lcd_bridge:8765"

# Shared secret sent as the X-Bridge-Token header.
#
# CONTRACT: this value must equal DEFAULT_TOKEN in the Solar LCD Bridge
# repository (https://dev.azure.com/roygard/Solar%20Cube%20%28Technology%29/_git/Solar_LCD_Bridge),
# which bakes the same default into its Docker image so the display works out
# of the box on appliances whose users cannot edit container environments.
# See that repo's CONTRACT.md. Being published, it is NOT a secret: it only
# keeps unrelated services and casual probes off the endpoint. Operators who
# can set an environment variable should override it on both sides.
DEFAULT_S1_LCD_BRIDGE_TOKEN: Final = "solar-cube-lcd-default"
CONF_S1_LCD_BRIDGE_TOKEN: Final = "s1_lcd_bridge_token"


def normalize_language(value: str | None, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Return a supported two-letter language code.

    Accepts full locale tags such as ``pl-PL`` and falls back to ``fallback``
    (then to English) when the language is unknown or missing.
    """

    code = (value or "").split("-")[0].strip().lower()
    if code in SUPPORTED_LANGUAGES:
        return code
    fallback_code = (fallback or "").split("-")[0].strip().lower()
    if fallback_code in SUPPORTED_LANGUAGES:
        return fallback_code
    return DEFAULT_LANGUAGE
