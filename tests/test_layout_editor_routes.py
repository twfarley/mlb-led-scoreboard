"""Route-level tests for the layout editor endpoints.

The editor is a plain stdlib http.server, so its handler can be driven directly
over in-memory buffers -- no socket, no port. That keeps the routes covered even
where binding a listener is not possible.
"""

import io
import json
import unittest

import config_editor


class Harness(config_editor.Handler):
    """Runs one request through the real handler and captures the response."""

    def __init__(self, path):  # noqa: D107 - deliberately skips the socket setup
        self.rfile = io.BytesIO(f"GET {path} HTTP/1.1\r\nHost: test\r\n\r\n".encode())
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


if __name__ == "__main__":
    unittest.main()
