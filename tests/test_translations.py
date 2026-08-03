"""Completeness checks for every user-facing string.

Missing or mismatched translations degrade silently at runtime -- Home Assistant
renders a raw key, the LCD falls back to English -- so they are asserted here
rather than left to be spotted on a screenshot.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.solar_cube import const
from custom_components.solar_cube.solar_lcd import (
    _MODE_ERROR_LABELS,
    _MODE_STATES,
    _STRINGS,
)

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "solar_cube"
TRANSLATIONS = COMPONENT / "translations"
DASHBOARDS = COMPONENT / "dashboards"

LANGUAGES = ("en", "pl")


def load(lang: str) -> dict:
    return json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))


def flatten(node, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out |= flatten(value, f"{prefix}.{key}" if prefix else key)
        return out
    return {prefix: node}


class TestTranslationFiles:
    def test_every_language_parses(self) -> None:
        for lang in LANGUAGES:
            assert load(lang)

    def test_all_languages_have_identical_keys(self) -> None:
        reference = set(flatten(load("en")))
        for lang in LANGUAGES[1:]:
            assert set(flatten(load(lang))) == reference, lang

    def test_no_value_is_left_untranslated(self) -> None:
        """A value identical to English usually means it was never translated."""
        english = flatten(load("en"))
        for lang in LANGUAGES[1:]:
            other = flatten(load(lang))
            identical = [k for k, v in english.items() if other[k] == v]
            assert not identical, f"{lang}: untranslated {identical}"

    def test_no_value_is_empty(self) -> None:
        for lang in LANGUAGES:
            blank = [k for k, v in flatten(load(lang)).items() if not str(v).strip()]
            assert not blank, f"{lang}: empty {blank}"

    def test_placeholders_match_across_languages(self) -> None:
        """`{integration}` and friends must survive translation."""
        english = flatten(load("en"))
        for lang in LANGUAGES[1:]:
            other = flatten(load(lang))
            for key, value in english.items():
                assert set(re.findall(r"\{(\w+)\}", str(value))) == set(
                    re.findall(r"\{(\w+)\}", str(other[key]))
                ), f"{lang}: placeholder mismatch in {key}"


class TestFlowCoverage:
    """Every string the flows can surface must have a label."""

    def _code(self) -> str:
        return "".join(
            (COMPONENT / f).read_text(encoding="utf-8")
            for f in ("config_flow.py", "__init__.py", "repairs.py")
        )

    def test_every_error_reason_is_translated(self) -> None:
        emitted = set(re.findall(r'errors\["base"\]\s*=\s*"(\w+)"', self._code()))
        emitted |= {"invalid_auth", "cannot_connect", "unknown", "missing_token"}
        for lang in LANGUAGES:
            data = load(lang)
            have = set(data["config"]["error"]) | set(data["options"]["error"])
            assert not emitted - have, f"{lang}: missing {sorted(emitted - have)}"

    def test_every_abort_reason_is_translated(self) -> None:
        aborts = set(re.findall(r'async_abort\(reason="(\w+)"\)', self._code()))
        aborts |= {"already_configured"}
        for lang in LANGUAGES:
            have = set(load(lang)["config"]["abort"])
            assert not aborts - have, f"{lang}: missing {sorted(aborts - have)}"

    def test_every_schema_field_has_a_label(self) -> None:
        """Checked per flow: the two schemas legitimately differ.

        reapply_dashboards is an upgrade action, so it appears in the options
        flow only -- there is nothing to re-apply on a first install.
        """
        code = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
        # Split the source at the options handler so each schema is read alone.
        split = code.index("class SolarCubeOptionsFlowHandler")
        sources = {
            ("config", "user"): code[:split],
            ("options", "init"): code[split:],
        }
        base = {"name", "url", "token"}  # from homeassistant.const

        for (section, step), source in sources.items():
            names = set(
                re.findall(r"vol\.(?:Optional|Required)\(\s*(CONF_\w+)", source)
            )
            expected = {
                getattr(const, n)
                for n in names
                if isinstance(getattr(const, n, None), str)
            } | base

            for lang in LANGUAGES:
                have = set(load(lang)[section]["step"][step]["data"])
                assert not expected - have, (
                    f"{lang} {section}.{step}: missing {sorted(expected - have)}"
                )
                assert not have - expected, (
                    f"{lang} {section}.{step}: orphaned {sorted(have - expected)}"
                )

    def test_repairs_fix_flow_is_translated(self) -> None:
        for lang in LANGUAGES:
            step = load(lang)["issues"]["restart_required"]["fix_flow"]["step"]["confirm"]
            assert step["title"] and step["description"]


class TestLcdStrings:
    """The LCD carries its own string table, independent of translations/."""

    def _source(self) -> str:
        return (COMPONENT / "solar_lcd.py").read_text(encoding="utf-8")

    def _table(self) -> str:
        src = self._source()
        return src[src.index("_STRINGS: dict") : src.index("_FA_SOLID_CODEPOINTS")]

    def test_every_label_has_every_language(self) -> None:
        for key, values in _STRINGS.items():
            for lang in LANGUAGES:
                assert values.get(lang), f"{key} missing {lang}"

    def test_every_controller_mode_has_every_language(self) -> None:
        for code, (labels, _colour) in _MODE_STATES.items():
            for lang in LANGUAGES:
                assert labels.get(lang), f"mode {code} missing {lang}"
        for lang in LANGUAGES:
            assert _MODE_ERROR_LABELS.get(lang)

    def test_no_label_is_defined_but_never_rendered(self) -> None:
        """Dead labels drift out of sync and mislead translators."""
        src = self._source()
        table = self._table()
        rest = src.replace(table, "")
        keys = re.findall(r'^\s*"(\w+)":\s*\{', table, re.M)
        unused = [k for k in keys if not re.search(rf"""['"]{k}['"]""", rest)]
        assert not unused, f"unused LCD labels: {unused}"

    def test_polish_spelling_of_known_regressions(self) -> None:
        """Typos that shipped once and must not come back."""
        src = self._source()
        for wrong, right in (
            ("Autokonsupcja", "Autokonsumpcja"),
            ("Rozladowywanie", "Rozładowywanie"),
        ):
            assert wrong not in src, f"{wrong!r} should be {right!r}"


class TestDashboardTranslations:
    """Each dashboard ships one file per language; they must stay in step."""

    BASES = ("panel", "history", "forecasts")

    def _labels(self, path: Path) -> list[str]:
        """Return label values with any surrounding YAML quoting removed.

        Stripping the quotes matters: a value like ``'Solar Cube '`` has no
        trailing whitespace in the raw line but renders with one.
        """
        raw = re.findall(
            r"^\s*(?:title|name|label):\s*(.+)$", path.read_text(encoding="utf-8"), re.M
        )
        out = []
        for value in raw:
            value = value.rstrip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out.append(value)
        return out

    @pytest.mark.parametrize("base", BASES)
    def test_both_languages_exist(self, base: str) -> None:
        for lang in LANGUAGES:
            assert (DASHBOARDS / f"{base}_solar_cube_{lang}.yaml").is_file()

    @pytest.mark.parametrize("base", BASES)
    def test_same_number_of_labels(self, base: str) -> None:
        counts = {
            lang: len(self._labels(DASHBOARDS / f"{base}_solar_cube_{lang}.yaml"))
            for lang in LANGUAGES
        }
        assert len(set(counts.values())) == 1, f"{base}: label counts differ {counts}"

    def test_polish_dashboards_have_no_leftover_english_markers(self) -> None:
        leftovers = {"Now", "Total", "Today", "Production", "Consumption"}
        for base in self.BASES:
            path = DASHBOARDS / f"{base}_solar_cube_pl.yaml"
            for label in self._labels(path):
                assert label.strip() not in leftovers, f"{path.name}: {label!r}"

    def test_polish_spelling_of_known_regressions(self) -> None:
        for base in self.BASES:
            text = (DASHBOARDS / f"{base}_solar_cube_pl.yaml").read_text(encoding="utf-8")
            for wrong in ("produckji", "producja", "Autokonsupcja", "konsupcja"):
                assert wrong not in text, f"{base}_pl: {wrong!r}"

    def test_polish_labels_use_sentence_case(self) -> None:
        """Polish does not Title-Case common nouns; the English source does, so
        capitals leak across during translation."""
        proper = {"Solar", "Cube", "PV", "L1", "L2", "L3", "SoC", "EMS", "HEMS",
                  "PRO", "S1"}
        offenders = []
        for base in self.BASES:
            path = DASHBOARDS / f"{base}_solar_cube_pl.yaml"
            for label in self._labels(path):
                for word in label.split()[1:]:
                    bare = word.strip("()%/,.-")
                    if (bare and bare[0].isupper() and bare not in proper
                            and not bare.isupper()):
                        offenders.append(f"{path.name}: {label!r} ({bare})")
        assert not offenders, offenders

    def test_polish_uses_one_term_per_concept(self) -> None:
        """The same quantity was labelled differently on different dashboards."""
        banned = {
            "Konsumpcja": "use 'Zużycie' for consumption",
            "zużycie prądu": "use 'zużycie energii'",
            "per kWh": "use 'za kWh'",
            "Taryfa zakupu": "use 'Cena zakupu'",
            "Dynamiczna cena sprzedaży": "use 'Cena sprzedaży'",
            "Wartość wysłanej": "use 'Wartość sprzedanej'",
        }
        for base in self.BASES:
            text = (DASHBOARDS / f"{base}_solar_cube_pl.yaml").read_text(encoding="utf-8")
            for phrase, hint in banned.items():
                assert phrase not in text, f"{base}_pl contains {phrase!r}: {hint}"

    def test_no_trailing_whitespace_in_labels(self) -> None:
        for base in self.BASES:
            for lang in LANGUAGES:
                path = DASHBOARDS / f"{base}_solar_cube_{lang}.yaml"
                for label in self._labels(path):
                    assert label == label.rstrip(), f"{path.name}: {label!r}"


def _labels_for(lang: str, field: str) -> list[str]:
    """Every label a given schema field has, across both flows."""
    data = json.loads(
        (TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8")
    )
    found = []
    for flow in ("config", "options"):
        for body in (data.get(flow, {}).get("step") or {}).values():
            if field in (body.get("data") or {}):
                found.append(body["data"][field])
    return found


class TestRequestedWording:
    """Copy the user asked for by name. Pinned so a later edit cannot quietly
    revert it."""

    @pytest.mark.parametrize("lang", ("en", "pl"))
    def test_the_lcd_option_names_the_pro_without_the_model_number(self, lang) -> None:
        for label in _labels_for(lang, "s1_lcd_display"):
            assert "Solar Cube PRO" in label
            assert "S1" not in label

    @pytest.mark.parametrize("lang", ("en", "pl"))
    def test_the_reapply_option_reads_as_a_sentence(self, lang) -> None:
        """It appeared in the UI as the raw key `reapply_dashboards`."""
        labels = _labels_for(lang, "reapply_dashboards")
        assert labels, f"{lang}: no label for reapply_dashboards"
        for label in labels:
            assert label != "reapply_dashboards"
            assert "_" not in label
            assert label[0].isupper()


class TestHassfestRules:
    """Rules hassfest enforces in CI. Checked here so a failure surfaces before
    the push rather than after it."""

    @pytest.mark.parametrize("lang", ("en", "pl"))
    def test_no_translation_string_contains_a_url(self, lang) -> None:
        """hassfest: "the string should not contain URLs, please use description
        placeholders instead". The bridge URL option carried its default in the
        label, which failed the check.
        """
        offenders: list[str] = []

        def walk(node, path: str = "") -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, str) and re.search(r"https?://", node):
                offenders.append(f"{path}: {node}")

        walk(json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8")))
        assert not offenders, offenders

    @pytest.mark.parametrize("lang", ("en", "pl"))
    def test_an_issue_is_either_fixable_or_descriptive(self, lang) -> None:
        """hassfest declares description and fix_flow mutually exclusive:

            vol.Exclusive("description", "fixable")
            vol.Exclusive("fix_flow", "fixable")

        restart_required carried both, so the repair could not be validated.
        """
        data = json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))
        both = [
            name
            for name, issue in (data.get("issues") or {}).items()
            if "description" in issue and "fix_flow" in issue
        ]
        assert not both, f"{lang}: issues with both description and fix_flow: {both}"

    @pytest.mark.parametrize("lang", ("en", "pl"))
    def test_no_translation_string_has_stray_whitespace(self, lang) -> None:
        """hassfest: "the string should not contain leading or trailing spaces"."""
        offenders: list[str] = []

        def walk(node, path: str = "") -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, str) and node != node.strip():
                offenders.append(f"{path}: {node!r}")

        walk(json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8")))
        assert not offenders, offenders
