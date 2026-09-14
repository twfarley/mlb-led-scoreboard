"""Tests for the optional season records on the FINAL screen.

Drawn by the postgame renderer rather than by the team banner's own `teams.record`.
The banner draws on every screen, so a record placed beside the score there also
appears mid-game; a board whose live screen already fills that space can only show
records once the game is over.

The record MLB reports in `gameData.teams.<side>.record` for a completed game
already includes that game, so nothing here compensates for a lag. Checked against
the standings on games that had just gone final.
"""

import unittest
from unittest import mock

import data.config.color
from data.config.color import Color
from data.config.layout import Layout
from renderers.games import postgame as postgamerender

WIDTH = 128
HEIGHT = 64

BASE_COORDS = {
    "defaults": {"font_name": "4x6"},
    "final": {"inning": {"x": 64, "y": 13}},
}

RECORD_COORDS = {
    "font_name": "4x6",
    "enabled": True,
    "away": {"x": 56, "y": 39},
    "home": {"x": 56, "y": 57},
}

COLORS = {
    "default": {"background": {"r": 0, "g": 0, "b": 0}},
    "final": {"record": {"r": 1, "g": 2, "b": 3}},
}


class Recorder:
    def __init__(self):
        self.texts = []

    def DrawText(self, canvas, font, x, y, color, text):
        self.texts.append({"x": x, "y": y, "text": text, "color": color})

    def Color(self, r, g, b):
        return (r, g, b)


class Stub:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_scoreboard(away_record, home_record):
    return Stub(away_team=Stub(record=away_record), home_team=Stub(record=home_record))


class TestFinalRecords(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        for target in (postgamerender, data.config.color):
            patcher = mock.patch.object(target, "graphics", self.recorder)
            patcher.start()
            self.addCleanup(patcher.stop)

    def render(self, away, home, record=RECORD_COORDS):
        values = dict(BASE_COORDS)
        values["final"] = dict(values["final"])
        if record is not None:
            values["final"]["record"] = record
        layout = Layout(values, WIDTH, HEIGHT)
        postgamerender._render_records(
            layout=layout, canvas=None, colors=Color(COLORS), scoreboard=make_scoreboard(away, home)
        )
        return [entry["text"] for entry in self.recorder.texts]

    def test_a_layout_without_the_key_draws_nothing(self):
        self.assertEqual(self.render({"wins": 93, "losses": 57}, {"wins": 70, "losses": 79}, record=None), [])

    def test_disabled_is_the_same_as_absent(self):
        off = dict(RECORD_COORDS, enabled=False)
        self.assertEqual(self.render({"wins": 93, "losses": 57}, {"wins": 70, "losses": 79}, record=off), [])

    def test_both_records_are_drawn_away_first(self):
        drawn = self.render({"wins": 55, "losses": 94}, {"wins": 70, "losses": 79})
        self.assertEqual(drawn, ["(55-94)", "(70-79)"])

    def test_each_record_lands_on_its_own_team_row(self):
        self.render({"wins": 55, "losses": 94}, {"wins": 70, "losses": 79})
        self.assertEqual([entry["y"] for entry in self.recorder.texts], [39, 57])
        self.assertEqual({entry["x"] for entry in self.recorder.texts}, {56})

    def test_an_empty_record_is_skipped_rather_than_drawn_as_zeroes(self):
        """Exhibition and spring-training sides can arrive with no record at all,
        and "(0-0)" would be a claim rather than an absence."""
        drawn = self.render({}, {"wins": 70, "losses": 79})
        self.assertEqual(drawn, ["(70-79)"])

    def test_a_partial_record_is_also_skipped(self):
        self.assertEqual(self.render({"wins": 93}, {}), [])


if __name__ == "__main__":
    unittest.main()
