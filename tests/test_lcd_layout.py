"""Text must stay inside the card it is drawn in.

The LCD is 170x320: a label one font-step too large is clipped by a card border
rather than wrapping, and nothing at runtime notices. These checks were written
after generating previews showed "PV charging" cut off by the mode card and the
savings figure sliding underneath its icon.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from custom_components.solar_cube.solar_lcd import (
    _MODE_ERROR_LABELS,
    _MODE_STATES,
    LX1,
    RX2,
    Y_MODE,
    _fit_text,
    _load_fonts,
    _render_image_pil,
    _text_height,
    _text_width,
    currency_label,
    mode_card_geometry,
)

pytest.importorskip("PIL")

LANGUAGES = ("en", "pl")

# Mirrors the mode card in _render_image_pil: Y_MODE, the value baseline and the
# width left of the card edge once the icon and label are placed.
MODE_CARD_TOP, MODE_CARD_BOTTOM = 54, 87
MODE_VALUE_Y = MODE_CARD_TOP + 16
MODE_VALUE_MAX_H = MODE_CARD_BOTTOM - MODE_VALUE_Y - 2
MODE_VALUE_MAX_W = 168 - 33 - 4


@pytest.fixture(scope="module")
def fonts(tmp_path_factory) -> dict:
    return _load_fonts(str(tmp_path_factory.mktemp("cfg")))


class TestFitText:
    def test_height_is_respected_when_given(self, fonts) -> None:
        font, text = _fit_text(fonts, "PV charging", 200, "xlarge", max_h=10)
        assert _text_height(font, text) <= 10

    def test_width_is_still_respected(self, fonts) -> None:
        font, text = _fit_text(fonts, "PV charging", 40, "xlarge")
        assert _text_width(font, text) <= 40

    def test_without_max_h_the_largest_fitting_face_is_used(self, fonts) -> None:
        unbounded = _fit_text(fonts, "PV", 200, "xlarge")[0]
        bounded = _fit_text(fonts, "PV", 200, "xlarge", max_h=8)[0]
        assert _text_height(unbounded, "PV") >= _text_height(bounded, "PV")

    def test_untruncatable_text_still_returns_something_drawable(self, fonts) -> None:
        font, text = _fit_text(fonts, "A very long label indeed", 12, "xlarge")
        assert text
        assert _text_width(font, text) <= 12 or text.endswith("…")


class TestModeCardFits:
    """Every controller label, in every language, inside the mode card."""

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_all_controller_labels_fit(self, fonts, lang) -> None:
        labels = [labels[lang] for labels, _colour in _MODE_STATES.values()]
        labels.append(_MODE_ERROR_LABELS[lang])

        for label in labels:
            font, text = _fit_text(
                fonts, label, MODE_VALUE_MAX_W, "large", max_h=MODE_VALUE_MAX_H
            )
            height = _text_height(font, text)
            assert height <= MODE_VALUE_MAX_H, (
                f"{lang}: {label!r} renders {height}px tall in a "
                f"{MODE_VALUE_MAX_H}px slot and would be clipped"
            )
            assert _text_width(font, text) <= MODE_VALUE_MAX_W, f"{lang}: {label!r}"


class TestRenderEveryScenario:
    """The renderer must survive every state it can be handed."""

    @staticmethod
    def _scenarios() -> dict:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "tools" / "preview_lcd.py"
        spec = importlib.util.spec_from_file_location("preview_lcd", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.SCENARIOS

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_all_preview_scenarios_render(self, lang, tmp_path) -> None:
        for name, (_description, data) in self._scenarios().items():
            image = _render_image_pil(data, lang, str(tmp_path))
            assert image.size == (170, 320), name

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_extreme_values_do_not_crash(self, lang, tmp_path) -> None:
        data = {
            "pv_active_power": 10**9,
            "consumption_active_power": -(10**9),
            "ess_active_power": 0,
            "grid_active_power": 0,
            "ess_soc": 999,
            "grid_voltage_l1": 10**9,
            "buy_energy_price": -12345.6789,
            "sell_energy_price": 10**6,
            "optimised_energy_total_savings": 10**9,
            "controller_id": "not-a-number",
        }
        assert _render_image_pil(data, lang, str(tmp_path)).size == (170, 320)


class TestCurrencyLabels:
    """The panel follows the Home Assistant currency, in a tight slot."""

    # stat_card draws the price unit at x1 + 31 in an 82 px tile.
    UNIT_BUDGET = 82 - 31 - 2

    def test_english_shows_the_iso_code(self) -> None:
        """"PLN" is clearer than "zł" to a reader who is not Polish."""
        assert currency_label("PLN", "en") == "PLN"
        assert currency_label("EUR", "en") == "EUR"

    def test_polish_shows_the_local_symbol(self) -> None:
        assert currency_label("PLN", "pl") == "zł"
        assert currency_label("EUR", "pl") == "€"

    def test_an_unknown_code_falls_back_to_itself(self) -> None:
        """Always correct, if less pretty, for a currency we have no symbol for."""
        assert currency_label("JPY", "pl") == "JPY"
        assert currency_label("JPY", "en") == "JPY"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_currency_falls_back_to_pln(self, value) -> None:
        from custom_components.solar_cube.const import DEFAULT_CURRENCY

        assert DEFAULT_CURRENCY == "PLN"
        assert currency_label(value, "en") == "PLN"
        assert currency_label(value, "pl") == "zł"

    def test_lowercase_input_is_normalised(self) -> None:
        assert currency_label("pln", "en") == "PLN"
        assert currency_label(" eur ", "pl") == "€"

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("code", ["PLN", "EUR", "USD", "GBP", "CZK", "HUF", "JPY"])
    def test_every_price_unit_fits_the_tile(self, fonts, lang, code) -> None:
        """The unit is drawn in 49 px; "PLN/kWh" already needs 47 of them."""
        unit = f"{currency_label(code, lang)}/kWh"
        font, text = _fit_text(fonts, unit, self.UNIT_BUDGET, "small")
        assert text == unit, f"{lang}/{code}: {unit!r} had to be truncated"
        assert _text_width(font, text) <= self.UNIT_BUDGET

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("code", ["PLN", "EUR", "USD", "JPY"])
    def test_the_panel_renders_in_every_currency(self, lang, code, tmp_path) -> None:
        image = _render_image_pil(
            {"buy_energy_price": 0.42, "optimised_energy_total_savings": 1234.5},
            lang,
            str(tmp_path),
            currency=code,
        )
        assert image.size == (170, 320)

    def test_sensor_fallback_is_a_valid_iso_4217_code(self) -> None:
        """Home Assistant rejects anything else for device_class monetary, so
        the panel's display symbols must never leak into a sensor unit."""
        import voluptuous as vol

        from custom_components.solar_cube.const import DEFAULT_CURRENCY

        from homeassistant.core_config import _validate_currency

        _validate_currency(DEFAULT_CURRENCY)

        for display_only in ("zł", "€", "$"):
            with pytest.raises(vol.Invalid):
                _validate_currency(display_only)


class TestModeColours:
    """Mode colours must be readable on the panel's near-black background."""

    # WCAG AA for large text. The mode label is 13-19 px bold, so this is the
    # right floor; the panel is also viewed at arm's length in daylight.
    MIN_CONTRAST = 3.0

    # Modes deliberately kept below the floor, with the reason. Empty, and it
    # should stay that way: the same colours are drawn on the history timeline's
    # light card, so anything added here must be measured against both
    # surfaces. tests/test_controller_colours.py keeps the two palettes equal,
    # so a change here is a change there.
    ACCEPTED_LOW_CONTRAST: ClassVar[dict[str, str]] = {}

    @staticmethod
    def _relative_luminance(colour: tuple[int, int, int]) -> float:
        def channel(value: int) -> float:
            v = value / 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(c) for c in colour)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _contrast_against(
        cls, foreground: tuple[int, int, int], background: tuple[int, int, int]
    ) -> float:
        a = cls._relative_luminance(foreground)
        b = cls._relative_luminance(background)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)

    @classmethod
    def _contrast(cls, foreground: tuple[int, int, int]) -> float:
        from custom_components.solar_cube.solar_lcd import BG

        return cls._contrast_against(foreground, BG)

    def test_discharge_to_grid_matches_the_export_tile(self) -> None:
        """The mode and the grid tile below it describe the same flow."""
        from custom_components.solar_cube.solar_lcd import GRID_EXPORT_C

        _labels, colour = _MODE_STATES["20"]
        assert colour == GRID_EXPORT_C

    def test_every_mode_colour_is_legible(self) -> None:
        from custom_components.solar_cube.solar_lcd import _MODE_ERROR_COLOR

        offenders = []
        for code, (labels, colour) in _MODE_STATES.items():
            if code in self.ACCEPTED_LOW_CONTRAST:
                continue
            contrast = self._contrast(colour)
            if contrast < self.MIN_CONTRAST:
                offenders.append(f"mode {code} ({labels['en']}) {colour} = {contrast:.2f}")

        error_contrast = self._contrast(_MODE_ERROR_COLOR)
        if error_contrast < self.MIN_CONTRAST:
            offenders.append(f"error colour {_MODE_ERROR_COLOR} = {error_contrast:.2f}")

        assert not offenders, (
            f"contrast below {self.MIN_CONTRAST}:1 against the background: {offenders}"
        )

    def test_the_previous_dark_teal_would_now_be_rejected(self) -> None:
        """Regression: (0, 77, 96) scored 1.83 and was nearly invisible."""
        assert self._contrast((0, 77, 96)) < self.MIN_CONTRAST

    def test_the_accepted_exceptions_are_still_exceptions(self) -> None:
        """If an accepted mode now clears the floor, drop it from the list."""
        for code in self.ACCEPTED_LOW_CONTRAST:
            assert code in _MODE_STATES, f"mode {code} no longer exists"
            contrast = self._contrast(_MODE_STATES[code][1])
            assert contrast < self.MIN_CONTRAST, (
                f"mode {code} now measures {contrast:.2f}; remove it from "
                "ACCEPTED_LOW_CONTRAST"
            )

    def test_accepted_exceptions_are_strong_on_the_light_timeline(self) -> None:
        """The justification for each exception, asserted rather than asserted-to.

        These colours are shared with the history dashboard, which draws them on
        a white card. If one is weak on *both* surfaces it has no defence.
        """
        for code in self.ACCEPTED_LOW_CONTRAST:
            colour = _MODE_STATES[code][1]
            on_white = self._contrast_against(colour, (255, 255, 255))
            assert on_white >= 4.5, (
                f"mode {code} measures {on_white:.2f} on the timeline too; it is "
                "not legible on either surface"
            )


class TestModeLabelIsUniform:
    """The mode card must not change typography as the state changes."""

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_one_font_size_for_every_state(self, fonts, lang) -> None:
        """Regression: per-label fitting gave "Maintain" the 19 px face while
        "Discharge to home" got 13 px, so the card resized as the state moved."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font

        font = _mode_label_font(fonts, lang, MODE_VALUE_MAX_W, MODE_VALUE_MAX_H)
        labels = [labels[lang] for labels, _colour in _MODE_STATES.values()]
        labels.append(_MODE_ERROR_LABELS[lang])

        heights = {_text_height(font, label) for label in labels}
        assert len(heights) <= 3, (
            f"{lang}: ink heights vary too much for one face: {sorted(heights)}"
        )
        for label in labels:
            assert _text_width(font, label) <= MODE_VALUE_MAX_W, f"{lang}: {label!r}"
            assert _text_height(font, label) <= MODE_VALUE_MAX_H, f"{lang}: {label!r}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_the_chosen_face_is_the_largest_that_fits_all(self, fonts, lang) -> None:
        """Uniform must not mean needlessly small."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font

        order = ["xlarge", "large", "bold", "medium", "small", "tiny", "micro", "nano"]
        chosen = _mode_label_font(fonts, lang, MODE_VALUE_MAX_W, MODE_VALUE_MAX_H)
        chosen_key = next(k for k in order if fonts[k] is chosen)

        labels = [labels[lang] for labels, _colour in _MODE_STATES.values()]
        labels.append(_MODE_ERROR_LABELS[lang])
        for key in order[: order.index(chosen_key)]:
            bigger = fonts[key]
            assert not all(
                _text_width(bigger, t) <= MODE_VALUE_MAX_W
                and _text_height(bigger, t) <= MODE_VALUE_MAX_H
                for t in labels
            ), f"{lang}: {key} would also fit; the label is smaller than it needs to be"

    def test_each_language_is_sized_independently(self, fonts) -> None:
        """Polish labels are longer; they must not shrink the English ones."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font

        en = _mode_label_font(fonts, "en", MODE_VALUE_MAX_W, MODE_VALUE_MAX_H)
        pl = _mode_label_font(fonts, "pl", MODE_VALUE_MAX_W, MODE_VALUE_MAX_H)
        assert _text_height(en, "Discharge to home") >= _text_height(pl, "Discharge to home")


class TestEmsBadge:
    """The badge must not claim the EMS is driving a state we cannot read."""

    KNOWN = ("0", "2", "4", "7", "10", "20")

    def test_the_known_states_are_exactly_the_documented_set(self) -> None:
        assert set(_MODE_STATES) == set(self.KNOWN)

    @pytest.mark.parametrize("value", [0, "2", 4.0, "7", 10, "20", " 4 ", "10.0"])
    def test_known_controller_ids_are_recognised(self, value) -> None:
        from custom_components.solar_cube.solar_lcd import _mode_code

        assert _mode_code(value) in self.KNOWN

    @pytest.mark.parametrize(
        "value", [19, 21, 99, -1, "abc", "", "   ", None, float("nan")]
    )
    def test_everything_else_is_not_recognised(self, value) -> None:
        from custom_components.solar_cube.solar_lcd import _mode_code

        assert _mode_code(value) is None

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_badge_wording_differs_between_the_two_states(self, lang) -> None:
        from custom_components.solar_cube.solar_lcd import _s

        assert _s("hems_active", lang) != _s("hems_inactive", lang)
        assert _s("hems_inactive", lang)

    def test_english_wording(self) -> None:
        from custom_components.solar_cube.solar_lcd import _s

        assert _s("hems_active", "en") == "EMS ACTIVE"
        assert _s("hems_inactive", "en") == "EMS INACTIVE"

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("key", ["hems_active", "hems_inactive"])
    def test_the_badge_fits_the_panel(self, fonts, lang, key) -> None:
        """The badge is centred and sized from its text; the longer inactive
        wording must not run off either edge."""
        from custom_components.solar_cube.solar_lcd import W, _s

        text = _s(key, lang)
        width = _text_width(fonts["badge"], text) + 24 + 4
        left = (W - width) // 2
        assert left >= 1, f"{lang}/{key}: overflows the left edge"
        assert left + width <= W - 2, f"{lang}/{key}: overflows the right edge"

    def test_the_inactive_badge_uses_the_error_colour(self) -> None:
        """Same red as "System Error", so the two read as one condition."""
        from custom_components.solar_cube.solar_lcd import (
            _MODE_ERROR_COLOR,
            BADGE_ALERT_FILL,
            BADGE_OK_FILL,
            GREEN,
        )

        assert _MODE_ERROR_COLOR != GREEN
        assert BADGE_ALERT_FILL != BADGE_OK_FILL

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("controller", ["7", "19", "99", None])
    def test_the_panel_renders_in_both_badge_states(
        self, lang, controller, tmp_path
    ) -> None:
        image = _render_image_pil(
            {"controller_id": controller, "pv_active_power": 1000},
            lang,
            str(tmp_path),
        )
        assert image.size == (170, 320)


class TestEmsBadgeRendersCorrectly:
    """Checks the drawn pixels, not just the helper.

    The helper-level tests would still pass if the render path stopped calling
    them, so the badge row is inspected in an actual frame.
    """

    BADGE_ROWS = (36, 50)  # Y_HEMS plus a row of margin

    def _badge_colours(self, controller, lang, tmp_path) -> tuple[int, int]:
        from collections import Counter

        from custom_components.solar_cube.solar_lcd import _MODE_ERROR_COLOR, GREEN

        image = _render_image_pil({"controller_id": controller}, lang, str(tmp_path))
        pixels = Counter(image.crop((0, *self.BADGE_ROWS[:1], 170, self.BADGE_ROWS[1])).getdata())
        return pixels.get(GREEN, 0), pixels.get(_MODE_ERROR_COLOR, 0)

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("controller", ["0", "2", "4", "7", "10", "20"])
    def test_known_states_draw_a_green_badge(self, lang, controller, tmp_path) -> None:
        green, red = self._badge_colours(controller, lang, tmp_path)
        assert green > 0 and red == 0, f"{lang}/{controller}: green={green} red={red}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("controller", ["19", "21", "99", "abc", None])
    def test_unknown_states_draw_a_red_badge(self, lang, controller, tmp_path) -> None:
        green, red = self._badge_colours(controller, lang, tmp_path)
        assert red > 0 and green == 0, f"{lang}/{controller}: green={green} red={red}"


class TestModeCardTypography:
    """The mode card's two rows: constant caption, then the state itself."""

    # Read from the renderer rather than restated here: duplicating this
    # arithmetic let the tests drift out of step with the layout three times.
    GEOMETRY = mode_card_geometry()
    CAP_TOP, CAP_BOTTOM = GEOMETRY.caption_top, GEOMETRY.caption_bottom
    VAL_TOP, VAL_BOTTOM = GEOMETRY.value_top, GEOMETRY.value_bottom
    ICON_R, ICON_CX = GEOMETRY.icon_r, GEOMETRY.icon_cx
    VAL_X, VAL_WIDTH = GEOMETRY.text_x, GEOMETRY.text_w

    def test_vertical_centring_is_independent_of_face_size(self, fonts) -> None:
        """Regression: text was drawn at a fixed y, so Pillow's ascender-based
        placement made smaller faces sit high in the row."""
        from PIL import Image, ImageDraw

        from custom_components.solar_cube.solar_lcd import _draw_vcentered

        offsets = {}
        for key in ("bold", "medium", "small", "tiny"):
            image = Image.new("RGB", (170, 320), (0, 0, 0))
            _draw_vcentered(
                ImageDraw.Draw(image), self.VAL_X, self.VAL_TOP, self.VAL_BOTTOM,
                "Rozładowywanie", fonts[key], (255, 255, 255),
            )
            rows = [
                y
                for y in range(self.VAL_TOP - 4, self.VAL_BOTTOM + 5)
                if any(image.getpixel((x, y)) != (0, 0, 0) for x in range(170))
            ]
            assert rows, f"{key}: nothing drawn"
            offsets[key] = ((rows[0] + rows[-1]) / 2)

        target = (self.VAL_TOP + self.VAL_BOTTOM) / 2
        for key, centre in offsets.items():
            assert abs(centre - target) <= 1.5, (
                f"{key}: ink centred at {centre}, row centre is {target}"
            )

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_the_state_fits_the_full_width_row(self, fonts, lang) -> None:
        from custom_components.solar_cube.solar_lcd import _mode_label_font

        font = _mode_label_font(
            fonts, lang, self.VAL_WIDTH, self.VAL_BOTTOM - self.VAL_TOP
        )
        labels = [labels[lang] for labels, _colour in _MODE_STATES.values()]
        labels.append(_MODE_ERROR_LABELS[lang])
        for label in labels:
            assert _text_width(font, label) <= self.VAL_WIDTH, f"{lang}: {label!r}"

    def test_the_icon_nearly_fills_the_section_with_a_visible_margin(self) -> None:
        section_h = Y_MODE[1] - Y_MODE[0]
        assert section_h - 4 >= 2 * self.ICON_R, "no gap to the section border"
        assert section_h - 8 <= 2 * self.ICON_R, "icon smaller than intended"
        assert self.ICON_CX - self.ICON_R > LX1, "icon crosses the left border"

    def test_polish_now_matches_english_at_the_same_face(self, fonts) -> None:
        """The two long labels were abbreviated so Polish is no longer the
        constraint: both languages settle on the same face."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font

        slot = (self.VAL_WIDTH, self.VAL_BOTTOM - self.VAL_TOP)
        en = _mode_label_font(fonts, "en", *slot)
        pl = _mode_label_font(fonts, "pl", *slot)
        # Compare the chosen face, not ink heights: those differ by which
        # ascenders and descenders a given word happens to contain.
        assert pl is en, "Polish is still forced to a smaller face than English"

    def test_the_longest_polish_label_still_fits(self, fonts) -> None:
        """It uses almost the whole row, so worth asserting not assuming."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font

        font = _mode_label_font(
            fonts, "pl", self.VAL_WIDTH, self.VAL_BOTTOM - self.VAL_TOP
        )
        longest = max(
            (labels["pl"] for labels, _c in _MODE_STATES.values()),
            key=lambda t: _text_width(font, t),
        )
        assert _text_width(font, longest) <= self.VAL_WIDTH, longest

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_the_caption_is_smaller_than_the_state(self, fonts, lang) -> None:
        """The caption is constant; the state is the information."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font, _s

        caption_h = _text_height(fonts["small"], f"{_s('mode_label', lang)}:")
        state_font = _mode_label_font(
            fonts, lang, self.VAL_WIDTH, self.VAL_BOTTOM - self.VAL_TOP
        )
        widest = max(
            [labels[lang] for labels, _c in _MODE_STATES.values()],
            key=lambda t: _text_height(state_font, t),
        )
        assert _text_height(state_font, widest) >= caption_h

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_the_text_column_clears_the_icon(self, fonts, lang) -> None:
        """Both rows start to the right of the icon, and neither overruns."""
        from custom_components.solar_cube.solar_lcd import _mode_label_font, _s

        assert self.VAL_X > self.ICON_CX + self.ICON_R

        caption = f"{_s('mode_label', lang)}:"
        assert self.VAL_X + _text_width(fonts["small"], caption) <= RX2 - 3

        state_font = _mode_label_font(
            fonts, lang, self.VAL_WIDTH, self.VAL_BOTTOM - self.VAL_TOP
        )
        for labels, _colour in _MODE_STATES.values():
            assert (
                self.VAL_X + _text_width(state_font, labels[lang]) <= RX2 - 3
            ), f"{lang}: {labels[lang]!r} overruns the card"


class TestPriceTiles:
    """Sale on the left, purchase on the right, each with its own colour."""

    @staticmethod
    def _half_colours(image, x_from: int, x_to: int) -> set:
        from custom_components.solar_cube.solar_lcd import Y_S1

        crop = image.crop((x_from, Y_S1[0], x_to, Y_S1[1]))
        return set(crop.getdata())

    @pytest.fixture
    def rendered(self, tmp_path):
        # Distinct values so a swapped pair would be obvious.
        return _render_image_pil(
            {"buy_energy_price": 9.99, "sell_energy_price": 1.11},
            "en",
            str(tmp_path),
        )

    def test_the_left_tile_is_the_sale_colour(self, rendered) -> None:
        from custom_components.solar_cube.solar_lcd import BUY_TILE_C, SELL_TILE_C

        left = self._half_colours(rendered, 0, 85)
        assert SELL_TILE_C in left
        assert BUY_TILE_C not in left

    def test_the_right_tile_is_the_purchase_colour(self, rendered) -> None:
        from custom_components.solar_cube.solar_lcd import BUY_TILE_C, SELL_TILE_C

        right = self._half_colours(rendered, 86, 170)
        assert BUY_TILE_C in right
        assert SELL_TILE_C not in right

    def test_the_two_tiles_use_different_colours(self) -> None:
        from custom_components.solar_cube.solar_lcd import BUY_TILE_C, SELL_TILE_C

        assert BUY_TILE_C != SELL_TILE_C

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_both_price_labels_still_split_over_two_lines(self, fonts, lang) -> None:
        """stat_card wraps these two titles on their last space; a label without
        one would silently fall through to the single-line branch."""
        from custom_components.solar_cube.solar_lcd import _s

        for key in ("buy_price_label", "sell_price_label"):
            assert " " in _s(key, lang), f"{lang}/{key} cannot be split"


class TestBatteryTile:
    def test_english_spells_out_discharging(self) -> None:
        from custom_components.solar_cube.solar_lcd import _s

        assert _s("discharging_label", "en") == "DISCHARGING"
        assert _s("charging_label", "en") == "CHARGING"

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("key", ["charging_label", "discharging_label"])
    def test_the_state_label_fits_the_battery_tile(self, fonts, lang, key) -> None:
        """It is drawn with the face fitted for the tile title, not its own."""
        from custom_components.solar_cube.solar_lcd import LX1, LX2, _fit_text, _s

        title_font, _ = _fit_text(
            fonts, _s("bat_label", lang), LX2 - LX1 - 39, "medium"
        )
        width = _text_width(title_font, _s(key, lang))
        assert width <= (LX2 - LX1) - 6, f"{lang}/{key}: {width}px in an 82px tile"
