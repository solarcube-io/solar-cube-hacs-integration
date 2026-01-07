# Solar Cube Home Assistant Integration

Custom HACS-friendly integration that connects Home Assistant to your Solar Cube HEMS by reading metrics and forecasts from InfluxDB 2.x.

## Features
- Config-flow based setup for InfluxDB URL, token, organization, and bucket names.
- Sensors for live power, voltages, accumulated energy, SoC, prices, controller metadata, and optimisation savings.
- Attribute-rich sensors that expose hourly energy forecasts and optimal charge/discharge actions pulled directly from InfluxDB.
- Bundled Lovelace dashboards (`dashboards/panel_solar_cube_pl.yaml` / `dashboards/panel_solar_cube_en.yaml`, `dashboards/history_solar_cube_pl.yaml` / `dashboards/history_solar_cube_en.yaml`, `dashboards/forecasts_solar_cube_pl.yaml` / `dashboards/forecasts_solar_cube_en.yaml`) that can be auto-imported during setup.
- Optional automatic installation of the dashboard card dependencies listed in `dashboards/dependencies.json` when HACS is available.

## Compatibility
- Requires Home Assistant Core 2025.12.3 or newer.

## Installation (HACS)

Install via HACS (recommended — now included in the official HACS store):

1. Open HACS in Home Assistant and go to *Integrations → Explore & Add repositories*.
2. Search for **Solar Cube HEMS** and click *Install*.
3. Restart Home Assistant if prompted.

If you previously added this repository as a custom repository in HACS, you can remove it from HACS → Integrations → Installed repositories.

4. Add Local Calendar (optional, required for bundled automations):

	- Go to *Settings → Devices & Services → Add Integration* and select **Local Calendar**.
	- Create a calendar with the name "solar_cube" (recommended). The included automation uses `calendar.solar_cube` to create events; without this calendar the automation will not be able to create calendar events.

5. The integration will create the sensors automatically. By default it also registers the bundled dashboards in the sidebar (using the shipped YAML files under `dashboards/`). If you prefer to manage dashboards manually, disable **Import dashboards** in the setup form and then import the YAML files yourself.
6. Dashboard custom cards: the integration can optionally run a local installer hook to register/repair the required Lovelace resources. Leave **Run local frontend installer hook (advanced)** enabled during setup (default), or manage the resources yourself.

## Branching
All development branches have been consolidated into `main`. If you previously tracked other branches, switch to `main` to ensure you have the latest dashboards, config flow options, and dependency handling.
