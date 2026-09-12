from data.config.layout import Layout
from data.scoreboard.team import Team
from renderers.games.teams import can_use_full_team_names

import unittest, string, random

WIDTH = 32
HEIGHT = 32


def make_layout(full=False, shorten_team_name_on_high_line_score=False, max_width=None):
    name = {"full": full}
    if max_width is not None:
        name["max_width"] = max_width
    return Layout(
        {
            "teams": {
                "name": name,
                "line_score": {"shorten_team_name_on_high_line_score": shorten_team_name_on_high_line_score},
                "record": {},
            },
            "defaults": {"font_name": "4x6"},
        },
        WIDTH,
        HEIGHT,
    )


def make_team(
    abbrev="".join(random.choice(string.ascii_uppercase) for _ in range(3)),
    runs=0,
    name=None,
    hits=0,
    errors=0,
    record="0-0",
    special_uniform=None,
    abs_challenges=2,
):
    if name is None:
        name = f"Test {abbrev}"

    return Team(abbrev, runs, name, hits, errors, record, special_uniform, abs_challenges)


class TestCanUseFullTeamNames(unittest.TestCase):

    def test_global_setting_disabled(self):
        layout = make_layout()
        teams = [make_team(), make_team()]

        self.assertFalse(can_use_full_team_names(layout, teams))

    def test_global_setting_disabled_shorten_disabled_with_10_hits(self):
        layout = make_layout()
        teams = [make_team(hits=10), make_team()]

        self.assertFalse(can_use_full_team_names(layout, teams))

    def test_global_setting_enabled_shorten_disabled_with_10_hits(self):
        layout = make_layout(full=True)
        teams = [make_team(hits=10), make_team()]

        self.assertTrue(can_use_full_team_names(layout, teams))

    def test_settings_enabled_with_10_hits(self):
        layout = make_layout(full=True, shorten_team_name_on_high_line_score=True)
        teams = [make_team(hits=10), make_team()]

        self.assertFalse(can_use_full_team_names(layout, teams))

    def test_settings_enabled_with_10_runs(self):
        layout = make_layout(full=True, shorten_team_name_on_high_line_score=True)
        teams = [make_team(runs=10), make_team()]

        self.assertFalse(can_use_full_team_names(layout, teams))

    def test_settings_enabled_with_10_errors(self):
        layout = make_layout(full=True, shorten_team_name_on_high_line_score=True)
        # A very bad day at the ballpark
        teams = [make_team(errors=10), make_team()]

        self.assertFalse(can_use_full_team_names(layout, teams))

    def test_settings_enabled_with_rhe_less_than_10(self):
        layout = make_layout(full=True, shorten_team_name_on_high_line_score=True)
        teams = [make_team(runs=5, hits=5, errors=5), make_team(runs=5, hits=5, errors=5)]

        self.assertTrue(can_use_full_team_names(layout, teams))


class TestFullNameWidthLimit(unittest.TestCase):
    """`max_width` abbreviates names too wide for the space before the line score.

    Nothing clips team names -- __render_team_text is a plain DrawText -- so on a
    narrow banner a long full name simply runs into the score. w128h32 shipped
    that way: "Nationals" at 6px overlapped the runs column.
    """

    def test_no_limit_keeps_full_names(self):
        layout = make_layout(full=True)
        self.assertTrue(can_use_full_team_names(layout, [make_team(name="Diamondbacks"), make_team(name="Cubs")]))

    def test_names_within_the_limit_stay_full(self):
        # 4x6 font, so "Brewers" is 28px.
        layout = make_layout(full=True, max_width=51)
        self.assertTrue(can_use_full_team_names(layout, [make_team(name="Brewers"), make_team(name="Padres")]))

    def test_an_over_wide_name_abbreviates_both_teams(self):
        """Applied to both rows together, so they never disagree on the format."""
        layout = make_layout(full=True, max_width=20)
        self.assertFalse(can_use_full_team_names(layout, [make_team(name="Brewers"), make_team(name="Cubs")]))

    def test_the_limit_is_measured_in_pixels_not_characters(self):
        layout = make_layout(full=True, max_width=24)
        # "Cubs" is 4 chars = 16px, fits; "Nationals" is 9 chars = 36px, does not.
        self.assertTrue(can_use_full_team_names(layout, [make_team(name="Cubs"), make_team(name="Reds")]))
        self.assertFalse(can_use_full_team_names(layout, [make_team(name="Cubs"), make_team(name="Nationals")]))
