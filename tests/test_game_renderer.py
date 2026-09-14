"""Tests for the optional additions to the live-game renderer.

Everything here is switched off by a layout that does not mention it, which is the
whole contract: an existing coordinates file must render exactly as it did. So each
test comes in a pair -- absent key, then enabled key -- and the absent case asserts
the *old* output is still produced, not merely that the new output is missing.

`graphics` is replaced with a recorder rather than a canvas being inspected. What
matters is which strings get drawn where, and reading that off pixels would mean
reimplementing the font renderer to assert anything.
"""

import unittest
from unittest import mock

import data.config.color
from data.config.color import Color
from data.config.layout import Layout
from data.scoreboard.inning import Inning
from renderers.games import game as gamerender

# These are module-private (`__name`), which is not name mangling at module level --
# but it is inside a class body, so the aliases have to be bound out here.
render_batter_text = gamerender.__dict__["__render_batter_text"]
render_pitcher_text = gamerender.__dict__["__render_pitcher_text"]
render_play_description = gamerender.__dict__["__render_play_description"]
render_inning_half = gamerender.__dict__["__render_inning_half"]
batter_stat_positions = gamerender.__dict__["__batter_stat_positions"]
due_up_line = gamerender.__dict__["__due_up_line"]

WIDTH = 128
HEIGHT = 64

# Only the keys the code under test reaches for. A real layout has far more, and
# using one here would couple these tests to a shipped board size.
BASE_COORDS = {
    "defaults": {"font_name": "4x6"},
    "atbat": {
        "batter": {"x": 1, "y": 10, "width": 60},
        "pitcher": {"x": 1, "y": 25, "width": 55},
        "pitch_count": {"x": 110, "y": 25, "enabled": False, "append_pitcher_name": False},
    },
    "inning": {
        "number": {"x": 65, "y": 43},
        "arrow": {"size": 3, "up": {"x_offset": -5, "y_offset": -4}, "down": {"x_offset": -5, "y_offset": -2}},
        "break": {
            "number": {"x": 3, "y": 58},
            "text": {"x": 3, "y": 44},
        },
    },
}

COLORS = {
    "default": {"background": {"r": 0, "g": 0, "b": 0}},
    "atbat": {
        "batter": {"r": 1, "g": 1, "b": 1},
        "pitcher": {"r": 2, "g": 2, "b": 2},
        "play_result": {"r": 3, "g": 3, "b": 3},
        "batter_stats": {"r": 4, "g": 4, "b": 4},
        "batter_stats_label": {"r": 5, "g": 5, "b": 5},
    },
    "inning": {
        "number": {"r": 6, "g": 6, "b": 6},
        "arrow": {
            "up": {"r": 7, "g": 7, "b": 7},
            "down": {"r": 8, "g": 8, "b": 8},
            "active": {"r": 9, "g": 9, "b": 9},
            "inactive": {"r": 10, "g": 10, "b": 10},
        },
        "break": {
            "text": {"r": 11, "g": 11, "b": 11},
            "due_up_names": {"r": 12, "g": 12, "b": 12},
            "inactive": {"r": 13, "g": 13, "b": 13},
        },
    },
}


def deep_merge(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def make_layout(**extra):
    return Layout(deep_merge(BASE_COORDS, extra), WIDTH, HEIGHT)


def make_colors():
    return Color(COLORS)


class Recorder:
    """Stands in for driver.graphics and records what was asked for."""

    def __init__(self):
        self.texts = []
        self.lines = []

    def DrawText(self, canvas, font, x, y, color, text):
        self.texts.append({"x": x, "y": y, "text": text, "color": color})
        return len(text)

    def DrawLine(self, canvas, x0, y0, x1, y1, color):
        self.lines.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color})

    def Color(self, r, g, b):
        return (r, g, b)

    def __getattr__(self, name):  # pragma: no cover - anything else is a real bug
        raise AttributeError(name)


class Stub:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_atbat(**overrides):
    values = {
        "batter": "Yelich",
        "onDeck": "Hiura",
        "inHole": "Moustakas",
        "pitcher": "Sanchez, A",
        "batting_order": 3,
        "onDeck_order": 4,
        "inHole_order": 5,
        "avg": ".327",
        "home_runs": 39,
        "rbi": 85,
        "pitcher_era": "3.73",
    }
    return Stub(**deep_merge(values, overrides))


def drawn(recorder):
    return [entry["text"] for entry in recorder.texts]


class RendererTestCase(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        patcher = mock.patch.object(gamerender, "graphics", self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        # bullpen's scrolling_text draws through its own graphics reference, so the
        # batter/pitcher names are not in the recorder. They are not what is under
        # test here; the labels and stat columns beside them are.
        scroll = mock.patch.object(gamerender, "scrolling_text", lambda *a, **k: 0)
        scroll.start()
        self.addCleanup(scroll.stop)
        # Color.graphics_color() builds its colour through its own graphics import,
        # so it needs the recorder too for colours to come back comparable.
        colors = mock.patch.object(data.config.color, "graphics", self.recorder)
        colors.start()
        self.addCleanup(colors.stop)


class TestBatterRow(RendererTestCase):
    def test_a_layout_without_the_new_keys_still_draws_the_ab_label(self):
        render_batter_text(None, make_layout(), make_colors(), make_atbat(), 0)
        self.assertEqual(drawn(self.recorder), ["AB:"])

    def test_the_batting_order_replaces_the_ab_label(self):
        """ "AB:" says nothing a number in the same place does not, and the row is
        the one place on the board where every pixel is a character of a name."""
        layout = make_layout(atbat={"batter_order": {"x": 1, "y": 10, "enabled": True}})
        render_batter_text(None, layout, make_colors(), make_atbat(), 0)
        self.assertEqual(drawn(self.recorder), ["3."])

    def test_a_batter_with_no_batting_order_keeps_the_label(self):
        """Pitchers in a DH game, and anyone before the lineup is posted."""
        layout = make_layout(atbat={"batter_order": {"x": 1, "y": 10, "enabled": True}})
        render_batter_text(None, layout, make_colors(), make_atbat(batting_order=None), 0)
        self.assertEqual(drawn(self.recorder), ["AB:"])

    def test_disabled_is_the_same_as_absent(self):
        layout = make_layout(atbat={"batter_order": {"x": 1, "y": 10, "enabled": False}})
        render_batter_text(None, layout, make_colors(), make_atbat(), 0)
        self.assertEqual(drawn(self.recorder), ["AB:"])

    def test_stats_are_drawn_as_value_label_pairs(self):
        layout = make_layout(atbat={"batter_stats": {"font_name": "4x6", "y": 10, "enabled": True}})
        render_batter_text(None, layout, make_colors(), make_atbat(), 0)
        self.assertEqual(drawn(self.recorder), [".327", "AVG", "39", "HR", "85", "RBI", "AB:"])

    def test_a_missing_stat_is_skipped_rather_than_drawn_empty(self):
        layout = make_layout(atbat={"batter_stats": {"font_name": "4x6", "y": 10, "enabled": True}})
        render_batter_text(None, layout, make_colors(), make_atbat(home_runs=None), 0)
        self.assertNotIn("HR", drawn(self.recorder))
        self.assertIn("AVG", drawn(self.recorder))


class TestStatColumnGeometry(unittest.TestCase):
    """The stat column is measured back from the right edge of the panel.

    Its width depends on live data -- ".327 39HR 85RBI" is wider than ".000 0HR
    0RBI" -- so it cannot be a fixed coordinate without either overflowing the panel
    or leaving a gap in the batter's name.
    """

    def positions(self, **overrides):
        layout = make_layout(atbat={"batter_stats": {"font_name": "4x6", "y": 10, "enabled": True}})
        return batter_stat_positions(layout, make_atbat(**overrides))

    def test_everything_fits_inside_the_panel(self):
        pos = self.positions()
        self.assertGreaterEqual(pos["leftmost_x"], 0)
        # RBI is the rightmost label; its 3 characters must end on the panel.
        self.assertLessEqual(pos["rbi_lbl_x"] + 3 * 4 - 1, WIDTH - 1)

    def test_the_columns_run_right_to_left_in_order(self):
        pos = self.positions()
        self.assertLess(pos["avg_val_x"], pos["avg_lbl_x"])
        self.assertLess(pos["avg_lbl_x"], pos["hr_val_x"])
        self.assertLess(pos["hr_val_x"], pos["hr_lbl_x"])
        self.assertLess(pos["hr_lbl_x"], pos["rbi_val_x"])
        self.assertLess(pos["rbi_val_x"], pos["rbi_lbl_x"])

    def test_shorter_numbers_free_up_space_on_the_left(self):
        wide = self.positions(home_runs=39, rbi=85)
        narrow = self.positions(home_runs=0, rbi=0)
        self.assertGreater(narrow["leftmost_x"], wide["leftmost_x"])


class TestPitcherRow(RendererTestCase):
    def test_a_layout_without_stats_still_draws_the_p_label(self):
        render_pitcher_text(None, make_layout(), make_colors(), make_atbat(), Stub(pitch_count=0), 0)
        self.assertEqual(drawn(self.recorder), ["P:"])

    def test_the_era_replaces_the_p_label_and_aligns_with_the_stats(self):
        layout = make_layout(atbat={"batter_stats": {"font_name": "4x6", "y": 10, "enabled": True, "show_era": True}})
        render_pitcher_text(None, layout, make_colors(), make_atbat(), Stub(pitch_count=0), 0)
        self.assertEqual(drawn(self.recorder), ["3.73", "ERA"])
        era_x = self.recorder.texts[0]["x"]
        positions = batter_stat_positions(layout, make_atbat())
        self.assertEqual(era_x, positions["leftmost_x"], "the ERA should start where the batter's stats do")

    def test_show_era_off_leaves_the_row_alone(self):
        layout = make_layout(atbat={"batter_stats": {"font_name": "4x6", "y": 10, "enabled": True, "show_era": False}})
        render_pitcher_text(None, layout, make_colors(), make_atbat(), Stub(pitch_count=0), 0)
        self.assertEqual(drawn(self.recorder), ["P:"])

    def test_a_pitcher_with_no_era_keeps_the_label(self):
        layout = make_layout(atbat={"batter_stats": {"font_name": "4x6", "y": 10, "enabled": True, "show_era": True}})
        render_pitcher_text(None, layout, make_colors(), make_atbat(pitcher_era=None), Stub(pitch_count=0), 0)
        self.assertEqual(drawn(self.recorder), ["P:"])


class TestPlayDescriptionLine(RendererTestCase):
    def render(self, description, **coords):
        layout = make_layout(atbat={"play_description": coords} if coords else {})
        return render_play_description(None, layout, make_colors(), description)

    def setUp(self):
        super().setUp()
        # Module-level scroll state, so it has to be rewound between tests.
        gamerender._play_desc_pos = None
        gamerender._play_desc_last = None
        gamerender._play_desc_finished = True

    def test_absent_from_the_layout_draws_nothing(self):
        self.assertEqual(self.render("Ohtani homers."), 0)
        self.assertEqual(drawn(self.recorder), [])

    def test_text_that_fits_is_drawn_in_place(self):
        """Scrolling something already visible only makes it harder to read, and
        most descriptions are short: "Mound visit.", "Wild pitch.", "Strikeout."."""
        held = self.render("Mound visit.", **{"font_name": "4x6", "x": 55, "y": 61, "width": 73, "enabled": True})
        self.assertEqual(drawn(self.recorder), ["Mound visit."])
        self.assertEqual(self.recorder.texts[0]["x"], 55)
        self.assertEqual(held, 0, "a static line must not hold the rotation")

    def test_text_too_wide_to_fit_scrolls_and_holds_the_rotation(self):
        long_text = "Yelich homers (30) on a fly ball to right center field."
        held = self.render(long_text, **{"font_name": "4x6", "x": 55, "y": 61, "width": 73, "enabled": True})
        self.assertGreater(held, 0, "a scrolling line should hold the rotation until it has been read")

    def test_an_empty_description_clears_the_scroll_state(self):
        coords = {"font_name": "4x6", "x": 55, "y": 61, "width": 73, "enabled": True}
        self.render("Yelich homers (30) on a fly ball to right center field.", **coords)
        self.render("", **coords)
        self.assertIsNone(gamerender._play_desc_pos)


class TestDueUpLine(unittest.TestCase):
    def line(self, **overrides):
        return due_up_line(make_atbat(**overrides))

    def test_each_name_carries_its_spot_in_the_order(self):
        self.assertEqual(self.line(), "Due Up: 3. Yelich, 4. Hiura, 5. Moustakas")

    def test_a_missing_order_still_renders_the_name(self):
        self.assertEqual(self.line(onDeck_order=None), "Due Up: 3. Yelich, Hiura, 5. Moustakas")

    def test_missing_names_are_dropped_rather_than_leaving_gaps(self):
        self.assertEqual(self.line(onDeck="", inHole=None), "Due Up: 3. Yelich")

    def test_no_batters_at_all_is_empty(self):
        self.assertEqual(self.line(batter=None, onDeck=None, inHole=None), "")


class TestInningArrow(RendererTestCase):
    """Two placements, chosen by which keys the layout provides."""

    def render(self, state, **arrow):
        layout = make_layout(inning={"arrow": arrow} if arrow else {})
        inning = Stub(number=7, state=state, ordinal="7th")
        render_inning_half(None, layout, make_colors(), inning)
        return self.recorder.lines

    def test_offsets_draw_one_arrow_in_the_original_colours(self):
        lines = self.render(Inning.TOP)
        self.assertEqual({line["color"] for line in lines}, {(7, 7, 7)}, "top of the inning uses inning.arrow.up")

    def test_offsets_switch_colour_key_with_the_half(self):
        lines = self.render(Inning.BOTTOM)
        self.assertEqual({line["color"] for line in lines}, {(8, 8, 8)})

    def test_absolute_coordinates_draw_both_arrows(self):
        arrow = {"size": 3, "up": {"x": 62, "y": 29}, "down": {"x": 62, "y": 51}}
        lines = self.render(Inning.TOP, **arrow)
        self.assertEqual({line["color"] for line in lines}, {(9, 9, 9), (10, 10, 10)})
        self.assertEqual(len(lines), 6, "three rows per arrow, both arrows")

    def test_the_active_half_is_the_bright_one(self):
        arrow = {"size": 3, "up": {"x": 62, "y": 29}, "down": {"x": 62, "y": 51}}
        lines = self.render(Inning.BOTTOM, **arrow)
        bright = {line["y0"] for line in lines if line["color"] == (9, 9, 9)}
        self.assertTrue(all(y > 40 for y in bright), "the bottom half should light the lower arrow")

    def test_the_arrows_widen_away_from_their_tips(self):
        arrow = {"size": 3, "up": {"x": 62, "y": 29}, "down": {"x": 62, "y": 51}}
        lines = self.render(Inning.TOP, **arrow)
        up_rows = sorted(line["y0"] for line in lines if line["y0"] < 40)
        self.assertEqual(up_rows, [29, 30, 31], "the up arrow's tip is its top row")
        widths = sorted(line["x1"] - line["x0"] for line in lines if line["y0"] < 40)
        self.assertEqual(widths, [0, 2, 4])

    def test_the_upcoming_half_blinks_through_a_break(self):
        """Which is the point of drawing both: during a break neither half is being
        played, so a single arrow has nothing to point at."""
        arrow = {"size": 3, "up": {"x": 62, "y": 29}, "down": {"x": 62, "y": 51}}
        seen = set()
        for now in (100, 101):
            self.recorder.lines.clear()
            with mock.patch.object(gamerender.time, "time", lambda: now):
                lines = self.render(Inning.END, **arrow)
            seen.add(frozenset(line["color"] for line in lines))
        self.assertEqual(len(seen), 2, "the arrows should look different on alternate seconds")


FIELD_COORDS = {
    "bases": {
        "1B": {"x": 88, "y": 9, "size": 12},
        "2B": {"x": 80, "y": 1, "size": 12},
        "3B": {"x": 72, "y": 9, "size": 12},
    },
    "outs": {
        "1": {"x": 105, "y": 46, "size": 2},
        "2": {"x": 112, "y": 46, "size": 2},
        "3": {"x": 119, "y": 46, "size": 2},
    },
}

STACKED_ARROWS = {"size": 3, "up": {"x": 62, "y": 29}, "down": {"x": 62, "y": 51}}


def make_scoreboard(state):
    return Stub(
        inning=Stub(number=7, state=state, ordinal="7th"),
        atbat=make_atbat(),
        bases=Stub(runners=[False, False, False]),
        outs=Stub(number=1),
    )


class TestBreakScreen(RendererTestCase):
    """The break screen has two forms, and the stock one must be untouched."""

    def setUp(self):
        super().setUp()
        # The due-up display is the same on both forms and has its own tests.
        due_up = mock.patch.object(gamerender, "_render_due_up", lambda *a: 0)
        due_up.start()
        self.addCleanup(due_up.stop)

    def layout(self, **inning):
        values = deep_merge(deep_merge(BASE_COORDS, FIELD_COORDS), {"inning": inning})
        return Layout(values, WIDTH, HEIGHT)

    def field_layout(self):
        return self.layout(**{"break": {"show_field": True}, "arrow": STACKED_ARROWS})

    def render(self, layout, state):
        gamerender.render_live_game(None, layout, make_colors(), make_scoreboard(state), 0, 0)

    def test_by_default_it_draws_the_mid_inning_text_and_nothing_else(self):
        self.render(self.layout(), Inning.MIDDLE)
        self.assertEqual(drawn(self.recorder), ["Mid", "7th"])
        self.assertEqual(self.recorder.lines, [], "the stock break screen draws no diamond")

    def test_end_of_an_inning_says_end_rather_than_mid(self):
        self.render(self.layout(), Inning.END)
        self.assertEqual(drawn(self.recorder), ["End", "7th"])

    def test_show_field_swaps_the_text_for_the_diamond_and_the_indicator(self):
        self.render(self.field_layout(), Inning.MIDDLE)
        self.assertEqual(drawn(self.recorder), ["7"], "the inning number should be the only text")
        self.assertTrue(self.recorder.lines, "the diamond and out markers should be drawn")

    def test_the_field_is_drawn_in_the_dim_colour_not_the_live_ones(self):
        """Between halves of an inning there are no runners and no outs to report,
        so lit markers would state something false."""
        self.render(self.field_layout(), Inning.MIDDLE)
        # Rows 29..51 are the two arrows, which keep their own bright/dim scale.
        field = [line for line in self.recorder.lines if not 29 <= line["y0"] <= 51]
        self.assertTrue(field)
        self.assertEqual({line["color"] for line in field}, {(13, 13, 13)})


if __name__ == "__main__":
    unittest.main()
