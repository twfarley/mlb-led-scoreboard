"""Guards the 128x64 ABS challenge indicators.

Automatic ball-strike challenges arrived in 2025, and every canned preview fixture
is an older game, so `abs_challenges_remaining()` returns None for all of them and
the squares never draw. That means neither the editor preview nor the layout
monitor exercises these unless a real game happens to be in progress -- they were
moved from the panel's right edge to the team colour edge without any check
noticing either position. Hence mocked data and explicit assertions here.

They sit at x 53..54: immediately right of the team colour block, which spans
x 0..52 (a `width: 52` rect drawn from x=0 covers x..x+width inclusive). Inside
the block is not available -- `teams.line_score` is right-anchored with its
rightmost pixel at x=51, so a single 9x18B digit already occupies x 43..51.
"""

import io
import unittest
from unittest import mock

from PIL import Image

import data.game
import layout_preview

SIZE = "w128h64"
BACKGROUND = (7, 14, 25)

TEAM_BLOCK_RIGHT_EDGE = 52
SQUARE_X0 = 53
SQUARE_SIZE = 2

# The challenge rows also cross the bases diamonds (2B spans x 79..91), so
# assertions look only at the narrow strip beside the colour block.
STRIP = range(TEAM_BLOCK_RIGHT_EDGE + 1, 58)


def render_with_challenges(away, home):
    with mock.patch.object(
        data.game.Game, "abs_challenges_remaining", lambda self, side: away if side == "away" else home
    ):
        layout_preview._layout_pool.clear()
        png = layout_preview.render(SIZE, "live")
    layout_preview._layout_pool.clear()
    return Image.open(io.BytesIO(png)).convert("RGB")


def lit_columns(image, rows):
    pixels = image.load()
    return {x for x in range(image.width) for y in rows if pixels[x, y] != BACKGROUND}


class TestAbsChallengeIndicators(unittest.TestCase):
    def setUp(self):
        self.coords = layout_preview._coords_for(SIZE)["teams"]["abs_challenges"]

    def test_configured_beside_the_team_block_not_the_panel_edge(self):
        for side in ("away", "home"):
            self.assertEqual(self.coords[side]["x"], SQUARE_X0, f"{side} should sit against the colour block")
            self.assertEqual(self.coords[side]["size"], SQUARE_SIZE)

    def test_squares_do_not_reach_the_line_score(self):
        """The score is right-anchored at x=51, so anything at or below it collides."""
        for side in ("away", "home"):
            self.assertGreater(self.coords[side]["x"], TEAM_BLOCK_RIGHT_EDGE - 1)

    def test_squares_are_two_pixels_wide_and_drawn_where_configured(self):
        image = render_with_challenges(2, 2)
        # __draw_challenge_square fills x .. x + size - 1.
        expected = set(range(SQUARE_X0, SQUARE_X0 + SQUARE_SIZE))
        for side in ("away", "home"):
            height = self.coords[side].get("height", SQUARE_SIZE)
            for top in self.coords[side]["squares"]:
                band = range(top, top + height)
                drawn = {x for x in lit_columns(image, band) if x in STRIP}
                self.assertEqual(drawn, expected, f"{side} square at y={top} should occupy exactly {expected}")

    def test_nothing_is_drawn_at_the_old_right_edge_position(self):
        """They used to sit at x 125..127.

        Checked on the away bars only. The home bars now run down to row 63 and
        cross the outs squares (y 46..50, x 105..127), and a pixel test cannot tell
        an outs pixel from a challenge pixel -- an earlier version of this assertion
        failed for exactly that reason once the marks became bars.
        """
        image = render_with_challenges(2, 2)
        for top in self.coords["away"]["squares"]:
            band = range(top, top + self.coords["away"].get("height", SQUARE_SIZE))
            self.assertFalse(
                {x for x in lit_columns(image, band) if x >= 125},
                f"something is still drawn near x=125 on the challenge row y={top}",
            )

    def test_a_spent_challenge_dims_rather_than_disappearing(self):
        """Both squares are always drawn; only the colour changes, so the count of
        lit pixels must not depend on how many challenges remain."""
        full = render_with_challenges(2, 2)
        spent = render_with_challenges(0, 0)
        rows = self.coords["away"]["squares"]
        band = range(rows[0], rows[0] + self.coords["away"].get("height", SQUARE_SIZE))
        self.assertEqual(
            {x for x in lit_columns(full, band) if x in STRIP},
            {x for x in lit_columns(spent, band) if x in STRIP},
        )


if __name__ == "__main__":
    unittest.main()
