from datetime import timedelta
from pathlib import Path

DOMAIN = "solar_cube"
DEFAULT_NAME = "Solar Cube"
DEFAULT_URL = "http://influxdb2:8086"
DEFAULT_ORG = "solarcube"
DEFAULT_DATA_BUCKET = "db"
DEFAULT_AGENTS_BUCKET = "agents"
DEFAULT_IMPORT_DASHBOARDS = True
DEFAULT_CONFIGURE_ENERGY_DASHBOARD = True
CONF_DATA_BUCKET = "data_bucket"
CONF_AGENTS_BUCKET = "agents_bucket"
CONF_ORG = "org"
CONF_IMPORT_DASHBOARDS = "import_dashboards"
CONF_RUN_FRONTEND_INSTALLER = "run_frontend_installer"
CONF_CONFIGURE_ENERGY_DASHBOARD = "configure_energy_dashboard"
CONF_LANGUAGE = "language"


def get_dashboard_files(lang: str = "en") -> dict[str, str]:
    """Return dashboard filename mapping for the given language."""
    suffix = "pl" if lang == "pl" else "en"
    return {
        "solar-cube-panel": f"panel_solar_cube_{suffix}.yaml",
        "solar-cube-history": f"history_solar_cube_{suffix}.yaml",
        "solar-cube-forecasts": f"forecasts_solar_cube_{suffix}.yaml",
    }

# Dashboards and dependencies are bundled with the integration so they are available
# even when installed via HACS (which typically installs only custom_components/*).
DASHBOARD_DEPENDENCIES_PATH = (
    Path(__file__).parent / "dashboards" / "dependencies.json"
)

UPDATE_INTERVAL = timedelta(seconds=30)
FORECAST_UPDATE_INTERVAL = timedelta(minutes=30)
OPTIMAL_ACTIONS_UPDATE_INTERVAL = timedelta(minutes=30)
