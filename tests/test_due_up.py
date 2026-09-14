"""Guards the mid-inning break screen on 128x64.

The stock break screen stacks three 7x13 names down the right-hand column with a
"Due / Up:" label beside them. That block assumes the teams live at the top of the
board; on this layout they own y 28..63, so there is nowhere for it to go. The
128x64 layout instead sets `inning.break.due_up.scroll`, which collapses the whole
thing into one scrolling line across the free top band.

Two things are easy to break and neither is visible to the layout monitor -- the
line scrolls, so at the monitor's fixed scroll phase it sits off-canvas:

  * the stacked rows must not also draw (they would overlap the team banner);
  * bases and outs must stay on screen but dimmed. Between halves of an inning
    there are no runners and no outs to report, so drawing them lit would state
    something false, while dropping them makes the board visibly lose furniture.
"""

import io
import unittest

from PIL import Image

import layout_preview
from renderers.games.game import __due_up_line as due_up_line

SIZE = "w128h64"
BACKGROUND = (7, 14, 25)

# colors/scoreboard.schema.json: inning.break.inactive, shared with inning.arrow.inactive.
INACTIVE = (100, 90, 22)

# inning.break.due_up.leadoff/on_deck/in_hole -- the stacked form's rows, whose
# 7x13 cells run from y 30 down. All of that is team banner on this layout.
STACKED_ROWS = range(30, 64)
STACKED_COLUMNS = range(70, 128)


class Stub:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def atbat(batter="Yelich", on_deck="Hiura", in_hole="Moustakas", orders=(3, 4, 5)):
    return Stub(
        batter=batter,
        onDeck=on_deck,
        inHole=in_hole,
        batting_order=orders[0],
        onDeck_order=orders[1],
        inHole_order=orders[2],
    )


class TestDueUpLine(unittest.TestCase):
    def test_each_name_carries_its_spot_in_the_order(self):
        """The spot is the point: it is how you tell that the top of the lineup
        is coming up, which a bare list of three names does not convey."""
        self.assertEqual(due_up_line(atbat()), "Due Up: 3. Yelich, 4. Hiura, 5. Moustakas")

    def test_a_missing_order_still_renders_the_name(self):
        line = due_up_line(atbat(orders=(3, None, 5)))
        self.assertEqual(line, "Due Up: 3. Yelich, Hiura, 5. Moustakas")

    def test_missing_names_are_dropped_rather_than_leaving_gaps(self):
        line = due_up_line(atbat(on_deck="", in_hole=None))
        self.assertEqual(line, "Due Up: 3. Yelich")

    def test_no_batters_at_all_is_empty(self):
        self.assertEqual(due_up_line(atbat(batter=None, on_deck=None, in_hole=None)), "")


def render_break(text_pos, size=SIZE):
    layout_preview._layout_pool.clear()
    png = layout_preview.render(size, "live_break", text_pos=text_pos)
    layout_preview._layout_pool.clear()
    return Image.open(io.BytesIO(png)).convert("RGB")


class TestBreakScreen(unittest.TestCase):
    def test_the_scrolling_line_is_what_this_layout_uses(self):
        coords = layout_preview._coords_for(SIZE)["inning"]["break"]["due_up"]
        self.assertTrue(coords["scroll"]["enabled"], "the single-line form is the 128x64 break screen")

    def test_the_line_draws_in_the_free_band_above_the_teams(self):
        """Rendered at scroll offset 0 -- the default parks it at the canvas width,
        i.e. entirely off the right edge, which is why nothing here can rely on it."""
        image = render_break(text_pos=0)
        pixels = image.load()
        band = {y for y in range(0, 28) for x in range(0, 128) if pixels[x, y] != BACKGROUND}
        self.assertTrue(band, "the due-up line should occupy the band above the team banner")

    def test_the_stacked_form_does_not_also_draw(self):
        """`_render_due_up` returns early on the scroll config. If it ever stops
        doing so, three 7x13 names land on top of the team colours."""
        image = render_break(text_pos=0)
        pixels = image.load()
        drawn = {(x, y) for y in STACKED_ROWS for x in STACKED_COLUMNS if pixels[x, y] != BACKGROUND}
        # The bases diamond and the outs squares legitimately live in this region;
        # the names would be text, which is far denser. A 7x13 "Moustakas" alone is
        # ~200 lit pixels, and there are three rows of it.
        self.assertLess(len(drawn), 200, "something text-shaped is drawn where the stacked names used to be")

    def test_bases_and_outs_are_drawn_in_the_inactive_colour(self):
        image = render_break(text_pos=0)
        pixels = image.load()
        colors = {pixels[x, y] for y in range(28, 64) for x in range(60, 128)}
        self.assertIn(INACTIVE, colors, "bases/outs should still be on screen during a break")

    def test_keeping_bases_and_outs_is_opt_in_per_layout(self):
        """The dim colour is shared, so it cannot be the gate.

        Every other board size stacks the due-up names across the space the
        diamond occupies -- drawing both there put text on top of the diamond on
        four of the six sizes until this flag existed.
        """
        for size in ("w128h32", "w64h32", "w64h64", "w32h32", "w192h64"):
            coords = layout_preview._coords_for(size)["inning"]["break"]
            self.assertNotIn("show_bases_and_outs", coords, f"{size} stacks its due-up names over the diamond")
        self.assertTrue(layout_preview._coords_for(SIZE)["inning"]["break"]["show_bases_and_outs"])

    def test_a_layout_without_the_flag_draws_no_diamond_during_the_break(self):
        """Enabling the flag has to be what changes the render.

        A colour comparison alone will not do it: `inning.break.inactive` shares
        its value with `inning.arrow.inactive`, so the dim arrow is on screen
        either way. Compare the two renders instead.
        """
        import copy

        stock = layout_preview._coords_for("w128h32")
        flagged = copy.deepcopy(stock)
        flagged["inning"]["break"]["show_bases_and_outs"] = True

        layout_preview._layout_pool.clear()
        default = layout_preview.render("w128h32", "live_break", text_pos=0)
        layout_preview._layout_pool.clear()
        opted_in = layout_preview.render("w128h32", "live_break", coords=flagged, text_pos=0)
        layout_preview._layout_pool.clear()

        self.assertNotEqual(default, opted_in, "the flag should be what puts the diamond on the break screen")

        # w128h32's 2B diamond spans x 80..91, y 1..12 -- team-banner-free and
        # nowhere near the dim arrow.
        pixels = Image.open(io.BytesIO(default)).convert("RGB").load()
        self.assertNotIn(
            INACTIVE,
            {pixels[x, y] for y in range(1, 13) for x in range(80, 92)},
            "w128h32 should render its break screen exactly as it did before",
        )

    def test_the_inning_indicator_stays_bright(self):
        """The number and the blinking arrow are the one thing on the break screen
        that is still live information, so they are deliberately not dimmed."""
        image = render_break(text_pos=0)
        pixels = image.load()
        inning = layout_preview._coords_for(SIZE)["inning"]["number"]
        rows = range(inning["y"] - 6, inning["y"] + 1)
        lit = {pixels[x, y] for y in rows for x in range(inning["x"] - 6, inning["x"] + 1)}
        self.assertTrue(lit - {BACKGROUND, INACTIVE}, "the inning number should not be drawn in the inactive colour")


if __name__ == "__main__":
    unittest.main()
