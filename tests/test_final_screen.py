"""Guards the 128x64 FINAL screen.

Two things live here that the rest of the board does not have to think about.

`FINAL` and the W/L/SV/blurb scroll sit in the band above the team banner. They
used to sit beside and below it, which is how the "F" of FINAL ended up hidden
behind the home score -- the banner draws last, so anything in its rows loses.

Season records sit to the right of the final score, drawn by the postgame renderer
rather than by the team banner's own `teams.record`. The banner draws on every
screen, and on this layout the live screen already fills the space right of the
team block with the diamond, the count and the outs; records only fit once the
game is over. Keeping them out of the banner means nothing else has to become
screen-aware.

The record MLB reports in `gameData.teams.<side>.record` for a completed game
already includes that game. Checked on 2026-09-13 against a game that had just
gone final: MIL 93-57 and CIN 70-79 in the game feed, identical to the standings.
So there is nothing to wait for -- the worry that records would lag a game behind
does not hold.
"""

import io
import unittest

from PIL import Image

import layout_preview

SIZE = "w128h64"
BACKGROUND = (7, 14, 25)

# The team banner owns y 28..63 on this layout; everything above it is free.
BANNER_TOP = 28
TEAM_BLOCK_RIGHT_EDGE = 52


def render(screen="final"):
    layout_preview._layout_pool.clear()
    png = layout_preview.render(SIZE, screen, text_pos=0)
    layout_preview._layout_pool.clear()
    return Image.open(io.BytesIO(png)).convert("RGB")


def coords():
    return layout_preview._coords_for(SIZE)["final"]


def font_width(keypath):
    layout = layout_preview._layout_for(layout_preview._coords_for(SIZE), 128, 64)
    return layout.font(keypath)["size"]["width"]


def lit_row_groups(image, rows):
    """Contiguous runs of rows containing anything but background.

    Two text lines in a 28px band read as two groups. One group means they have
    merged into each other, which is the failure a y-coordinate comparison cannot
    see without knowing each BDF font's ascent.
    """
    pixels = image.load()
    lit = [any(pixels[x, y] != BACKGROUND for x in range(image.width)) for y in rows]
    groups = []
    for y, on in zip(rows, lit):
        if on and (not groups or groups[-1][-1] != y - 1):
            groups.append([y])
        elif on:
            groups[-1].append(y)
    return groups


class TestFinalScreenLayout(unittest.TestCase):
    def test_final_and_the_decision_scroll_are_both_above_the_banner(self):
        """Anything in the banner's rows is painted over by it."""
        for key in ("inning", "scrolling_text"):
            self.assertLess(
                coords()[key]["y"],
                BANNER_TOP,
                f"final.{key} is in the team banner's rows, which are drawn over it",
            )

    def test_the_two_lines_stay_visually_separate(self):
        for screen in ("final", "final_nohitter"):
            groups = lit_row_groups(render(screen), range(0, BANNER_TOP))
            self.assertEqual(
                len(groups),
                2,
                f"{screen}: expected FINAL and the scroll as two separate lines, got rows {groups}",
            )

    def test_the_scroll_starts_at_the_left_edge(self):
        image = render()
        pixels = image.load()
        row = coords()["scrolling_text"]["y"]
        lit = [x for x in range(128) for y in range(row - 6, row + 1) if pixels[x, y] != BACKGROUND]
        self.assertTrue(lit, "the decision scroll should be visible at scroll offset 0")
        self.assertLessEqual(min(lit), 2, "it should begin at its configured x=0")


class TestFinalRecords(unittest.TestCase):
    def test_records_are_enabled_only_on_this_layout(self):
        """`final.record` is absent everywhere else, and the renderer treats a
        missing key as off, so no other board size changes."""
        self.assertTrue(coords()["record"]["enabled"])
        for size in ("w128h32", "w64h32", "w64h64", "w32h32", "w192h64"):
            self.assertNotIn("record", layout_preview._coords_for(size)["final"], size)

    def test_they_sit_right_of_the_score_not_over_it(self):
        """The line score is right-anchored with its rightmost pixel at x=51,
        inside a colour block that runs to x=52."""
        for side in ("away", "home"):
            self.assertGreater(coords()["record"][side]["x"], TEAM_BLOCK_RIGHT_EDGE, side)

    def test_each_record_is_drawn_on_its_own_team_row(self):
        self.assertIn(coords()["record"]["away"]["y"], range(BANNER_TOP, 46))
        self.assertIn(coords()["record"]["home"]["y"], range(46, 64))

    def test_both_records_actually_render(self):
        image = render()
        pixels = image.load()
        for side in ("away", "home"):
            x0, y = coords()["record"][side]["x"], coords()["record"][side]["y"]
            lit = [x for x in range(x0, 128) for row in range(y - 5, y + 1) if pixels[x, row] != BACKGROUND]
            self.assertTrue(lit, f"the {side} record should be drawn")
            self.assertLess(min(lit) - x0, 2, f"the {side} record should start at x={x0}")

    def test_the_nohit_banner_does_not_land_on_a_record(self):
        """It used to sit at (55, 61), which is where the home record now is."""
        nohit, home = coords()["nohit_text"], coords()["record"]["home"]
        self.assertLess(nohit["y"], BANNER_TOP, "the no-hit banner belongs in the free band with FINAL")
        self.assertNotEqual((nohit["x"], nohit["y"]), (home["x"], home["y"]))

    def test_the_nohit_banner_clears_final_on_the_line_they_share(self):
        """`FINAL 14` is the widest that text gets, and final.inning is centred."""
        widest = len("FINAL 14") * font_width("final.inning")
        right_edge = coords()["inning"]["x"] + widest // 2
        self.assertGreater(coords()["nohit_text"]["x"], right_edge, "N.H would touch the inning number")


if __name__ == "__main__":
    unittest.main()
