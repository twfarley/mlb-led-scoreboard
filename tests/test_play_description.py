"""Tests for the play-by-play text line.

`Game.current_play_description()` feeds a single scrolling line with the most
informative thing available. It replaced a synthetic "Top 3 · 1-2 · 2 out" filler
that restated numbers already drawn elsewhere on the board.
"""

import io
import unittest
from unittest import mock

from PIL import Image

import data.game
import layout_preview
from data.game import Game, _article


def game_with(result_description=None, events=None, players=None, pitcher_id=None):
    """A Game with just enough live data for the description methods."""
    game = Game.__new__(Game)
    current_play = {"result": {}}
    if result_description is not None:
        current_play["result"]["description"] = result_description
    if events is not None:
        current_play["playEvents"] = events
    game._current_data = {
        "liveData": {
            "plays": {"currentPlay": current_play},
            "linescore": {"defense": {"pitcher": {"id": pitcher_id}} if pitcher_id else {}},
        },
        "gameData": {"players": players or {}},
    }
    return game


def pitch(pitch_type="Slider", speed=88.4, call="Ball", is_pitch=True):
    details = {"description": call}
    if pitch_type:
        details["type"] = {"description": pitch_type}
    return {"isPitch": is_pitch, "details": details, "pitchData": {"startSpeed": speed}}


PITCHER = {"ID660271": {"fullName": "Shohei Ohtani", "boxscoreName": "Ohtani, S"}}


class TestArticle(unittest.TestCase):
    """ "a 88mph" reads wrong: spoken, 88 begins with a vowel."""

    def test_eighties_take_an(self):
        self.assertEqual(_article(88), "an")
        self.assertEqual(_article(80), "an")

    def test_other_speeds_take_a(self):
        for speed in (71, 94, 100, 65):
            self.assertEqual(_article(speed), "a", speed)

    def test_vowel_initial_pitch_names_take_an(self):
        self.assertEqual(_article("Eephus"), "an")
        self.assertEqual(_article("Slider"), "a")


class TestLastPitchSentence(unittest.TestCase):
    def test_reads_as_prose(self):
        game = game_with(events=[pitch()], players=PITCHER, pitcher_id=660271)
        self.assertEqual(game.last_pitch_sentence(), "Shohei Ohtani throws an 88mph Slider (Ball)")

    def test_uses_the_last_event_not_the_first(self):
        game = game_with(
            events=[pitch("Curveball", 78, "Called Strike"), pitch("Sinker", 94, "Foul")],
            players=PITCHER,
            pitcher_id=660271,
        )
        self.assertIn("Sinker", game.last_pitch_sentence())
        self.assertNotIn("Curveball", game.last_pitch_sentence())

    def test_in_play_calls_are_omitted(self):
        """The resolved play arrives seconds later and says what happened, so
        announcing "in play, out(s)" first is clumsy and redundant."""
        game = game_with(events=[pitch(call="In play, out(s)")], players=PITCHER, pitcher_id=660271)
        sentence = game.last_pitch_sentence()
        self.assertNotIn("In play", sentence)
        self.assertTrue(sentence.endswith("Slider"), sentence)

    def test_a_call_that_repeats_the_pitch_type_is_omitted(self):
        game = game_with(events=[pitch("Slider", 88, "Slider")], players=PITCHER, pitcher_id=660271)
        self.assertEqual(game.last_pitch_sentence().count("Slider"), 1)

    def test_non_pitch_events_are_ignored(self):
        game = game_with(events=[pitch(is_pitch=False)], players=PITCHER, pitcher_id=660271)
        self.assertEqual(game.last_pitch_sentence(), "")

    def test_no_events_is_empty(self):
        self.assertEqual(game_with(events=[]).last_pitch_sentence(), "")


class TestCurrentPlayDescription(unittest.TestCase):
    def test_a_resolved_play_wins(self):
        game = game_with(
            result_description="Ohtani homers (40) on a fly ball to right field.",
            events=[pitch()],
            players=PITCHER,
            pitcher_id=660271,
        )
        self.assertEqual(game.current_play_description(), "Ohtani homers (40) on a fly ball to right field.")

    def test_falls_back_to_the_live_pitch_mid_at_bat(self):
        game = game_with(events=[pitch()], players=PITCHER, pitcher_id=660271)
        self.assertEqual(game.current_play_description(), "Shohei Ohtani throws an 88mph Slider (Ball)")

    def test_holds_the_last_resolved_play_when_there_is_nothing_live(self):
        """Between innings and across a pitching change there is no pitch and no
        result. Holding beats blanking, which is the whole point of the change."""
        game = game_with(result_description="Ohtani singles on a line drive.", players=PITCHER)
        game.current_play_description()

        game._current_data["liveData"]["plays"]["currentPlay"] = {"result": {}}
        self.assertEqual(game.current_play_description(), "Ohtani singles on a line drive.")

    def test_a_pitch_does_not_overwrite_the_remembered_play(self):
        """Once pitches stop, the last completed play is more useful than the last
        pitch of an at-bat that has since ended."""
        game = game_with(result_description="Ohtani doubles.", players=PITCHER, pitcher_id=660271)
        game.current_play_description()

        game._current_data["liveData"]["plays"]["currentPlay"] = {"result": {}, "playEvents": [pitch()]}
        self.assertIn("throws", game.current_play_description())

        game._current_data["liveData"]["plays"]["currentPlay"] = {"result": {}}
        self.assertEqual(game.current_play_description(), "Ohtani doubles.")

    def test_empty_when_nothing_has_happened_yet(self):
        self.assertEqual(game_with().current_play_description(), "")


SIZE = "w128h64"
BACKGROUND = (7, 14, 25)

# atbat.play_description on w128h64: x 55, y 61 (baseline), width 73, font 4x6.
# A 4x6 cell above baseline 61 covers y 56..61, and x 0..52 there is team banner.
SLOT_ROWS = range(56, 62)
SLOT_COLUMNS = range(55, 128)

SHORT = "Mound visit."  # 12 chars = 48px, comfortably inside 73
LONG = "Yelich homers (30) on a fly ball to right center field."


def render_with_description(description):
    with mock.patch.object(data.game.Game, "current_play_description", lambda self: description):
        layout_preview._layout_pool.clear()
        png = layout_preview.render(SIZE, "live")
    layout_preview._layout_pool.clear()
    image = Image.open(io.BytesIO(png)).convert("RGB")
    pixels = image.load()
    return {x for y in SLOT_ROWS for x in SLOT_COLUMNS if pixels[x, y] != BACKGROUND}


class TestPlayDescriptionScrolling(unittest.TestCase):
    """Only text too wide for the slot animates.

    The renderer parks a scrolling line off the right edge on its first frame, so
    "is it in the slot at frame zero?" cleanly separates the two paths without
    having to step an animation.
    """

    def test_text_that_fits_is_drawn_in_place(self):
        drawn = render_with_description(SHORT)
        self.assertTrue(drawn, f"{SHORT!r} fits the slot and should be visible immediately, not scrolled in")
        self.assertGreaterEqual(min(drawn), 55, "the line should start at its configured x")

    def test_text_too_wide_to_fit_still_scrolls(self):
        self.assertEqual(render_with_description(LONG), set(), "a long description should start off the right edge")

    def test_nothing_is_drawn_without_a_description(self):
        self.assertEqual(render_with_description(""), set())


if __name__ == "__main__":
    unittest.main()
