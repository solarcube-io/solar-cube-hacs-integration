#!/usr/bin/env python3
"""Validate the JSON and YAML assets shipped inside the integration.

Broken translations or dashboards fail silently at runtime (a missing label, a
dashboard that never appears), so they are checked in CI instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "solar_cube"
TRANSLATIONS = COMPONENT / "translations"
DASHBOARDS = COMPONENT / "dashboards"

errors: list[str] = []


def check_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        errors.append(f"{path.relative_to(ROOT)}: {err}")
        return None


def check_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as err:
        errors.append(f"{path.relative_to(ROOT)}: {err}")
        return None


def leaf_keys(node: object, prefix: str = "") -> set[str]:
    if isinstance(node, dict):
        keys: set[str] = set()
        for key, value in node.items():
            keys |= leaf_keys(value, f"{prefix}.{key}" if prefix else key)
        return keys
    return {prefix}


def main() -> int:
    manifest = check_json(COMPONENT / "manifest.json")

    # Every translation file must parse and expose the same set of keys, so a
    # language never silently falls back to raw placeholders.
    translations: dict[str, set[str]] = {}
    for path in sorted(TRANSLATIONS.glob("*.json")):
        data = check_json(path)
        if data is not None:
            translations[path.stem] = leaf_keys(data)

    if "en" in translations:
        for lang, keys in translations.items():
            if lang == "en":
                continue
            missing = translations["en"] - keys
            extra = keys - translations["en"]
            if missing:
                errors.append(f"translations/{lang}.json missing keys: {sorted(missing)}")
            if extra:
                errors.append(f"translations/{lang}.json unexpected keys: {sorted(extra)}")

    for path in sorted(DASHBOARDS.glob("*.yaml")):
        check_yaml(path)
    for path in sorted(DASHBOARDS.glob("*.json")):
        check_json(path)

    # Every dashboard referenced by DASHBOARD_SPECS must actually be shipped.
    sys.path.insert(0, str(ROOT))
    from custom_components.solar_cube.const import DASHBOARD_SPECS

    for lang, specs in DASHBOARD_SPECS.items():
        for spec in specs:
            if not (DASHBOARDS / spec["filename"]).is_file():
                errors.append(
                    f"DASHBOARD_SPECS[{lang!r}] references missing "
                    f"dashboards/{spec['filename']}"
                )

    if isinstance(manifest, dict):
        version = manifest.get("version", "")
        if not version:
            errors.append("manifest.json: version must be set for HACS")

    if errors:
        print("Asset validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("All shipped JSON/YAML assets are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
