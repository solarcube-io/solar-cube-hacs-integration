#!/usr/bin/env python3
"""Render Solar Cube PRO S1 LCD previews without Home Assistant.

Draws the real renderer against canned sensor data so the layout can be
reviewed, and every localisation checked, without a panel or a running HA.

    python3 tools/preview_lcd.py                      # every scenario, en + pl
    python3 tools/preview_lcd.py --lang pl            # one language
    python3 tools/preview_lcd.py --scenario fault     # one scenario
    python3 tools/preview_lcd.py --scale 6 --out /tmp # bigger, elsewhere
    python3 tools/preview_lcd.py --currency EUR       # another currency

Writes <scenario>_<lang>.png plus a contact sheet per language.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = ROOT / "custom_components" / "solar_cube"
DEFAULT_OUT = ROOT / "docs" / "lcd_previews"

# Raw coordinator values: powers in W, voltages in mV, SoC in %, prices and
# savings in currency units. sensor_definitions applies the divisions, so the
# voltages are deliberately given as millivolts.
SCENARIOS: dict[str, tuple[str, dict]] = {
    "peak-solar": (
        "Midday peak: PV covers the house, charges the battery and exports",
        {
            "pv_active_power": 7400,
            "consumption_active_power": 1900,
            "ess_active_power": 3200,
            "grid_active_power": -2300,
            "ess_soc": 61,
            "grid_voltage_l1": 241000,
            "grid_voltage_l2": 239500,
            "grid_voltage_l3": 240200,
            "buy_energy_price": 0.42,
            "sell_energy_price": 0.31,
            "optimised_energy_total_savings": 128.55,
            "controller_id": "2",
        },
    ),
    "self-consumption": (
        "Balanced: PV feeds the house directly, little grid exchange",
        {
            "pv_active_power": 2450,
            "consumption_active_power": 2300,
            "ess_active_power": 0,
            "grid_active_power": 150,
            "ess_soc": 88,
            "grid_voltage_l1": 233000,
            "grid_voltage_l2": 232000,
            "grid_voltage_l3": 234000,
            "buy_energy_price": 0.55,
            "sell_energy_price": 0.28,
            "optimised_energy_total_savings": 96.10,
            "controller_id": "7",
        },
    ),
    "evening-discharge": (
        "Evening: no PV, battery covers the house",
        {
            "pv_active_power": 0,
            "consumption_active_power": 3100,
            "ess_active_power": -2900,
            "grid_active_power": 200,
            "ess_soc": 47,
            "grid_voltage_l1": 236000,
            "grid_voltage_l2": 235000,
            "grid_voltage_l3": 237000,
            "buy_energy_price": 0.87,
            "sell_energy_price": 0.34,
            "optimised_energy_total_savings": 143.20,
            "controller_id": "4",
        },
    ),
    "grid-charging": (
        "Cheap tariff overnight: charging the battery from the grid",
        {
            "pv_active_power": 0,
            "consumption_active_power": 700,
            "ess_active_power": 4100,
            "grid_active_power": 4800,
            "ess_soc": 34,
            "grid_voltage_l1": 229000,
            "grid_voltage_l2": 228500,
            "grid_voltage_l3": 230000,
            "buy_energy_price": 0.19,
            "sell_energy_price": 0.11,
            "optimised_energy_total_savings": 151.75,
            "controller_id": "10",
        },
    ),
    "export-to-grid": (
        "Price spike: discharging the battery into the grid",
        {
            "pv_active_power": 1200,
            "consumption_active_power": 900,
            "ess_active_power": -5200,
            "grid_active_power": -5500,
            "ess_soc": 72,
            "grid_voltage_l1": 244000,
            "grid_voltage_l2": 243000,
            "grid_voltage_l3": 245500,
            "buy_energy_price": 1.34,
            "sell_energy_price": 1.12,
            "optimised_energy_total_savings": 209.90,
            "controller_id": "20",
        },
    ),
    "standby": (
        "Night, idle: battery held at its reserve",
        {
            "pv_active_power": 0,
            "consumption_active_power": 320,
            "ess_active_power": 0,
            "grid_active_power": 330,
            "ess_soc": 20,
            "grid_voltage_l1": 231000,
            "grid_voltage_l2": 230500,
            "grid_voltage_l3": 231500,
            "buy_energy_price": 0.24,
            "sell_energy_price": 0.12,
            "optimised_energy_total_savings": 88.00,
            "controller_id": "0",
        },
    ),
    "fault": (
        "Unknown controller state: falls back to the error label",
        {
            "pv_active_power": 0,
            "consumption_active_power": 1500,
            "ess_active_power": 0,
            "grid_active_power": 1500,
            "ess_soc": 55,
            "grid_voltage_l1": 252000,
            "grid_voltage_l2": 251000,
            "grid_voltage_l3": 253000,
            "buy_energy_price": 0.61,
            "sell_energy_price": 0.29,
            "optimised_energy_total_savings": 74.30,
            "controller_id": "99",
        },
    ),
    "no-data": (
        "Startup or InfluxDB down: every value unavailable",
        {},
    ),
    "long-values": (
        "Layout stress: widest plausible numbers on every tile",
        {
            "pv_active_power": 19850,
            "consumption_active_power": 18400,
            "ess_active_power": -12750,
            "grid_active_power": -14300,
            "ess_soc": 100,
            "grid_voltage_l1": 253900,
            "grid_voltage_l2": 253900,
            "grid_voltage_l3": 253900,
            "buy_energy_price": 12.99,
            "sell_energy_price": 11.88,
            "optimised_energy_total_savings": 98765.43,
            "controller_id": "4",
        },
    ),
}

LANGUAGES = ("en", "pl")


def _install_ha_stubs() -> None:
    """Provide enough Home Assistant surface to import the LCD renderer."""
    for name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.issue_registry",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))

    sys.modules["homeassistant.config_entries"].ConfigEntry = object
    sys.modules["homeassistant.core"].HomeAssistant = object
    registry = sys.modules["homeassistant.helpers.issue_registry"]
    registry.async_create_issue = lambda *a, **k: None
    registry.async_delete_issue = lambda *a, **k: None
    registry.IssueSeverity = types.SimpleNamespace(WARNING="warning")
    sys.modules["homeassistant.helpers"].issue_registry = registry

    package = types.ModuleType("custom_components.solar_cube")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    sys.modules.setdefault("custom_components.solar_cube", package)


def _load_renderer():
    _install_ha_stubs()
    name = "custom_components.solar_cube.solar_lcd"
    spec = importlib.util.spec_from_file_location(name, COMPONENT_DIR / "solar_lcd.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load solar_lcd.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _upscaled(image, scale: int):
    """Enlarge with NEAREST.

    This is a 170x320 pixel panel; nearest-neighbour shows what it actually
    displays. Interpolating would invent gradients that are neither on the panel
    nor compressible -- LANCZOS made these files roughly ten times larger.
    """
    from PIL import Image

    return image.resize(
        (image.width * scale, image.height * scale), Image.Resampling.NEAREST
    )


def _contact_sheet(renderer, images: list[tuple[str, object]], scale: int):
    """Lay the scenarios out side by side with captions."""
    from PIL import Image, ImageDraw

    thumb_w, thumb_h = renderer.W * scale, renderer.H * scale
    pad, caption_h = 14, 22
    columns = min(len(images), 5)
    rows = (len(images) + columns - 1) // columns

    sheet = Image.new(
        "RGB",
        (
            columns * (thumb_w + pad) + pad,
            rows * (thumb_h + caption_h + pad) + pad,
        ),
        (18, 18, 20),
    )
    draw = ImageDraw.Draw(sheet)
    font = renderer._load_fonts(str(ROOT))["small"]

    for index, (label, image) in enumerate(images):
        col, row = index % columns, index // columns
        x = pad + col * (thumb_w + pad)
        y = pad + row * (thumb_h + caption_h + pad)
        sheet.paste(_upscaled(image, scale), (x, y))
        draw.text((x + 2, y + thumb_h + 5), label, font=font, fill=(200, 205, 210))
    return sheet


def _display(path: Path) -> str:
    """Path relative to the repo when it is inside it, absolute otherwise."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--lang", default=",".join(LANGUAGES))
    parser.add_argument("--scenario", default="", help="one scenario, or all")
    parser.add_argument(
        "--currency",
        default="PLN",
        help="ISO 4217 code the panel should display (default: PLN)",
    )
    parser.add_argument("--no-sheet", action="store_true")
    args = parser.parse_args()

    if importlib.util.find_spec("PIL") is None:
        print("ERROR: Pillow not installed. Run: pip install Pillow", file=sys.stderr)
        return 1

    languages = [x.strip() for x in args.lang.split(",") if x.strip()]
    if args.scenario:
        if args.scenario not in SCENARIOS:
            print(
                f"Unknown scenario {args.scenario!r}. Available: "
                f"{', '.join(SCENARIOS)}",
                file=sys.stderr,
            )
            return 2
        wanted = {args.scenario: SCENARIOS[args.scenario]}
    else:
        wanted = SCENARIOS

    renderer = _load_renderer()
    args.out.mkdir(parents=True, exist_ok=True)

    total = 0
    for lang in languages:
        rendered: list[tuple[str, object]] = []
        for name, (description, data) in wanted.items():
            image = renderer._render_image_pil(
                data, lang, str(ROOT), currency=args.currency
            )
            path = args.out / f"{name}_{lang}.png"
            _upscaled(image, args.scale).save(path, optimize=True)
            size_kb = path.stat().st_size / 1024
            total += path.stat().st_size
            print(f"  {_display(path)}  ({size_kb:.0f} KB)  {description}")
            rendered.append((name, image))

        if not args.no_sheet and len(rendered) > 1:
            sheet_path = args.out / f"_contact-sheet_{lang}.png"
            _contact_sheet(renderer, rendered, max(2, args.scale - 1)).save(
                sheet_path, optimize=True
            )
            total += sheet_path.stat().st_size
            print(
                f"  {_display(sheet_path)}  "
                f"({sheet_path.stat().st_size / 1024:.0f} KB)  contact sheet"
            )

    print(
        f"\n{len(wanted)} scenario(s) x {len(languages)} language(s) at "
        f"{renderer.W * args.scale}x{renderer.H * args.scale} px "
        f"(native {renderer.W}x{renderer.H}), {total / 1024:.0f} KB total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
