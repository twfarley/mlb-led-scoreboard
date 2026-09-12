"""Guards the no-hitter banner's placement on the 128x64 pitcher row.

`nohitter` sits between the ERA and the pitch count. Both of its neighbours are
pinned, and one of them moves:

  left   The ERA's start is derived from atbat.batter_stats, which
         __batter_stat_positions measures back from the right edge of the panel.
         A batter with short stats (.000 / 0 HR / 0 RBI) pushes it rightwards, and
         a five-character ERA then extends it further still.
  right  pitch_count cannot move past x=111, because a three-digit count
         ("123P", routine late in a game) needs 16px and would overflow x=127.

x=95 is the leftmost placement that cannot collide with the worst case. That is
tight -- 1px -- and deliberately accepted: the bad case needs a .000/0/0 batter,
a five-character ERA and an active no-hitter at the same time. This test exists so
that widening the ERA, the stats, or the fonts fails here rather than on the panel.
"""

import io
import unittest
from unittest import mock

from PIL import Image

import data.game
import layout_preview

SIZE = "w128h64"
BACKGROUND = (7, 14, 25)

# The pitcher row's baseline is atbat.pitcher.y = 25; a 4x6 cell covers y 20..25.
ROW = range(20, 26)
BANNER_X0 = 95  # nohitter.x
GUARD_COLUMN = BANNER_X0 - 1  # must stay clear for the banner to read as separate


def render_pitcher_row(avg, home_runs, rbi, era):
    stats = {"avg": avg, "homeRuns": home_runs, "rbi": rbi}
    with mock.patch.object(data.game.Game, "batter_stat", lambda self, key: stats[key]), mock.patch.object(
        data.game.Game, "pitcher_era", lambda self: era
    ):
        layout_preview._layout_pool.clear()
        png = layout_preview.render(SIZE, "live_nohitter")
    layout_preview._layout_pool.clear()
    return Image.open(io.BytesIO(png)).convert("RGB")


def lit_columns(image, rows=ROW):
    pixels = image.load()
    return {x for x in range(image.width) for y in rows if pixels[x, y] != BACKGROUND}


class TestNoHitterBannerPlacement(unittest.TestCase):
    def test_banner_is_drawn_on_the_pitcher_row(self):
        image = render_pitcher_row(".327", "39", "85", "3.73")
        columns = lit_columns(image)
        self.assertTrue(columns & set(range(BANNER_X0, BANNER_X0 + 12)), "the banner should occupy its slot")

    def test_worst_case_stats_do_not_reach_the_banner(self):
        """A .000/0/0 batter with a five-character ERA is the widest the left
        neighbour can get. It must still stop short of the banner."""
        image = render_pitcher_row(".000", "0", "0", "10.50")
        pixels = image.load()
        for y in ROW:
            self.assertEqual(
                pixels[GUARD_COLUMN, y],
                BACKGROUND,
                f"x={GUARD_COLUMN} must stay clear so the banner reads separately. Either the ERA/stats "
                f"widened, or nohitter.x moved left of {BANNER_X0}.",
            )

    def test_the_pitch_count_stays_clear_of_the_banner(self):
        image = render_pitcher_row(".327", "39", "85", "3.73")
        columns = sorted(lit_columns(image))
        after_banner = [x for x in columns if x >= BANNER_X0 + 12]
        self.assertTrue(after_banner, "expected the pitch count to the right of the banner")
        self.assertGreater(min(after_banner), BANNER_X0 + 12 - 1, "the pitch count must not touch the banner")

    def test_a_three_digit_pitch_count_still_fits_the_panel(self):
        """The reason pitch_count cannot move right to make room."""
        coords = layout_preview._coords_for(SIZE)
        x = coords["atbat"]["pitch_count"]["x"]
        font_width = 4  # atbat.pitch_count font_name is 4x6
        self.assertLessEqual(x + len("123P") * font_width - 1, 127)


if __name__ == "__main__":
    unittest.main()
