"""Tests for the layout monitor.

The monitor's job is to notice layout faults, so the test that matters is that
it still catches the one this layout actually shipped: "FINAL" positioned so the
team banner -- drawn last, and therefore on top -- painted over its first
glyphs. Two earlier versions of the check missed it, for two different reasons,
so it is pinned here.
"""

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "tools"))

import layout_monitor as monitor  # noqa: E402
import layout_preview  # noqa: E402

SIZE = "w128h64"


def findings_for(coords, screen="final", min_severity=("high", "medium")):
    with mock.patch.object(layout_preview, "_coords_for", lambda size: coords):
        layout_preview._layout_pool.clear()
        bg = monitor._background(SIZE)
        elements = layout_preview.elements(SIZE, screen)
        out = []
        for check in monitor.CHECKS:
            out += [f for f in check(SIZE, screen, elements, bg) if f["severity"] in min_severity]
    layout_preview._layout_pool.clear()
    return out


class TestOverdrawDetection(unittest.TestCase):
    def setUp(self):
        self.good = layout_preview._coords_for(SIZE)

    def test_current_layout_is_clean(self):
        self.assertEqual(findings_for(self.good), [], "the shipped layout should not report faults")

    def test_catches_the_final_under_the_score_regression(self):
        broken = copy.deepcopy(self.good)
        # The pre-retune coordinates. final.inning is centre-anchored, so x=67
        # put "FINAL 14" at x 39..94 -- starting inside the home team block.
        broken["final"]["inning"] = {"x": 67, "y": 61}
        broken["final"]["scrolling_text"] = {"x": 0, "y": 44, "width": 128}

        overdrawn = [f for f in findings_for(broken) if f["check"] == "overdrawn"]
        self.assertEqual(len(overdrawn), 1, "the banner covering FINAL must be reported")
        finding = overdrawn[0]
        self.assertEqual(finding["severity"], "high")
        self.assertIn("final.inning", finding["elements"])
        self.assertGreater(finding["pixels"], 0)

    def test_overdraw_means_changed_not_merely_erased(self):
        """The banner fills its rectangles with the team's colour.

        An earlier check only looked for pixels turning background-coloured, so
        it found nothing at all on the real bug.
        """
        broken = copy.deepcopy(self.good)
        broken["final"]["inning"] = {"x": 67, "y": 61}
        bg = monitor._background(SIZE)
        with mock.patch.object(layout_preview, "_coords_for", lambda size: broken):
            layout_preview._layout_pool.clear()
            content = monitor._image(layout_preview.render(SIZE, "final", skip_banner=True))
            full = monitor._image(layout_preview.render(SIZE, "final"))
        layout_preview._layout_pool.clear()

        cpx, fpx = content.load(), full.load()
        changed_to_bg = 0
        changed_at_all = 0
        for y in range(content.height):
            for x in range(content.width):
                if cpx[x, y] == bg or fpx[x, y] == cpx[x, y]:
                    continue
                changed_at_all += 1
                if fpx[x, y] == bg:
                    changed_to_bg += 1
        self.assertGreater(changed_at_all, 0)
        self.assertEqual(changed_to_bg, 0, "the banner recolours rather than erases, so only 'changed' works")

    def test_attribution_survives_text_wider_than_its_nominal_box(self):
        """Text boxes are sized from a guessed character count.

        "FINAL 14" is twice that, so the covered pixels fall outside the box and
        an earlier version dropped the whole finding for lack of a name.
        """
        broken = copy.deepcopy(self.good)
        broken["final"]["inning"] = {"x": 67, "y": 61}
        finding = next(f for f in findings_for(broken) if f["check"] == "overdrawn")

        with mock.patch.object(layout_preview, "_coords_for", lambda size: broken):
            layout_preview._layout_pool.clear()
            box = next(e["box"] for e in layout_preview.elements(SIZE, "final") if e["keypath"] == "final.inning")
        layout_preview._layout_pool.clear()

        self.assertLess(finding["bounds"][0], box[0], "the covered pixels start left of the nominal box")
        self.assertIn("final.inning", finding["elements"], "and must still be attributed to it")


class TestNoiseControl(unittest.TestCase):
    """A monitor nobody reads is worthless, so the quiet rules are load-bearing."""

    def test_switched_off_elements_are_ignored(self):
        self.assertFalse(monitor._renders({"coords": {"enabled": False}}))
        self.assertFalse(monitor._renders({"coords": {"draw": False}}))
        self.assertTrue(monitor._renders({"coords": {"enabled": True}}))
        self.assertTrue(monitor._renders({"coords": {}}))

    def test_fingerprints_are_stable_for_the_baseline(self):
        a = {"case": "w128h64/final", "check": "collision", "elements": {"b": 1, "a": 2}}
        b = {"case": "w128h64/final", "check": "collision", "elements": {"a": 9, "b": 9}}
        self.assertEqual(monitor.fingerprint(a), monitor.fingerprint(b))

    def test_siblings_do_not_count_as_collisions(self):
        self.assertEqual(monitor._parent("bases.1B"), monitor._parent("bases.2B"))
        self.assertEqual(monitor._parent("outs.1"), monitor._parent("outs.3"))
        self.assertNotEqual(monitor._parent("outs.1"), monitor._parent("bases.1B"))


if __name__ == "__main__":
    unittest.main()


class TestOverlapDetection(unittest.TestCase):
    """The deep check exists because colour-diffing has a blind spot.

    A team name and its line score are drawn in the same team text colour, so
    where they overlap the pixel is identical whichever drew it. w128h32 shipped
    that way and every colour-based check reported clean.
    """

    SIZE = "w128h32"

    def setUp(self):
        self.fixed = layout_preview._coords_for(self.SIZE)
        self.broken = copy.deepcopy(self.fixed)
        del self.broken["teams"]["name"]["max_width"]

    def overlaps(self, coords):
        with mock.patch.object(layout_preview, "_coords_for", lambda size: coords):
            layout_preview._layout_pool.clear()
            elements = layout_preview.elements(self.SIZE, "live")
            found = monitor.check_overlap(self.SIZE, "live", elements, monitor._background(self.SIZE))
        layout_preview._layout_pool.clear()
        return found

    def test_current_layout_has_no_overlaps(self):
        self.assertEqual([f["detail"] for f in self.overlaps(self.fixed)], [])

    def test_catches_the_team_name_over_the_line_score(self):
        found = self.overlaps(self.broken)
        pairs = [set(f["elements"]) for f in found]
        self.assertIn({"teams.name.home", "teams.line_score.home"}, pairs)

    def test_a_panel_under_its_own_text_is_not_an_overlap(self):
        """The team banner exists to sit beneath the names and scores."""
        for finding in self.overlaps(self.broken):
            for keypath in finding["elements"]:
                self.assertFalse(
                    keypath.startswith("teams.background") or keypath.startswith("teams.accent"),
                    f"{keypath} is a panel and should not be reported",
                )
