"""Route-level tests for the layout editor endpoints.

The editor is a plain stdlib http.server, so its handler can be driven directly
over in-memory buffers -- no socket, no port. That keeps the routes covered even
where binding a listener is not possible.
"""

import copy
import io
import json
import unittest

import config_editor


class Harness(config_editor.Handler):
    """Runs one request through the real handler and captures the response."""

    def __init__(self, path, post=None):  # noqa: D107 - deliberately skips the socket setup
        if post is None:
            raw = f"GET {path} HTTP/1.1\r\nHost: test\r\n\r\n".encode()
        else:
            payload = json.dumps(post).encode()
            raw = (
                f"POST {path} HTTP/1.1\r\nHost: test\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
            ).encode() + payload
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 0)
        self.requestline = ""
        self.request_version = "HTTP/1.1"
        self.command = ""
        self.handle_one_request()

    def log_message(self, fmt, *args):
        pass

    @property
    def raw(self):
        return self.wfile.getvalue()

    @property
    def status(self):
        return int(self.raw.split(b" ", 2)[1])

    @property
    def body(self):
        return self.raw.split(b"\r\n\r\n", 1)[1]

    def json(self):
        return json.loads(self.body)


class TestLayoutRoutes(unittest.TestCase):
    def test_meta_lists_sizes_screens_and_anchors(self):
        res = Harness("/api/layout/meta")
        self.assertEqual(res.status, 200)
        body = res.json()
        self.assertIn("w128h64", body["sizes"])
        self.assertIn("final", body["screens"])
        # The anchor that caused a real layout bug must survive the round trip.
        self.assertEqual(body["anchors"]["final.inning"]["anchor"], "center")
        self.assertEqual(body["anchors"]["teams.line_score.home"]["anchor"], "right")

    def test_elements_are_filtered_to_the_screen(self):
        final = Harness("/api/layout/elements?size=w128h64&screen=final").json()
        live = Harness("/api/layout/elements?size=w128h64&screen=live").json()

        self.assertEqual(final["width"], 128)
        self.assertEqual(final["height"], 64)

        final_keys = {e["keypath"] for e in final["elements"]}
        live_keys = {e["keypath"] for e in live["elements"]}

        self.assertIn("final.inning", final_keys)
        self.assertNotIn("final.inning", live_keys)
        self.assertIn("atbat.batter", live_keys)
        self.assertNotIn("atbat.batter", final_keys)
        # The team banner is drawn on every game screen.
        self.assertIn("teams.name.home", final_keys & live_keys)

    def test_elements_carry_a_box_and_anchor_semantics(self):
        body = Harness("/api/layout/elements?size=w128h64&screen=final").json()
        by_key = {e["keypath"]: e for e in body["elements"]}

        inning = by_key["final.inning"]
        self.assertEqual(inning["anchor"], "center")
        self.assertTrue(inning["dynamic"], "text width depends on live data")
        x0, _, x1, _ = inning["box"]
        # A centre anchor must straddle the stored x, not start at it.
        self.assertLess(x0, inning["coords"]["x"])
        self.assertGreater(x1, inning["coords"]["x"])

        score = by_key["teams.line_score.home"]
        self.assertEqual(score["anchor"], "right")
        self.assertLessEqual(score["box"][2], score["coords"]["x"])

    def test_bad_size_and_screen_are_rejected(self):
        self.assertEqual(Harness("/api/layout/elements?size=nonsense&screen=final").status, 400)
        self.assertEqual(Harness("/api/layout/elements?size=w128h64&screen=nope").status, 400)

    def test_static_assets_are_served(self):
        for path in ("/layout", "/layout.js", "/layout.css"):
            self.assertEqual(Harness(path).status, 200, path)

    def test_preview_returns_a_png(self):
        res = Harness("/api/layout/preview?size=w128h64&screen=final")
        self.assertEqual(res.status, 200)
        self.assertTrue(res.body.startswith(b"\x89PNG\r\n"), "expected a PNG")


class TestUnsavedEdits(unittest.TestCase):
    """The editor previews edits before they are written, so the override path
    has to change the render without touching disk."""

    def setUp(self):
        self.baseline = Harness("/api/layout/elements?size=w128h64&screen=final").json()
        self.coords = copy.deepcopy(self.baseline["coords"])
        self.on_disk = copy.deepcopy(self.baseline["coords"])

    def tearDown(self):
        # Nothing in this test class may write a custom coordinates file.
        after = Harness("/api/layout/elements?size=w128h64&screen=final").json()["coords"]
        self.assertEqual(after, self.on_disk, "a preview request modified the stored coordinates")

    def test_posted_coords_move_the_box(self):
        self.coords["final"]["inning"]["y"] = 30
        body = Harness(
            "/api/layout/elements", post={"size": "w128h64", "screen": "final", "coords": self.coords}
        ).json()
        moved = next(e for e in body["elements"] if e["keypath"] == "final.inning")
        original = next(e for e in self.baseline["elements"] if e["keypath"] == "final.inning")
        self.assertEqual(moved["box"][1], original["box"][1] - 15)

    def test_posted_coords_change_the_render(self):
        before = Harness("/api/layout/preview", post={"size": "w128h64", "screen": "final"}).body
        self.coords["final"]["inning"]["y"] = 30
        after = Harness("/api/layout/preview", post={"size": "w128h64", "screen": "final", "coords": self.coords}).body
        self.assertTrue(after.startswith(b"\x89PNG\r\n"))
        self.assertNotEqual(before, after, "moving an element should change the rendered board")


class TestScreenVariants(unittest.TestCase):
    """Several elements only exist in a particular game state, so the editor
    offers a variant per state rather than pretending they can all be shown."""

    def test_every_variant_renders(self):
        import layout_preview

        for name in layout_preview.SCREENS:
            png = layout_preview.render("w128h64", name)
            self.assertTrue(png.startswith(b"\x89PNG\r\n"), f"{name} did not render")

    def test_nohitter_variant_swaps_in_the_alternate_positions(self):
        import layout_preview

        normal = {e["keypath"]: e for e in layout_preview.elements("w128h64", "live")}
        # layout.coords() returns the `nohit` sub-dict when the state is active,
        # so the alternate position must actually differ from the normal one.
        coords = layout_preview._coords_for("w128h64")
        self.assertNotEqual(coords["batter_count"]["nohit"], {k: coords["batter_count"][k] for k in ("x", "y")})
        self.assertIn("nohitter", normal)

    def test_nohitter_variant_forces_a_late_inning(self):
        """The NO-HITTER banner is gated on the inning, so a 1st-inning fixture
        would render the very element the variant exists to show."""
        import layout_preview

        spec = layout_preview.SCREENS["live_nohitter"]
        threshold = layout_preview._coords_for("w128h64")["nohitter"]["innings_until_display"]
        self.assertIsNotNone(spec.inning)
        self.assertGreater(spec.inning, threshold)

    def test_show_disabled_changes_the_render_but_not_the_coords(self):
        import layout_preview

        plain = layout_preview.render("w128h64", "live")
        forced = layout_preview.render("w128h64", "live", None, True)
        self.assertNotEqual(plain, forced, "switched-off elements should appear")
        # The flag is a render-time override only.
        self.assertIs(layout_preview._coords_for("w128h64")["atbat"]["pitch"]["enabled"], False)

    def test_derived_elements_are_listed_without_a_box(self):
        import layout_preview

        era = next(e for e in layout_preview.elements("w128h64", "live") if e["keypath"] == "atbat.pitcher_era")
        self.assertIsNone(era["box"], "a derived element has no coordinates to box")
        self.assertEqual(era["anchor"], "derived")
        self.assertEqual(era["controlled_by"], ["atbat.batter_stats", "atbat.pitcher"])
        # It must not appear on a screen that never draws it.
        final = {e["keypath"] for e in layout_preview.elements("w128h64", "final")}
        self.assertNotIn("atbat.pitcher_era", final)

    def test_inning_break_screen_shows_due_up_and_not_the_at_bat(self):
        import layout_preview

        keys = {e["keypath"] for e in layout_preview.elements("w128h64", "live_break")}
        self.assertIn("inning.break.due_up.leadoff", keys)
        self.assertNotIn("atbat.batter", keys)
        # ...and the normal live screen is the other way round.
        live = {e["keypath"] for e in layout_preview.elements("w128h64", "live")}
        self.assertIn("atbat.batter", live)
        self.assertNotIn("inning.break.due_up.leadoff", live)


if __name__ == "__main__":
    unittest.main()
