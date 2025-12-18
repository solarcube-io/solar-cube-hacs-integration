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
1. Add this repository as a custom repository in HACS and install the **Solar Cube HEMS** integration.
2. Restart Home Assistant if prompted.
3. In **Settings → Devices & Services**, add the **Solar Cube** integration and provide:
   - InfluxDB URL (default: `http://influxdb2:8086`)
   - Token (optional if `influxdb_token` is set in `configuration.yaml`)
   - Organization (default: `solarcube`)
   - Buckets (defaults: `db` for live data, `agents` for forecasts/actions)
4. The integration will create the sensors automatically. By default it also registers the bundled dashboards in the sidebar (using the shipped YAML files under `dashboards/`). If you prefer to manage dashboards manually, disable **Import dashboards** in the setup form and then import the YAML files yourself.
5. Dashboard custom cards: the integration can optionally run a local installer hook to register/repair the required Lovelace resources. Leave **Run local frontend installer hook (advanced)** enabled during setup (default), or manage the resources yourself.

## Branching
All development branches have been consolidated into `main`. If you previously tracked other branches, switch to `main` to ensure you have the latest dashboards, config flow options, and dependency handling.
