"""The LCD and the history dashboard must colour controller states alike.

Both render the same `controller_id`, so a user glancing at the panel and then
at the timeline should see the same colour for the same state. The two palettes
are maintained in different files and had already drifted once: mode 20 was
changed on the panel and left behind in the dashboard.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.solar_cube.solar_lcd import _MODE_ERROR_COLOR, _MODE_STATES

DASHBOARDS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "solar_cube"
    / "dashboards"
)
HISTORY = sorted(DASHBOARDS.glob("history_solar_cube_*.yaml"))

# The dashboard's timeline keys, and the controller_id each stands for. Taken
# from the `process:` expression in the Controller state card.
KEY_TO_STATE = {
    "MNT": "0",
    "CH1": "2",
    "CH2": "10",
    "CH3": "19",
    "DCH1": "4",
    "DCH2": "20",
    "DCH3": "21",
    "A-C": "7",
}

# Controller states the dashboard maps but the LCD does not know. The panel
# currently shows "System Error" for these. Tracked here so the gap is visible
# rather than forgotten; remove an entry once the LCD gains the mode.
LCD_UNKNOWN_STATES = {"19", "21"}

NAMED_COLOURS = {"cyan": "#00FFFF"}


def hex_of(colour: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*colour)


def dashboard_colours(path: Path) -> dict[str, str]:
    """State key -> upper-case hex, from the dashboard's stateColors block."""
    text = path.read_text("utf-8")
    block = re.search(r"^        stateColors:\n((?:^ {10}.*\n)+)", text, re.M)
    assert block, f"{path.name}: no stateColors block"

    found: dict[str, str] = {}
    for key, value in re.findall(
        r"^ {10}'?([A-Za-z0-9-]+)'?:\s*'?([#\w]+)'?\s*$", block.group(1), re.M
    ):
        if key in KEY_TO_STATE or key == "Unknown":
            found[key] = NAMED_COLOURS.get(value.lower(), value).upper()
    return found


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_shared_states_use_the_same_colour(path) -> None:
    colours = dashboard_colours(path)
    mismatches = []
    for key, state in KEY_TO_STATE.items():
        if state in LCD_UNKNOWN_STATES:
            continue
        expected = hex_of(_MODE_STATES[state][1])
        actual = colours.get(key)
        if actual != expected:
            mismatches.append(f"{key} (state {state}): dashboard {actual} != LCD {expected}")
    assert not mismatches, f"{path.name}: {mismatches}"


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_every_lcd_mode_appears_in_the_dashboard(path) -> None:
    """A mode on the panel with no timeline colour would render as a default."""
    colours = dashboard_colours(path)
    mapped = {state for key, state in KEY_TO_STATE.items() if key in colours}
    missing = sorted(set(_MODE_STATES) - mapped)
    assert not missing, f"{path.name}: LCD modes absent from the timeline: {missing}"


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_discharge_to_grid_matches_the_export_colour(path) -> None:
    """Regression: the panel moved to the export purple, the dashboard did not."""
    from custom_components.solar_cube.solar_lcd import GRID_EXPORT_C

    assert dashboard_colours(path)["DCH2"] == hex_of(GRID_EXPORT_C)


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_both_languages_share_one_palette(path) -> None:
    """Colour is not a translatable property."""
    reference = dashboard_colours(HISTORY[0])
    assert dashboard_colours(path) == reference


def test_the_unknown_state_gap_is_recorded() -> None:
    """Keeps LCD_UNKNOWN_STATES honest: drop entries as the LCD gains modes."""
    for state in LCD_UNKNOWN_STATES:
        assert state not in _MODE_STATES, (
            f"the LCD now knows state {state}; remove it from LCD_UNKNOWN_STATES "
            "and give it a colour matching the dashboard"
        )
    assert set(KEY_TO_STATE.values()) >= LCD_UNKNOWN_STATES


def test_error_colour_is_documented_as_differing() -> None:
    """The panel shows red for an unknown state; the timeline shows black.

    Recorded rather than asserted equal: black is deliberate on the timeline's
    light background but would be invisible on the panel's dark one.
    """
    assert hex_of(_MODE_ERROR_COLOR) == "#FF0000"
    assert dashboard_colours(HISTORY[0])["Unknown"] == "#000000"


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_every_controller_state_has_a_distinct_colour(path) -> None:
    """Two states sharing a colour are indistinguishable on the timeline.

    MNT and DCH3 were both #808000 until MNT moved to grey.
    """
    colours = dashboard_colours(path)
    by_colour: dict[str, list[str]] = {}
    for key in KEY_TO_STATE:
        if key in colours:
            by_colour.setdefault(colours[key], []).append(key)

    clashes = {c: keys for c, keys in by_colour.items() if len(keys) > 1}
    assert not clashes, f"{path.name}: states sharing a colour: {clashes}"


def test_lcd_modes_have_distinct_colours() -> None:
    by_colour: dict[tuple[int, int, int], list[str]] = {}
    for code, (_labels, colour) in _MODE_STATES.items():
        by_colour.setdefault(colour, []).append(code)

    clashes = {hex_of(c): codes for c, codes in by_colour.items() if len(codes) > 1}
    assert not clashes, f"LCD modes sharing a colour: {clashes}"


def test_flow_modes_reuse_their_tile_colour() -> None:
    """Grid charge/discharge should read as the same flow as the grid tile."""
    from custom_components.solar_cube.solar_lcd import GRID_EXPORT_C, GRID_IMPORT_C

    assert _MODE_STATES["10"][1] == GRID_IMPORT_C
    assert _MODE_STATES["20"][1] == GRID_EXPORT_C


# ── Target SoC ramp ─────────────────────────────────────────────────────────────
# The timeline buckets target_soc into these keys, low to high, per the
# `process:` expression in the Target battery level card.
SOC_BUCKETS = [
    "0", "1-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31-35", "36-40",
    "41-45", "46-50", "51-55", "56-60", "61-65", "66-70", "71-75", "76-80",
    "81-85", "86-90", "91-95", "96-99", "100",
]


def soc_colours(path: Path) -> dict[str, tuple[int, int, int]]:
    text = path.read_text("utf-8")
    found: dict[str, tuple[int, int, int]] = {}
    for key in SOC_BUCKETS:
        match = re.search(
            rf"^ {{10}}'?{re.escape(key)}'?: '(#[0-9A-Fa-f]{{6}})'$", text, re.M
        )
        assert match, f"{path.name}: no colour for SoC bucket {key!r}"
        hex_value = match.group(1).lstrip("#")
        found[key] = tuple(int(hex_value[i : i + 2], 16) for i in (0, 2, 4))
    return found


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_every_soc_bucket_has_a_colour(path) -> None:
    """Regression: the "0" bucket had none, so an empty battery fell back to a
    default colour."""
    assert len(soc_colours(path)) == len(SOC_BUCKETS)


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_soc_runs_red_through_blue_to_green(path) -> None:
    """Regression: the ramp was inverted -- 100% drew red -- and bottomed out in
    near-black (#000040) at 1-5%, which is unreadable on the timeline."""
    colours = soc_colours(path)
    low, mid, high = colours["0"], colours["46-50"], colours["100"]

    assert low[0] > 200 and low[1] < 60, f"0% should be red, got {low}"
    assert mid[2] > max(mid[0], mid[1]), f"~50% should be blue, got {mid}"
    assert high[1] > 150 and high[0] < 80, f"100% should be green, got {high}"


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_the_soc_ramp_is_monotonic(path) -> None:
    """Red falls away and green rises across the range, with no reversals."""
    colours = soc_colours(path)
    reds = [colours[k][0] for k in SOC_BUCKETS]
    greens = [colours[k][1] for k in SOC_BUCKETS]
    assert reds == sorted(reds, reverse=True), f"{path.name}: red is not monotonic"
    assert greens[-1] > greens[0], f"{path.name}: green does not rise"


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_no_soc_bucket_is_too_dark_for_the_timeline(path) -> None:
    """The old low end was #000040. On a white card that is near-black."""
    for key, colour in soc_colours(path).items():
        assert sum(colour) > 120, f"{path.name}: {key} is {colour}, too dark"


@pytest.mark.parametrize("path", HISTORY, ids=lambda p: p.name)
def test_soc_and_controller_colours_do_not_collide(path) -> None:
    """Both live in one stateColors map; a shared value would be ambiguous."""
    controller = set(dashboard_colours(path).values())
    for key, colour in soc_colours(path).items():
        assert hex_of(colour) not in controller, (
            f"{path.name}: SoC {key} shares {hex_of(colour)} with a controller state"
        )
