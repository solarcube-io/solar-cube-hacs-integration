"""Solar Cube LCD display controller for the Solar Cube PRO S1.

Renders a solar-energy dashboard image (170×320 portrait) and sends
it as a raw RGB565 POST request to the Solar LCD Bridge service running
on the Ubuntu host (outside Docker).  No direct USB access needed.

The bridge is developed separately at
https://dev.azure.com/roygard/Solar%20Cube%20%28Technology%29/_git/Solar_LCD_Bridge
and its CONTRACT.md pins the wire format and shared default token used
here. Refreshes every LCD_REFRESH_INTERVAL seconds.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import io
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, NamedTuple

from .const import (
    DATA_ENTRIES,
    DEFAULT_CURRENCY,
    DEFAULT_S1_LCD_BRIDGE_URL,
    DOMAIN,
    ISSUE_LCD_BRIDGE,
)
from .sensor_definitions import scale_value

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

_LOGGER = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────────
LCD_REFRESH_INTERVAL = 5   # seconds – display refresh
HTTP_TIMEOUT = 4           # seconds – bridge POST timeout
STATUS_TIMEOUT = 4         # seconds – bridge /status timeout

# Wire-protocol version we speak. Must match PROTOCOL_VERSION in the Solar LCD
# Bridge; see that repository's CONTRACT.md.
PROTOCOL_VERSION = 1

# Display size – portrait orientation (170 wide × 320 tall)
W = 170
H = 320
FRAME_BYTES = W * H * 2

# ── Layout ──────────────────────────────────────────────────────────────────────
# Module level so the layout is described exactly once: the tests derive the
# geometry from here rather than restating the arithmetic, which had already
# drifted out of step with the renderer more than once.
LX1, LX2 = 2, 84      # left column  (82 px wide)
RX1, RX2 = 86, 168    # right column (82 px wide)
# Section y-bounds (top, bottom)
Y_LOGO = (0,  34)
Y_HEMS = (36, 49)
Y_MODE = (54, 87)
Y_R1   = (89, 146)   # PV | DOM
Y_R2   = (148, 205)  # BATERIA | SIEĆ
Y_S1   = (207, 261)  # CENA | GODZINA
Y_S2   = (263, 318)  # OSZCZĘDNOŚĆ | NAPIĘCIE


class ModeCard(NamedTuple):
    """Where the operation-mode card puts its icon and its two text rows."""

    icon_r: int
    icon_cx: int
    icon_cy: int
    text_x: int
    text_w: int
    caption_top: int
    caption_bottom: int
    value_top: int
    value_bottom: int


def mode_card_geometry() -> ModeCard:
    """Geometry of the operation-mode card.

    The icon fills the section height less a small margin, sits on the left, and
    the caption and state stack in the column beside it.
    """
    y1m, y2m = Y_MODE
    icon_r = (y2m - y1m) // 2 - 3
    icon_cx = LX1 + icon_r + 2
    text_x = icon_cx + icon_r + 5
    return ModeCard(
        icon_r=icon_r,
        icon_cx=icon_cx,
        icon_cy=(y1m + y2m) // 2,
        text_x=text_x,
        text_w=RX2 - text_x - 3,
        caption_top=y1m + 2,
        caption_bottom=y1m + 15,
        value_top=y1m + 16,
        value_bottom=y2m - 2,
    )

# ── Colour palette ──────────────────────────────────────────────────────────────
BG       = (26,  27,  31)  # #1A1B1F
PV_TILE_C = (255, 152, 0)  # #FF9800
HOUSE_TILE_C = (146, 165, 175)  # #92A5AF
BAT_C    = (0,   225, 215)
BAT_TILE_C = (233, 30, 99)  # #E91E63
GRID_EXPORT_C = (180, 80, 255)  # #B450FF
GRID_IMPORT_C = (72, 143, 194)  # #488FC2
SAVINGS_TILE_C = (0, 200, 75)  # #00C84B, same as HEMS ACTIVE
SELL_TILE_C = (55, 254, 180)      # #37FEB4, left price tile
BUY_TILE_C  = (0,  225, 215)      # #00E1D7, right price tile
VOLTAGE_TILE_C = (52, 152, 219)   # #3498DB
WHITE    = (255, 255, 255)
GRAY     = (120, 150, 175)
GREEN    = (0,   200,  75)
BADGE_OK_FILL    = (8, 28, 14)   # dark green behind "EMS ACTIVE"
BADGE_ALERT_FILL = (34, 8, 10)   # dark red behind "EMS INACTIVE"

# ── Localisation strings ────────────────────────────────────────────────────────
_STRINGS: dict[str, dict[str, str]] = {
    "mode_label":      {"pl": "TRYB PRACY",          "en": "OPERATION MODE"},
    "hems_active":     {"pl": "EMS AKTYWNY",          "en": "EMS ACTIVE"},
    "hems_inactive":   {"pl": "EMS NIEAKTYWNY",       "en": "EMS INACTIVE"},
    "house_label":     {"pl": "DOM",                  "en": "HOUSE"},
    "bat_label":       {"pl": "BATERIA",               "en": "BATTERY"},
    "grid_label":      {"pl": "SIEĆ",                 "en": "GRID"},
    "export_label":    {"pl": "EKSPORT",               "en": "EXPORT"},
    "import_label":    {"pl": "IMPORT",                "en": "IMPORT"},
    "charging_label":  {"pl": "ŁADOWANIE",             "en": "CHARGING"},
    "discharging_label": {"pl": "ROZŁADOWANIE",        "en": "DISCHARGING"},
    "savings_label":   {"pl": "OSZCZĘDNOŚCI",         "en": "SAVINGS"},
    "buy_price_label": {"pl": "ZAKUP ENERGII",         "en": "ENERGY PURCHASE"},
    "sell_price_label": {"pl": "SPRZEDAŻ ENERGII",     "en": "ENERGY SALE"},
    "volt_label":      {"pl": "NAPIĘCIE SIECI",        "en": "GRID VOLTAGE"},
}

# Symbols for the Polish display, where a symbol reads more naturally than the
# ISO code. English deliberately shows the code: "PLN" is clearer than "zł" to a
# reader who is not Polish. Anything not listed falls back to the code itself,
# which is always correct if less pretty.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "PLN": "zł",
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CZK": "Kč",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
    "UAH": "₴",
    "HUF": "Ft",
}

_FA_SOLID_CODEPOINTS: dict[str, str] = {
    # Class names requested in the LCD mockup, mapped to Font Awesome glyphs.
    "solar": "\uf5ba",        # fa-solar-panel
    "house": "\uf015",        # fa-house
    "battery": "\uf241",      # fa-battery-three-quarters
    "tower": "\ue55b",        # fa-plug-circle-bolt
    "price_tag": "\uf02b",    # fa-tag
    "bars": "\uf201",         # fa-chart-line
    "coins": "\uf51e",        # fa-coins
    "shield": "\uf3ed",       # fa-shield-halved
    "shield_v": "\uf3ed",     # fa-shield-halved
    "weather": "\uf6c4",      # fa-cloud-sun
    "check_circle": "\uf058", # fa-circle-check
    "leaf": "\uf06c",         # mode card accent
}

_MODE_STATES: dict[str, tuple[dict[str, str], tuple[int, int, int]]] = {
    # Colours are shared with the Controller state timeline in the history
    # dashboards; tests/test_controller_colours.py keeps the two in step. Where a
    # mode describes the same flow as a tile, it reuses that tile's colour.
    "0": ({"pl": "Podtrzymanie", "en": "Maintain"}, (117, 117, 117)),  # #757575
    "2": ({"pl": "Ładowanie z PV", "en": "PV charging"}, (255, 145, 0)),  # #FF9100
    "4": (
        {"pl": "Rozładow... do domu", "en": "Discharge to home"},
        (77, 182, 172),  # #4DB6AC
    ),
    "7": ({"pl": "Autokonsumpcja", "en": "Auto-Consumption"}, (46, 139, 87)),  # #2E8B57
    # Charging from the grid: same colour as the grid tile when importing.
    "10": ({"pl": "Ładowanie z sieci", "en": "Grid charging"}, GRID_IMPORT_C),
    # Discharging to the grid: same colour as the grid tile when exporting.
    "20": (
        {"pl": "Rozładow... do sieci", "en": "Discharge to grid"},
        GRID_EXPORT_C,
    ),
}
_MODE_ERROR_LABELS = {"pl": "Błąd systemu", "en": "System Error"}
_MODE_ERROR_COLOR = (255, 0, 0)


def _s(key: str, lang: str) -> str:
    """Return localised string."""
    return _STRINGS.get(key, {}).get(lang) or _STRINGS.get(key, {}).get("en", key)


def currency_label(code: str | None, lang: str) -> str:
    """Return how a currency should be written on the panel."""
    normalised = (code or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY
    if lang == "pl":
        return _CURRENCY_SYMBOLS.get(normalised, normalised)
    return normalised


def _mode_code(controller_id: Any) -> str | None:
    """Return the controller id as a known mode key, or None.

    None covers everything the panel cannot interpret: a value outside
    _MODE_STATES, a non-numeric reading, and a missing one. The EMS badge and
    the mode card both key off this so they can never disagree about whether
    the controller is in a state we recognise.
    """
    try:
        code = str(int(float(str(controller_id).strip())))
    except (TypeError, ValueError):
        return None
    return code if code in _MODE_STATES else None


def _get_mode_state(controller_id: Any, lang: str) -> tuple[str, tuple[int, int, int]]:
    """Return the localized controller state and its display color."""
    code = _mode_code(controller_id)
    if code is None:
        return (
            _MODE_ERROR_LABELS.get(lang) or _MODE_ERROR_LABELS["en"],
            _MODE_ERROR_COLOR,
        )
    labels, color = _MODE_STATES[code]
    return labels.get(lang) or labels["en"], color


# ── Pillow helpers ──────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=4)
def _load_fonts(config_dir: str) -> dict[str, Any]:
    """Load fonts; prefers the bundled Inter variable font if available.

    Cached: the renderer runs every LCD_REFRESH_INTERVAL seconds and loading
    thirteen faces from disk each time is pure waste.
    """
    from PIL import ImageFont

    # Ordered list of directories to search (first match wins)
    component_fonts_dir = os.path.join(config_dir, "custom_components", "solar_cube", "fonts")
    search_dirs = [
        component_fonts_dir,
        # Legacy path from the separate third-party acemagic_lcd_led integration;
        # kept as a font fallback for users who still have it installed.
        os.path.join(config_dir, "custom_components", "acemagic_lcd_led",  "fonts"),
        os.path.join(os.path.dirname(__file__), "fonts"),
        # Ubuntu / Debian system fonts
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/truetype/freefont",
        "/usr/share/fonts/truetype/ubuntu",
    ]

    _REG  = [
             "Inter-VariableFont_opszwght.ttf",
             "Inter-Regular.ttf",
             "ArialRegular.ttf",
             "LiberationSans-Regular.ttf", "DejaVuSans.ttf", "FreeSans.ttf",
             "Ubuntu-R.ttf"]
    _BOLD = [
             "Inter-VariableFont_opszwght.ttf",
             "Inter-Bold.ttf",
             "RobotoCondensed-BoldItalic.ttf",
             "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf", "FreeSansBold.ttf",
             "Ubuntu-B.ttf"]
    # Prefer real TrueType/OpenType faces: Pillow can only open .woff2 when its
    # bundled FreeType was compiled with brotli/woff2 support, which is not
    # guaranteed in the Home Assistant container images.
    _FA_SOLID = ["fa-solid-900.ttf", "fa-solid-900.otf", "fa-solid-900.woff2"]

    def _find(names: list[str], size: int) -> Any:
        for d in search_dirs:
            for name in names:
                path = os.path.join(d, name)
                if os.path.isfile(path):
                    try:
                        return ImageFont.truetype(path, size)
                    except OSError as err:
                        _LOGGER.debug("Cannot load font %s: %s", path, err)
        return ImageFont.load_default()

    def _find_optional(names: list[str], size: int) -> Any | None:
        for d in search_dirs:
            for name in names:
                path = os.path.join(d, name)
                if os.path.isfile(path):
                    try:
                        return ImageFont.truetype(path, size)
                    except OSError as err:
                        _LOGGER.debug("Cannot load font %s: %s", path, err)
        return None

    fonts = {
        "nano":   _find(_REG,  6),
        "micro":  _find(_REG,  7),
        "tiny":   _find(_REG,  8),
        "badge":  _find(_REG,  9),
        "small":  _find(_REG,  10),
        "medium": _find(_REG,  12),
        "bold":   _find(_BOLD, 13),
        "large":  _find(_BOLD, 19),
        "xlarge": _find(_BOLD, 26),
        "fa_tiny": _find_optional(_FA_SOLID, 10),
        "fa_small": _find_optional(_FA_SOLID, 13),
        "fa_medium": _find_optional(_FA_SOLID, 17),
        "fa_large": _find_optional(_FA_SOLID, 21),
    }

    if fonts["fa_medium"] is None:
        _LOGGER.warning(
            "Font Awesome face could not be loaded from %s; the LCD will fall back "
            "to simplified vector icons. Install fa-solid-900.ttf/.otf to enable "
            "the icon set (Pillow cannot always read .woff2)",
            component_fonts_dir,
        )

    return fonts


@functools.lru_cache(maxsize=4)
def _load_logo(config_dir: str):
    """Load logo image from known locations. Returns PIL image or None.

    Cached alongside the fonts: this parses an SVG and base64-decodes an
    embedded bitmap, which must not happen on every refresh tick.
    """
    from PIL import Image

    def _tint_for_lcd(logo: Any) -> Any:
        tinted = Image.new("RGBA", logo.size, (255, 255, 255, 0))
        alpha = logo.getchannel("A")
        tinted.putalpha(alpha)
        return tinted

    candidates = [
        os.path.join(config_dir, "custom_components", "solar_cube", "assets", "solar_cube_logo.svg"),
        os.path.join(config_dir, "custom_components", "solar_cube", "assets", "logo.png"),
        os.path.join(config_dir, "custom_components", "solar_cube", "assets", "solar_cube_logo.png"),
        os.path.join(config_dir, "custom_components", "solar_cube", "logo.png"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                if path.lower().endswith(".svg"):
                    with open(path, encoding="utf-8") as svg_file:
                        svg = svg_file.read()
                    match = re.search(r"data:image/[^;]+;base64,([^\"']+)", svg)
                    if match:
                        raw = base64.b64decode(match.group(1))
                        return _tint_for_lcd(Image.open(io.BytesIO(raw)).convert("RGBA"))
                    continue
                logo = Image.open(path).convert("RGBA")
                if os.path.basename(path) in {"solar_cube_logo.png", "logo.png"}:
                    return _tint_for_lcd(logo)
                return logo
            except (OSError, ValueError) as err:
                _LOGGER.debug("Cannot load logo %s: %s", path, err)
    return None


def _fit_text(fonts_d: dict[str, Any], text: str, max_w: int,
               start: str = "xlarge", max_h: int | None = None) -> tuple[Any, str]:
    """Return (font, text) fitting within max_w px, and max_h px when given.

    Height matters wherever the slot is shorter than the largest face: a label
    short enough to pass the width check would otherwise keep a 19 px font in a
    17 px slot and be clipped by the card border.
    """
    _ORDER = ["xlarge", "large", "bold", "medium", "small", "tiny", "micro", "nano"]
    idx    = _ORDER.index(start) if start in _ORDER else 0
    for key in _ORDER[idx:]:
        f = fonts_d[key]
        if _text_width(f, text) > max_w:
            continue
        if max_h is not None and _text_height(f, text) > max_h:
            continue
        return f, text
    f = fonts_d["nano"]
    while len(text) > 3 and _text_width(f, text + "\u2026") > max_w:
        text = text[:-1]
    return f, text + "\u2026"


def _uniform_font(
    fonts_d: dict[str, Any],
    texts: list[str],
    max_w: int,
    start: str,
    max_h: int | None = None,
) -> Any:
    """Return the largest face that fits *every* text in the slot.

    Sizing each label on its own makes the card change typography as the state
    changes: a short label like "Maintain" keeps the 19 px face while
    "Discharge to home" drops to 13 px. Fitting the whole set once keeps one
    size for every state.
    """
    _ORDER = ["xlarge", "large", "bold", "medium", "small", "tiny", "micro", "nano"]
    idx = _ORDER.index(start) if start in _ORDER else 0
    for key in _ORDER[idx:]:
        font = fonts_d[key]
        if all(
            _text_width(font, t) <= max_w
            and (max_h is None or _text_height(font, t) <= max_h)
            for t in texts
        ):
            return font
    return fonts_d["nano"]


def _mode_label_font(
    fonts_d: dict[str, Any], lang: str, max_w: int, max_h: int
) -> Any:
    """Font for the operation-mode label, uniform across every state.

    Fitted per language rather than globally: a user only ever sees one, and
    the longer Polish labels would otherwise shrink the English ones too.
    """
    labels = [labels[lang] for labels, _colour in _MODE_STATES.values()]
    labels.append(_MODE_ERROR_LABELS.get(lang, _MODE_ERROR_LABELS["en"]))
    return _uniform_font(fonts_d, labels, max_w, "large", max_h)


def _text_height(font: Any, text: str) -> int:
    """Ink height of ``text``, including any descender."""
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]


def _text_width(font: Any, text: str) -> int:
    try:
        return int(font.getlength(text))
    except AttributeError:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]


def _draw_centered(draw: Any, cx: int, y: int, text: str, font: Any, color: tuple) -> None:
    w = _text_width(font, text)
    draw.text((cx - w // 2, y), text, font=font, fill=color)


def _draw_vcentered(
    draw: Any,
    x: int,
    y_top: int,
    y_bottom: int,
    text: str,
    font: Any,
    color: tuple,
) -> None:
    """Draw ``text`` with its ink box centred between y_top and y_bottom.

    Pillow positions text by the font's ascender, not by the ink it actually
    puts on the panel, so drawing different face sizes at one fixed y makes the
    smaller ones sit high in their row. Centring on the bbox keeps every size
    optically centred -- the same correction the EMS badge already applies.
    """
    bbox = font.getbbox(text)
    ink_h = bbox[3] - bbox[1]
    y = (y_top + y_bottom - ink_h) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=color)


def _draw_rounded_rect(
    draw: Any,
    x1: int, y1: int, x2: int, y2: int,
    r: int,
    fill: tuple,
    outline: tuple | None = None,
    lw: int = 1,
) -> None:
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    draw.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
    draw.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)
    for cx, cy in [
        (x1 + r, y1 + r),
        (x2 - r, y1 + r),
        (x1 + r, y2 - r),
        (x2 - r, y2 - r),
    ]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    if outline:
        draw.arc([x1,        y1,        x1 + 2*r, y1 + 2*r], 180, 270, fill=outline, width=lw)
        draw.arc([x2 - 2*r, y1,        x2,        y1 + 2*r], 270, 360, fill=outline, width=lw)
        draw.arc([x1,        y2 - 2*r, x1 + 2*r, y2],        90,  180, fill=outline, width=lw)
        draw.arc([x2 - 2*r, y2 - 2*r, x2,        y2],        0,   90,  fill=outline, width=lw)
        draw.line([x1 + r, y1, x2 - r, y1], fill=outline, width=lw)
        draw.line([x1 + r, y2, x2 - r, y2], fill=outline, width=lw)
        draw.line([x1, y1 + r, x1, y2 - r], fill=outline, width=lw)
        draw.line([x2, y1 + r, x2, y2 - r], fill=outline, width=lw)


# ── RGB565 encoding ─────────────────────────────────────────────────────────────

def _encode_rgb565_portrait_py(img: Any) -> bytes:
    """Reference encoder: pure Python, columns right→left, big-endian RGB565."""
    pixels = img.load()
    data = bytearray()
    for x in range(img.width - 1, -1, -1):
        for y in range(img.height):
            r, g, b = pixels[x, y]
            r5 = (r >> 3) & 0x1F
            g6 = (g >> 2) & 0x3F
            b5 = (b >> 3) & 0x1F
            color = (r5 << 11) | (g6 << 5) | b5
            data.append((color >> 8) & 0xFF)
            data.append(color & 0xFF)
    return bytes(data)


def _pil_to_rgb565_portrait(img: Any) -> bytes:
    """Encode a PIL Image to RGB565 bytes in the panel's portrait scan order.

    Scan order is columns right→left, each column top→bottom, big-endian.
    Uses numpy when available (~50x faster than the per-pixel loop at the 5 s
    refresh rate); the pure-Python encoder is kept as a fallback and as the
    reference implementation the vectorised path is tested against.
    """
    try:
        import numpy as np
    except ImportError:
        return _encode_rgb565_portrait_py(img)

    # (H, W, 3) -> (W, H, 3) with the x axis reversed, giving the scan order above.
    arr = np.asarray(img.convert("RGB"), dtype=np.uint16)
    columns = arr.transpose(1, 0, 2)[::-1]
    red = (columns[..., 0] >> 3) << 11
    green = (columns[..., 1] >> 2) << 5
    blue = columns[..., 2] >> 3
    # ">u2" writes the high byte first, matching the reference encoder.
    return (red | green | blue).astype(">u2").tobytes()


# ── Data helpers ─────────────────────────────────────────────────────────────────

def _safe_float(val: Any, key: str = "") -> float | None:
    """Return the display-scaled float for a raw coordinator value, or None.

    Scaling is delegated to sensor_definitions.scale_value so the LCD and the
    sensor platform can never disagree about a divisor.
    """
    try:
        scaled = scale_value(key, val)
        return None if scaled is None else float(scaled)
    except (TypeError, ValueError):
        return None


def _fmt_kw(watts: float | None) -> str:
    if watts is None:
        return "---"
    return f"{abs(watts) / 1000:.1f}"


# ── Icon drawing ─────────────────────────────────────────────────────────────────

def _draw_fa_icon(draw: Any, name: str, cx: int, cy: int, col: tuple, font: Any | None) -> bool:
    """Draw a Font Awesome icon centered on (cx, cy). Returns False if unavailable."""
    if font is None:
        return False
    glyph = _FA_SOLID_CODEPOINTS.get(name)
    if not glyph:
        return False
    try:
        bbox = font.getbbox(glyph)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text((cx - w // 2 - bbox[0], cy - h // 2 - bbox[1]), glyph, font=font, fill=col)
        return True
    except Exception:  # noqa: BLE001
        return False


def _draw_icon(
    draw: Any,
    name: str,
    cx: int,
    cy: int,
    col: tuple,
    s: int = 7,
    font: Any | None = None,
) -> None:
    """Draw a simple geometric icon centered at (cx, cy).  s = half-size."""
    if _draw_fa_icon(draw, name, cx, cy, col, font):
        return

    if name == "solar":
        # Small sun above solar panel grid
        draw.ellipse([cx - 3, cy - s, cx + 3, cy - s + 6], outline=col, width=1)
        for dx, dy in [(0, -s - 2), (0, -1), (-s - 2, -s + 3), (s + 2, -s + 3)]:
            draw.point((cx + dx // 2, cy + dy // 2), fill=col)
        py = cy - s + 8
        for r in range(2):
            for c in range(3):
                x1 = cx - s + 1 + c * ((s * 2 - 2) // 3 + 1)
                y1 = py + r * ((s - 2) // 2 + 1)
                x2 = x1 + (s * 2 - 2) // 3 - 1
                y2 = y1 + (s - 2) // 2 - 1
                draw.rectangle([x1, y1, x2, y2], outline=col, width=1)

    elif name == "house":
        roof = [(cx, cy - s), (cx - s, cy - 1), (cx + s, cy - 1)]
        for i in range(3):
            draw.line([roof[i], roof[(i + 1) % 3]], fill=col, width=1)
        draw.rectangle([cx - s + 2, cy - 1, cx + s - 2, cy + s], outline=col, width=1)
        draw.rectangle([cx - 2, cy + 2, cx + 2, cy + s], fill=col)

    elif name == "battery":
        bx1, by1, bx2, by2 = cx - s - 1, cy - s // 2, cx + s - 1, cy + s // 2
        draw.rectangle([bx1, by1, bx2, by2], outline=col, width=1)
        draw.rectangle([bx2, cy - s // 4, bx2 + 2, cy + s // 4], fill=col)
        bw = (bx2 - bx1 - 4) // 3
        for i in range(3):
            x = bx1 + 2 + i * (bw + 1)
            draw.rectangle([x, by1 + 2, x + bw - 1, by2 - 2], fill=col)

    elif name == "tower":
        draw.line([(cx - s, cy - s), (cx + s, cy - s)], fill=col, width=1)
        draw.line([(cx - s, cy - s), (cx - s // 2, cy + s)], fill=col, width=1)
        draw.line([(cx + s, cy - s), (cx + s // 2, cy + s)], fill=col, width=1)
        draw.line([(cx - s // 2, cy - s // 4), (cx + s // 2, cy - s // 4)], fill=col, width=1)
        draw.line([(cx - s // 2, cy + s), (cx + s // 2, cy + s)], fill=col, width=1)

    elif name == "price_tag":
        draw.polygon([(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)],
                     outline=col, fill=None)
        draw.ellipse([cx - 1, cy - s + 2, cx + 1, cy - s + 4], fill=col)

    elif name == "bars":
        bw = max(2, s * 2 // 4)
        for i in range(3):
            bh = (s // 3 + 1) * (i + 1)
            bx = cx - s + i * (bw + 2)
            draw.rectangle([bx, cy + s - bh, bx + bw - 1, cy + s - 1], fill=col)

    elif name == "coins":
        draw.ellipse([cx - s, cy - s // 3, cx + s, cy + s - s // 3], outline=col, width=1)
        draw.ellipse([cx - s, cy - s, cx + s, cy + s // 3], outline=col, width=1)
        draw.line([(cx + s // 3, cy - s + 1), (cx + s, cy - s // 2)], fill=col, width=1)

    elif name == "shield":
        pts = [(cx, cy - s), (cx + s, cy - s // 2), (cx + s, cy + s // 3),
               (cx, cy + s), (cx - s, cy + s // 3), (cx - s, cy - s // 2)]
        for i in range(len(pts)):
            draw.line([pts[i], pts[(i + 1) % len(pts)]], fill=col, width=1)
        draw.line([(cx - s // 2, cy + s // 6), (cx - s // 6, cy + s // 2),
                   (cx + s // 2, cy - s // 4)], fill=col, width=1)

    elif name == "shield_v":
        pts = [(cx, cy - s), (cx + s, cy - s // 2), (cx + s, cy + s // 3),
               (cx, cy + s), (cx - s, cy + s // 3), (cx - s, cy - s // 2)]
        for i in range(len(pts)):
            draw.line([pts[i], pts[(i + 1) % len(pts)]], fill=col, width=1)
        draw.line([(cx, cy - s // 3), (cx, cy + s // 3)], fill=col, width=1)
        draw.line([(cx - s // 3, cy), (cx + s // 3, cy)], fill=col, width=1)

    elif name == "weather":
        draw.arc([cx - s + 2, cy - s // 4, cx + s - 2, cy + s], 180, 360, fill=col, width=1)
        draw.arc([cx - s // 2, cy - s, cx + s, cy + s // 3], 200, 340, fill=col, width=1)
        draw.arc([cx - s, cy - s // 2, cx + s // 2, cy + s // 3], 220, 360, fill=col, width=1)

    elif name == "check_circle":
        draw.ellipse([cx - s, cy - s, cx + s, cy + s], outline=col, width=1)
        draw.line([(cx - s // 2, cy + s // 6), (cx - s // 6, cy + s // 2),
                   (cx + s // 2, cy - s // 3)], fill=col, width=1)

    elif name == "leaf":
        draw.arc([cx - s, cy - s, cx + s, cy + s // 2], 220, 360, fill=col, width=1)
        draw.arc([cx - s, cy - s // 2, cx + s, cy + s], 0, 140, fill=col, width=1)
        draw.line([(cx, cy - s // 2), (cx, cy + s // 2)], fill=col, width=1)


# ── Image renderer (runs in executor, no hass access) ───────────────────────────

def _render_image_pil(
    data: dict[str, Any],
    lang: str,
    config_dir: str,
    currency: str = DEFAULT_CURRENCY,
) -> Any:
    """Render the solar dashboard to a PIL Image (usable for preview/testing).

    ``currency`` is the Home Assistant configured currency, so the panel agrees
    with the monetary sensors rather than hard-coding one market.
    """
    from PIL import Image, ImageDraw

    fonts = _load_fonts(config_dir)

    # ── Sensor values ────────────────────────────────────────────────────────────
    pv_w      = _safe_float(data.get("pv_active_power"),          "pv_active_power")
    house_w   = _safe_float(data.get("consumption_active_power"), "consumption_active_power")
    ess_w     = _safe_float(data.get("ess_active_power"),         "ess_active_power")
    grid_w    = _safe_float(data.get("grid_active_power"),        "grid_active_power")
    soc       = _safe_float(data.get("ess_soc"),                  "ess_soc")
    grid_voltages = [
        _safe_float(data.get("grid_voltage_l1"), "grid_voltage_l1"),
        _safe_float(data.get("grid_voltage_l2"), "grid_voltage_l2"),
        _safe_float(data.get("grid_voltage_l3"), "grid_voltage_l3"),
    ]
    valid_grid_voltages = [v for v in grid_voltages if v is not None]
    grid_v    = (
        sum(valid_grid_voltages) / len(valid_grid_voltages)
        if valid_grid_voltages
        else None
    )
    buy_p     = _safe_float(data.get("buy_energy_price"),         "buy_energy_price")
    sell_p    = _safe_float(data.get("sell_energy_price"),        "sell_energy_price")
    savings   = _safe_float(data.get("optimised_energy_total_savings"), "optimised_energy_total_savings")
    ctrl_id   = data.get("controller_id")

    bat_charging   = (ess_w or 0) >= 0
    grid_exporting = (grid_w or 0) <= 0

    # ── Create canvas with fixed LCD background ──────────────────────────────────
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ── Helpers ───────────────────────────────────────────────────────────────────
    def card(x1: int, y1: int, x2: int, y2: int, border: tuple,
             fill: tuple = BG) -> None:
        _draw_rounded_rect(draw, x1, y1, x2, y2, 4, fill, border, 1)

    def ctext(cx: int, y: int, text: str, font: Any, color: tuple) -> None:
        _draw_centered(draw, cx, y, text, font, color)

    def ltext(x: int, y: int, text: str, font: Any, color: tuple) -> None:
        draw.text((x, y), text, font=font, fill=color)

    def fit_ltext(x: int, y: int, text: str, max_w: int, start: str, color: tuple) -> None:
        font, txt = _fit_text(fonts, text, max_w, start)
        ltext(x, y, txt, font, color)

    # ── ① LOGO ───────────────────────────────────────────────────────────────────
    y1l, y2l = Y_LOGO
    logo = _load_logo(config_dir)
    if logo is not None:
        max_w, max_h = W - 6, y2l - y1l
        scale = min(max_w / max(1, logo.width), max_h / max(1, logo.height))
        lw = int(logo.width * scale)
        lh = int(logo.height * scale)
        logo_r = logo.resize((lw, lh), Image.Resampling.LANCZOS)
        logo_y = y1l + (max_h - lh) // 2 - 2
        img.paste(logo_r, ((W - lw) // 2, logo_y), logo_r)
    else:
        # Fallback: draw SC icon + "SOLAR CUBE" text
        ico_cx = 18
        ico_cy = (y1l + y2l) // 2
        r = 10
        # Half-filled circle icon (left=dark, right=white, thin border)
        draw.ellipse([ico_cx - r, ico_cy - r, ico_cx + r, ico_cy + r],
                     fill=(255, 255, 255), outline=WHITE)
        draw.rectangle([ico_cx - r - 1, ico_cy - r - 1, ico_cx, ico_cy + r + 1],
                       fill=(10, 15, 30))
        draw.arc([ico_cx - r, ico_cy - r, ico_cx + r, ico_cy + r],
                 90, 270, fill=WHITE, width=1)
        avail_sc = W - (ico_cx + r + 8)
        sc_font, sc_txt = _fit_text(fonts, "SOLAR CUBE", avail_sc, "xlarge")
        ltext(ico_cx + r + 6, ico_cy - _text_width(sc_font, "A") // 2 - 4, sc_txt, sc_font, WHITE)

    # ── ② HEMS badge ─────────────────────────────────────────────────────────────
    y1h, y2h = Y_HEMS
    # A controller state the panel cannot interpret means the EMS is not
    # driving the system, so the badge says so instead of claiming otherwise.
    ems_active = _mode_code(ctrl_id) is not None
    badge_t  = _s("hems_active" if ems_active else "hems_inactive", lang)
    badge_col = GREEN if ems_active else _MODE_ERROR_COLOR
    badge_fill = BADGE_OK_FILL if ems_active else BADGE_ALERT_FILL
    badge_font = fonts["badge"]
    badge_gap = 9
    badge_w  = _text_width(badge_font, badge_t) + 24 + (badge_gap - 5)
    bx1      = max(1, (W - badge_w) // 2)
    bx2      = min(W - 2, bx1 + badge_w)
    card(bx1, y1h, bx2, y2h, badge_col, badge_fill)
    dot_x    = bx1 + 6
    dot_cy   = (y1h + y2h) // 2
    draw.ellipse([dot_x - 3, dot_cy - 3, dot_x + 3, dot_cy + 3], fill=badge_col)
    badge_bbox = badge_font.getbbox(badge_t)
    badge_text_y = dot_cy - (badge_bbox[3] - badge_bbox[1]) // 2 - badge_bbox[1]
    ltext(dot_x + badge_gap, badge_text_y, badge_t, badge_font, badge_col)

    # ── ③ MODE card ───────────────────────────────────────────────────────────────
    y1m, y2m = Y_MODE
    mode_text, mode_color = _get_mode_state(ctrl_id, lang)
    card(LX1, y1m, RX2, y2m, mode_color, BG)

    mc = mode_card_geometry()
    draw.ellipse(
        [mc.icon_cx - mc.icon_r, mc.icon_cy - mc.icon_r,
         mc.icon_cx + mc.icon_r, mc.icon_cy + mc.icon_r],
        outline=mode_color, width=1,
    )
    _draw_icon(draw, "leaf", mc.icon_cx, mc.icon_cy, mode_color,
               mc.icon_r - 4, fonts.get("fa_large"))

    caption = f"{_s('mode_label', lang)}:"
    _draw_vcentered(draw, mc.text_x, mc.caption_top, mc.caption_bottom,
                    caption, fonts["small"], GRAY)

    value_h = mc.value_bottom - mc.value_top
    m_font = _mode_label_font(fonts, lang, mc.text_w, value_h)
    _, m_txt = _fit_text(fonts, mode_text, mc.text_w, "large")
    _draw_vcentered(draw, mc.text_x, mc.value_top, mc.value_bottom,
                    m_txt, m_font, mode_color)

    # ── ④ PV | DOM row ────────────────────────────────────────────────────────────
    y1, y2 = Y_R1

    def power_card(x1: int, x2: int, icn: str, title: str, col: tuple,
                   big_val: str, unit: str = "", sub1: str = "", sub2: str = "",
                   value_col: tuple = WHITE, unit_col: tuple = GRAY) -> None:
        """Power card.  Layout adapts to number of text items:
        2 items (PV/DOM): xlarge value + small unit at bottom.
        3 items (SIEĆ):   large value and unit in one row below sub1.
        4 items (BAT):    large value + title-sized sub1 + small sub2 at bottom.
        """
        card(x1, y1, x2, y2, col)
        cx_c = x1 + 52
        card_cx = (x1 + x2) // 2
        _draw_icon(draw, icn, x1 + 17, y1 + 18, col, 9, fonts.get("fa_medium"))
        title_font, title_txt = _fit_text(fonts, title, x2 - x1 - 39, "medium")
        title_y = y1 + 7
        ltext(x1 + 36, title_y, title_txt, title_font, col)
        if sub2:                             # 4-line (BAT)
            state_y = y1 + 37
            title_bbox = title_font.getbbox(title_txt)
            state_bbox = title_font.getbbox(sub1)
            value_bbox = fonts["large"].getbbox(big_val)
            title_center = title_y + (title_bbox[1] + title_bbox[3]) / 2
            state_center = state_y + (state_bbox[1] + state_bbox[3]) / 2
            value_center = (title_center + state_center) / 2
            value_y = round(value_center - (value_bbox[1] + value_bbox[3]) / 2)
            ctext(cx_c, value_y, big_val,  fonts["large"],  value_col)
            ctext(card_cx, state_y, sub1, title_font, col)
            ctext(card_cx, y1 + 47, sub2,  fonts["tiny"],  unit_col)
        elif sub1 and unit:                  # 3-line (SIEĆ)
            fit_ltext(x1 + 36, y1 + 23, sub1, x2 - x1 - 39, "small", col)
            value_font = fonts["large"]
            unit_font = fonts["small"]
            gap = 3
            value_w = _text_width(value_font, big_val)
            unit_w = _text_width(unit_font, unit)
            group_x = cx_c - (value_w + gap + unit_w) // 2
            value_y = y1 + 30
            value_bbox = value_font.getbbox(big_val)
            unit_bbox = unit_font.getbbox(unit)
            unit_y = value_y + value_bbox[3] - unit_bbox[3]
            ltext(group_x, value_y, big_val, value_font, value_col)
            ltext(group_x + value_w + gap, unit_y, unit, unit_font, unit_col)
        elif sub1:                           # 3-line no-unit
            ctext(cx_c, y1 + 16, big_val,  fonts["xlarge"], value_col)
            ctext(cx_c, y2 - 12, sub1,     fonts["medium"], col)
        else:                                # 2-line (PV / DOM)
            ctext(cx_c, y1 + 18, big_val,  fonts["xlarge"], value_col)
            if unit:
                ctext(cx_c, y2 - 11, unit, fonts["small"],  unit_col)

    power_card(LX1, LX2, "solar",  "PV",                      PV_TILE_C, _fmt_kw(pv_w), "kW",
               value_col=PV_TILE_C, unit_col=PV_TILE_C)
    power_card(RX1, RX2, "house",  _s("house_label", lang),   HOUSE_TILE_C, _fmt_kw(house_w), "kW",
               value_col=HOUSE_TILE_C, unit_col=HOUSE_TILE_C)

    # ── ⑤ BATERIA | SIEĆ row ─────────────────────────────────────────────────────
    y1, y2 = Y_R2

    soc_str   = f"{int(soc)}%" if soc is not None else "---"
    bat_state = _s("charging_label" if bat_charging else "discharging_label", lang)
    bat_kw    = f"{abs(ess_w) / 1000:.1f} kW" if ess_w is not None else "---"
    grid_flow = _s("export_label" if grid_exporting else "import_label", lang)
    grid_tile_c = GRID_EXPORT_C if grid_exporting else GRID_IMPORT_C

    power_card(LX1, LX2, "battery", _s("bat_label", lang),  BAT_TILE_C,  soc_str, "",
               bat_state, bat_kw, value_col=BAT_TILE_C, unit_col=BAT_TILE_C)
    power_card(RX1, RX2, "tower",   _s("grid_label", lang), grid_tile_c, _fmt_kw(grid_w), "kW",
               grid_flow, value_col=grid_tile_c, unit_col=grid_tile_c)

    # ── ⑥ CENA TERAZ | NASTĘPNA GODZINA ─────────────────────────────────────────
    y1, y2 = Y_S1
    money    = currency_label(currency, lang)
    per_kwh  = f"{money}/kWh"

    def stat_card(x1: int, x2: int, icn: str, title: str, col: tuple,
                  big_val: str, unit: str, title_col: tuple = GRAY,
                  unit_col: tuple = GRAY) -> None:
        card(x1, y1, x2, y2, col, BG)
        _draw_icon(draw, icn, x1 + 16, (y1 + y2) // 2 - 1, col, 9, fonts.get("fa_medium"))
        title_x = x1 + 31
        title_w = x2 - title_x - 2
        if title == _s("savings_label", lang):
            title_x = x1 + 4
            title_w = x2 - title_x - 4
            title_font, title_txt = _fit_text(fonts, title, title_w, "medium")
            ctext((x1 + x2) // 2, y1 + 4, title_txt, title_font, col)
            # Right-aligned, but never further left than the icon: the coins
            # glyph sits at x1 + 16 with a half-size of 9.
            value_left = x1 + 28
            value_right = x2 - 6
            value_font, value_txt = _fit_text(
                fonts, big_val, value_right - value_left, "large"
            )
            value_x = value_right - _text_width(value_font, value_txt)
            ltext(value_x, y1 + 25, value_txt, value_font, col)
            unit_x = x2 - _text_width(fonts["tiny"], unit) - 4
            ltext(unit_x, y2 - 9, unit, fonts["tiny"], col)
            return
        if title in {_s("buy_price_label", lang), _s("sell_price_label", lang)}:
            first, second = title.rsplit(" ", 1)
            fit_ltext(title_x, y1 + 2, first, title_w, "tiny", title_col)
            fit_ltext(title_x, y1 + 11, second, title_w, "tiny", title_col)
            val_y = y1 + 20
        else:
            t_font, t_txt = _fit_text(fonts, title, title_w, "small")
            ltext(title_x, y1 + 6, t_txt, t_font, title_col)
            val_y = y1 + 20
        ltext(title_x, val_y, big_val, fonts["large"], col)
        if unit == "V":
            unit_x = x2 - _text_width(fonts["small"], unit) - 4
            ltext(unit_x, y2 - 13, unit, fonts["small"], unit_col)
        else:
            # Fitted rather than drawn blind: currency units are localised and
            # "PLN/kWh" already uses 47 of the 49 px available.
            fit_ltext(title_x, y2 - 13, unit, x2 - title_x - 2, "small", unit_col)

    buyp_str = f"{buy_p:.2f}" if buy_p is not None else "---"
    sell_str = f"{sell_p:.2f}" if sell_p is not None else "---"

    stat_card(LX1, LX2, "bars", _s("sell_price_label", lang), SELL_TILE_C,
              sell_str, per_kwh, title_col=SELL_TILE_C, unit_col=SELL_TILE_C)
    stat_card(RX1, RX2, "price_tag", _s("buy_price_label", lang), BUY_TILE_C,
              buyp_str, per_kwh, title_col=BUY_TILE_C, unit_col=BUY_TILE_C)

    # ── ⑦ OSZCZĘDNOŚĆ | NAPIĘCIE SIECI ──────────────────────────────────────────
    y1, y2 = Y_S2

    sav_str  = f"{savings:.2f}" if savings is not None else "---"
    volt_str = f"{grid_v:.0f}" if grid_v is not None else "---"

    stat_card(LX1, LX2, "coins",  _s("savings_label", lang), SAVINGS_TILE_C, sav_str, money)
    stat_card(RX1, RX2, "shield", _s("volt_label", lang), VOLTAGE_TILE_C, volt_str, "V",
              title_col=VOLTAGE_TILE_C, unit_col=VOLTAGE_TILE_C)

    return img


def _render_image(
    data: dict[str, Any],
    lang: str,
    config_dir: str,
    currency: str = DEFAULT_CURRENCY,
) -> bytes:
    """Render the solar dashboard image and return RGB565 portrait bytes."""
    img = _render_image_pil(data, lang, config_dir, currency)
    return _pil_to_rgb565_portrait(img)


# ── HTTP bridge client ────────────────────────────────────────────────────────

class BridgeStatus(NamedTuple):
    """Outcome of probing the bridge's /status endpoint."""

    reachable: bool
    panel_connected: bool = False
    protocol: int | None = None
    frame_bytes: int | None = None
    #: Set when the bridge answered but we must not use it.
    fatal: str | None = None



class S1BridgeClient:
    """Sends RGB565 image frames to the Solar LCD Bridge over HTTP.

    The bridge runs on the Ubuntu host and handles USB access.
    All calls are blocking and must be run in an executor.
    """

    def __init__(self, bridge_url: str, token: str = "") -> None:
        base = (bridge_url or "").strip().rstrip("/")
        self.image_url  = f"{base}/image"
        self.status_url = f"{base}/status"
        self._headers   = {"X-Bridge-Token": token} if token else {}
        self.url_error  = validate_bridge_url(bridge_url)

    def send_image(self, rgb565_data: bytes) -> tuple[bool, str | None]:
        """POST raw RGB565 bytes to the bridge.

        Returns ``(ok, fatal_reason)``. ``fatal_reason`` is set for responses
        that will not fix themselves -- a rejected token or a frame the bridge
        refuses -- so the caller can tell the user instead of retrying in
        silence every few seconds. A missing panel or an unreachable bridge is
        transient and reported as ``(False, None)``.
        """
        if self.url_error is not None:
            return False, self.url_error

        try:
            req = urllib.request.Request(
                self.image_url,
                data=rgb565_data,
                method="POST",
                headers={
                    "Content-Type": "application/octet-stream",
                    **self._headers,
                },
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.status in (200, 204), None
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False, (
                    "the bridge rejected our token (HTTP 401). Set the "
                    "'Solar LCD Bridge token' option to the bridge's "
                    "BRIDGE_TOKEN value"
                )
            if exc.code == 400:
                return False, (
                    f"the bridge rejected the frame (HTTP 400: {exc.reason}). "
                    "This usually means the integration and the bridge are "
                    "different versions"
                )
            _LOGGER.debug("LCD bridge HTTP error %d: %s", exc.code, exc.reason)
        except (urllib.error.URLError, OSError) as exc:
            _LOGGER.debug("LCD bridge unreachable: %s", exc)
        except ValueError as exc:
            # urllib raises this for a URL it cannot dispatch at all.
            return False, f"the bridge URL {self.image_url!r} is not usable: {exc}"
        return False, None

    def fetch_status(self) -> BridgeStatus:
        """Query /status and judge whether we can talk to this bridge.

        Replaces a bool-returning health check: the interesting answers are
        *why* the bridge is unusable (wrong token, incompatible protocol,
        different frame geometry), which a boolean throws away.
        """
        if self.url_error is not None:
            return BridgeStatus(reachable=False, fatal=self.url_error)

        try:
            req = urllib.request.Request(self.status_url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=STATUS_TIMEOUT) as resp:
                if resp.status != 200:
                    return BridgeStatus(reachable=True)
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return BridgeStatus(
                    reachable=True,
                    fatal=(
                        "the bridge rejected our token (HTTP 401). Set the "
                        "'Solar LCD Bridge token' option to the bridge's "
                        "BRIDGE_TOKEN value"
                    ),
                )
            _LOGGER.debug("LCD bridge status HTTP %d: %s", exc.code, exc.reason)
            return BridgeStatus(reachable=True)
        except (urllib.error.URLError, OSError) as exc:
            _LOGGER.debug("LCD bridge status unreachable: %s", exc)
            return BridgeStatus(reachable=False)
        except ValueError as exc:
            _LOGGER.debug("LCD bridge status unreadable: %s", exc)
            return BridgeStatus(reachable=True)

        protocol = body.get("protocol")
        frame_bytes = body.get("frame_bytes")

        # A bridge older than the protocol field cannot be checked; let it
        # through rather than refusing to drive a display that may work.
        if protocol is not None and protocol != PROTOCOL_VERSION:
            return BridgeStatus(
                reachable=True,
                protocol=protocol,
                frame_bytes=frame_bytes,
                fatal=(
                    f"the bridge speaks protocol {protocol} but this integration "
                    f"speaks {PROTOCOL_VERSION}. Update whichever is older -- see "
                    "the Solar LCD Bridge CONTRACT.md"
                ),
            )
        if frame_bytes is not None and frame_bytes != FRAME_BYTES:
            return BridgeStatus(
                reachable=True,
                protocol=protocol,
                frame_bytes=frame_bytes,
                fatal=(
                    f"the bridge expects {frame_bytes}-byte frames but this "
                    f"integration renders {FRAME_BYTES}. The two are different "
                    "versions"
                ),
            )

        return BridgeStatus(
            reachable=True,
            panel_connected=bool(body.get("ok")),
            protocol=protocol,
            frame_bytes=frame_bytes,
        )


def validate_bridge_url(url: str | None) -> str | None:
    """Return a human-readable reason the bridge URL is unusable, else None."""
    candidate = (url or "").strip()
    if not candidate:
        return "no Solar LCD Bridge URL is configured"
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return (
            f"the bridge URL {candidate!r} must start with http:// or https:// "
            f"(got scheme {parsed.scheme or 'none'!r})"
        )
    if not parsed.hostname:
        return f"the bridge URL {candidate!r} has no host"
    return None


# ── Controller class ─────────────────────────────────────────────────────────

class SolarCubeLCDController:
    """Periodically renders the solar dashboard and POSTs it to the LCD Bridge."""

    # Consecutive transient failures before saying so at warning level once.
    QUIET_FAILURES = 12  # ~1 minute at LCD_REFRESH_INTERVAL
    # Back off to this while the bridge is unreachable, so a powered-off panel
    # does not cost a render and an HTTP attempt every few seconds all day.
    BACKOFF_INTERVAL = 60
    BACKOFF_AFTER = 3
    # Re-send an unchanged frame at least this often, so the panel recovers if
    # it ever drops one.
    HEARTBEAT_INTERVAL = 60

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        language: str = "en",
        bridge_url: str = DEFAULT_S1_LCD_BRIDGE_URL,
        bridge_token: str = "",
        currency: str = DEFAULT_CURRENCY,
    ) -> None:
        self._hass     = hass
        self._entry    = entry
        self._language = language
        self._currency = currency
        self._client   = S1BridgeClient(bridge_url, bridge_token)
        self._task: asyncio.Task | None = None
        self._running  = False
        self._failures = 0
        self._reported_fatal: str | None = None
        self._last_payload: bytes | None = None
        self._last_sent_at: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._clear_issue()
        # A tracked background task is cancelled automatically when the config
        # entry unloads or Home Assistant shuts down.
        self._task = self._entry.async_create_background_task(
            self._hass, self._loop(), name=f"{DOMAIN}_lcd_refresh"
        )
        _LOGGER.info(
            "Solar Cube LCD controller started (bridge=%s, refresh=%ds)",
            self._client.image_url, LCD_REFRESH_INTERVAL,
        )

    async def async_stop(self) -> None:
        """Stop the refresh loop and wait for the task to finish."""
        self._running = False
        self._clear_issue()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        _LOGGER.info("Solar Cube LCD controller stopped")

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _raise_issue(self, reason: str) -> None:
        """Surface an unusable bridge in Settings -> Repairs.

        The appliance's users cannot read Home Assistant logs, so a warning
        there is not enough on its own.
        """
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            ISSUE_LCD_BRIDGE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_LCD_BRIDGE,
            translation_placeholders={
                "reason": reason,
                "url": self._client.image_url.removesuffix("/image"),
            },
        )

    def _clear_issue(self) -> None:
        ir.async_delete_issue(self._hass, DOMAIN, ISSUE_LCD_BRIDGE)

    async def _async_probe(self) -> None:
        """Check compatibility once before the first frame.

        A protocol or geometry mismatch would otherwise only show up as a
        rejected frame, and only for someone watching the log.
        """
        status = await self._hass.async_add_executor_job(self._client.fetch_status)

        if status.fatal is not None:
            self._reported_fatal = status.fatal
            _LOGGER.warning("Solar Cube LCD display disabled: %s", status.fatal)
            self._raise_issue(status.fatal)
            return

        if not status.reachable:
            _LOGGER.info(
                "Solar Cube LCD bridge at %s is not answering yet; will keep trying",
                self._client.status_url,
            )
            return

        _LOGGER.info(
            "Solar Cube LCD bridge reachable (protocol=%s, panel %s)",
            status.protocol if status.protocol is not None else "unreported",
            "connected" if status.panel_connected else "not detected",
        )

    async def _loop(self) -> None:
        # CancelledError deliberately propagates: async_stop() awaits this task.
        await self._async_probe()
        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("LCD tick error: %s", exc)
            await asyncio.sleep(self._interval())

    def _interval(self) -> int:
        """Refresh interval, backed off while the bridge is not answering."""
        if self._reported_fatal is not None or self._failures >= self.BACKOFF_AFTER:
            return self.BACKOFF_INTERVAL
        return LCD_REFRESH_INTERVAL

    async def _tick(self) -> None:
        """Snapshot data → render (executor) → POST to bridge (executor)."""
        # Snapshot sensor data on the event-loop thread
        entry_state = (
            self._hass.data.get(DOMAIN, {})
            .get(DATA_ENTRIES, {})
            .get(self._entry.entry_id)
        )
        coordinator = (entry_state or {}).get("data_coordinator")
        raw_data: dict[str, Any] = dict(getattr(coordinator, "data", None) or {})

        # The coordinator refreshes every 30 s but the loop ticks every 5 s, so
        # most ticks would re-render and re-POST a byte-identical frame. Skip
        # those, keeping a heartbeat so a dropped frame still self-heals.
        fingerprint = repr(sorted(raw_data.items(), key=lambda kv: kv[0])).encode()
        now = self._hass.loop.time()
        unchanged = fingerprint == self._last_payload
        if (
            unchanged
            and self._reported_fatal is None
            and now - self._last_sent_at < self.HEARTBEAT_INTERVAL
        ):
            return

        config_dir = self._hass.config.config_dir
        lang       = self._language

        # Render image in executor (CPU-bound PIL work)
        try:
            rgb565_bytes: bytes = await self._hass.async_add_executor_job(
                _render_image, raw_data, lang, config_dir, self._currency
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("LCD render failed: %s", exc)
            return

        # Send to bridge in executor (blocking HTTP)
        ok, fatal = await self._hass.async_add_executor_job(
            self._client.send_image, rgb565_bytes
        )
        if ok:
            if self._reported_fatal is not None:
                self._clear_issue()
            self._failures = 0
            self._reported_fatal = None
            self._last_payload = fingerprint
            self._last_sent_at = now
            return

        if fatal is not None:
            # A misconfiguration: log it once at warning rather than burying it
            # in debug output that nobody has enabled.
            if self._reported_fatal != fatal:
                self._reported_fatal = fatal
                _LOGGER.warning("Solar Cube LCD display disabled: %s", fatal)
                self._raise_issue(fatal)
            return

        self._failures += 1
        if self._failures == self.QUIET_FAILURES:
            _LOGGER.warning(
                "Solar Cube LCD bridge at %s has not responded for %d attempts; "
                "continuing to retry quietly",
                self._client.image_url,
                self._failures,
            )
        else:
            _LOGGER.debug("LCD bridge send failed - will retry on next tick")
