"""Keeps schemas/coordinates/_anchors.json in step with the coordinate schemas.

The layout editor cannot draw a correct bounding box without knowing what `x`
means for an element, and that lives in the renderer rather than the schema. The
map is therefore hand-authored, so it needs a guard: if someone adds a
positioned element to a coordinates example and forgets the anchor entry, the
editor would silently place its box at the wrong spot. Fail the build instead.
"""

import json
import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent
ANCHORS_FILE = REPO / "schemas" / "coordinates" / "_anchors.json"

VALID_ANCHORS = {"left", "center", "right", "board-right", "none"}
VALID_EXTENTS = {
    "text",
    "scroll",
    "box",
    "square",
    "diamond",
    "arrow_up",
    "arrow_down",
    "vline",
    "squares",
    "image",
    "column",
}

# Keys that mark a dict as a positioned element rather than a plain container.
POSITION_KEYS = {"x", "y", "y_start", "squares"}


def positioned_keypaths(doc) -> set:
    found = set()

    def walk(node, path=""):
        if not isinstance(node, dict):
            return
        if POSITION_KEYS & node.keys():
            found.add(path)
        for key, value in node.items():
            walk(value, f"{path}.{key}" if path else key)

    walk(doc)
    return found


def all_example_keypaths() -> dict:
    """Every positioned keypath across every board size -> the sizes using it."""
    keypaths: dict = {}
    for example in sorted((REPO / "coordinates").glob("w*.example.json")):
        for keypath in positioned_keypaths(json.loads(example.read_text())):
            keypaths.setdefault(keypath, []).append(example.stem.replace(".example", ""))
    return keypaths


class TestLayoutAnchors(unittest.TestCase):
    def setUp(self):
        self.anchors = json.loads(ANCHORS_FILE.read_text())["anchors"]

    def test_every_positioned_element_has_an_anchor(self):
        missing = {kp: sizes for kp, sizes in all_example_keypaths().items() if kp not in self.anchors}
        self.assertEqual(
            missing,
            {},
            "coordinate elements with no entry in schemas/coordinates/_anchors.json. "
            "Add one describing what `x` means for each -- see the $comment in that file.",
        )

    def test_no_stale_anchor_entries(self):
        stale = sorted(set(self.anchors) - set(all_example_keypaths()))
        self.assertEqual(stale, [], "anchor entries for elements that no longer exist in any coordinates example")

    def test_anchor_and_extent_values_are_known(self):
        for keypath, meta in self.anchors.items():
            self.assertIn(meta.get("anchor"), VALID_ANCHORS, f"{keypath} has an unknown anchor")
            self.assertIn(meta.get("extent"), VALID_EXTENTS, f"{keypath} has an unknown extent")

    def test_centre_and_right_anchored_sets_are_pinned(self):
        """Pins the elements whose `x` is not a left edge.

        These are the trap: `final.inning` reads as a left edge but is run
        through center_text_position, and `teams.line_score.*` treats `x` as the
        RIGHTMOST pixel. A box drawn at `x` for either lands in the wrong place,
        which has already caused one real layout bug. Changing this set should be
        a deliberate edit, made together with the renderer.
        """
        by_anchor: dict = {}
        for keypath, meta in self.anchors.items():
            by_anchor.setdefault(meta["anchor"], set()).add(keypath)

        self.assertEqual(
            by_anchor.get("center", set()),
            {
                "final.inning",
                "pregame.start_time",
                "pregame.warmup_text",
                "status.text",
                "network.text",
                "news.time",
                "news.conditions",
                "news.temperature",
                "news.wind",
                "news.wind_speed",
                "news.wind_dir",
            },
        )
        self.assertEqual(
            by_anchor.get("right", set()),
            {
                "teams.line_score.away",
                "teams.line_score.home",
                # __render_inning_number subtracts the text width from x.
                "inning.number",
                "inning.number.nohit",
                "inning.number.perfect_game",
            },
        )
        self.assertEqual(by_anchor.get("board-right", set()), {"atbat.batter_stats"})


if __name__ == "__main__":
    unittest.main()
