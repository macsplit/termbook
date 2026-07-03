"""Unit tests for termbook.colors' pure RGB/palette logic (Phase 4.4 backfill).

get_color_pair_with_reversal() only touches curses (curses.init_pair) when
state.COLORSUPPORT is True, so these tests pin it False to exercise the
color-index/palette math without needing a live curses screen.
"""

import pytest

from termbook import state
from termbook.colors import (
    get_ui_color_pair,
    init_syntax_color_pairs,
    init_smart_color_palette,
    find_closest_palette_color,
    rgb_to_color_index,
    get_color_pair_with_reversal,
    get_color_pair,
    get_syntax_color_pair,
)


@pytest.fixture(autouse=True)
def no_color_support():
    """Ensure these tests never depend on a live curses screen."""
    original = state.COLORSUPPORT
    state.COLORSUPPORT = False
    yield
    state.COLORSUPPORT = original


class TestRgbToColorIndex:
    def test_black_maps_to_0(self):
        assert rgb_to_color_index(0, 0, 0) == 0

    def test_white_maps_to_15(self):
        assert rgb_to_color_index(255, 255, 255) == 15

    def test_mid_gray_maps_to_grayscale_range(self):
        assert 232 <= rgb_to_color_index(128, 128, 128) <= 255

    def test_saturated_red_maps_to_color_cube(self):
        assert rgb_to_color_index(255, 0, 0) >= 16

    def test_out_of_range_values_are_clamped_not_raised(self):
        # Should clamp rather than raise, per the max(0, min(255, ...)) guards
        assert rgb_to_color_index(300, -20, 999) == rgb_to_color_index(255, 0, 255)

    def test_non_numeric_input_falls_back_to_default_white(self):
        assert rgb_to_color_index("x", None, object()) == 7


class TestFindClosestPaletteColor:
    def test_exact_black_returns_black(self):
        init_smart_color_palette()
        assert find_closest_palette_color((0, 0, 0)) == (0, 0, 0)

    def test_close_color_snaps_to_palette_entry(self):
        init_smart_color_palette()
        result = find_closest_palette_color((1, 1, 1))
        assert result != (1, 1, 1)  # snapped to a palette entry, not passed through


class TestColorPairWithoutColorSupport:
    def test_get_color_pair_with_reversal_returns_default(self):
        assert get_color_pair_with_reversal((255, 0, 0), (0, 0, 0)) == (0, False)

    def test_get_color_pair_returns_zero(self):
        assert get_color_pair((255, 0, 0), (0, 0, 0)) == 0

    def test_get_syntax_color_pair_returns_zero(self):
        assert get_syntax_color_pair((255, 0, 0)) == 0


def test_get_ui_color_pair_returns_default_pair():
    assert get_ui_color_pair() == 1


def test_init_syntax_color_pairs_is_a_noop_without_curses():
    # Should not raise even without an initialized curses screen.
    init_syntax_color_pairs()
