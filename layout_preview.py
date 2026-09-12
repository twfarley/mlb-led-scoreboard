"""Headless board renderer for the layout editor's WYSIWYG preview.

Renders a real board screen -- the actual renderers, the actual BDF fonts, the
actual draw order -- into a PNG at native resolution (1 image pixel per LED).
The editor upscales that by an integer factor in the browser, so the preview is
pixel-exact and coordinates snap to a true grid.

Why render server-side instead of redrawing the board in JavaScript: the only
thing that knows where an element really lands is the renderer itself. Text is
BDF, `y` is a baseline, and anchoring varies per element (see
schemas/coordinates/_anchors.json). A JS reimplementation would drift from the
board, which defeats the point of WYSIWYG.

Screens are driven by canned MLB games pinned to a `timecode`, so a preview is
deterministic and reproducible -- the same request always renders the same
board. The API is still contacted to fetch them; responses are cached per
process.
"""

from __future__ import annotations

import io
import json
import re
from collections import namedtuple
from pathlib import Path
from typing import Any

REPO = Path(__file__).parent
EMULATOR_CONFIG = REPO / "preview_emulator_config.json"

# A screen is one branch of MlbRenderer.__draw_game, plus the always-on-top team
# banner. `timecode=None` means "latest", which for these games is Final.
Fixture = namedtuple("Fixture", ["game_id", "game_date", "timecode", "label"])

SCREENS: dict[str, Fixture] = {
    "live": Fixture(565956, "2019-08-17", "20190817_231033", "Live game (mid at-bat)"),
    "pregame": Fixture(565956, "2019-08-17", "20190817_223000", "Pregame"),
    "final": Fixture(565956, "2019-08-17", None, "Final"),
    "irregular": Fixture(745808, "2024-06-26", "20240627_004712", "Delayed / irregular status"),
}

_SIZE_RE = re.compile(r"^w(\d+)h(\d+)$")

_game_cache: dict[str, Any] = {}


def sizes() -> list[str]:
    """Board sizes that ship a coordinates schema, e.g. ['w128h64', ...]."""
    return sorted(p.stem.replace(".schema", "") for p in (REPO / "schemas" / "coordinates").glob("w*.schema.json"))


def screens() -> dict[str, str]:
    return {name: fx.label for name, fx in SCREENS.items()}


def _force_emulation() -> None:
    """Select the emulator driver.

    Must run before any renderer is imported: renderers do `from driver import
    graphics`, and on a Pi the wrapper would otherwise bind the real rgbmatrix,
    which cannot render to an offscreen buffer.
    """
    import driver
    from driver.mode import DriverMode

    driver.set_mode(DriverMode.SOFTWARE_EMULATION)


def _load_json(custom: Path, example: Path) -> dict:
    return dict(json.loads((custom if custom.exists() else example).read_text()))


def _coords_for(size: str) -> dict:
    return _load_json(REPO / "coordinates" / f"{size}.json", REPO / "coordinates" / f"{size}.example.json")


def _build_game(fixture: Fixture):
    """A Game pinned to a fixed point in a real game, so previews are stable."""
    key = f"{fixture.game_id}:{fixture.timecode}"
    if key in _game_cache:
        return _game_cache[key]

    import data.game
    from data.leagues import LEAGUES

    MockConfig = namedtuple("MockConfig", ["sync_amount", "api_refresh_rate", "uniform_types"])
    config = MockConfig(sync_amount=0, api_refresh_rate=10, uniform_types={})

    game = data.game.Game.from_scheduled(
        {
            "league": LEAGUES["MLB"],
            "game_id": fixture.game_id,
            "game_date": fixture.game_date,
            "national_broadcasts": [],
            "series_status": "",
        },
        config,
    )
    if game is None:
        raise RuntimeError(f"could not load preview game {fixture.game_id} (network?)")

    if fixture.timecode:
        game.update(force=True, testing_params={"timecode": fixture.timecode})

    _game_cache[key] = game
    return game


def render(size: str, screen: str) -> bytes:
    """Render one screen of one board size. Returns PNG bytes at native resolution."""
    m = _SIZE_RE.match(size)
    if not m:
        raise ValueError(f"bad size {size!r}, expected e.g. 'w128h64'")
    if screen not in SCREENS:
        raise ValueError(f"unknown screen {screen!r}, expected one of {sorted(SCREENS)}")
    width, height = int(m.group(1)), int(m.group(2))

    _force_emulation()

    # Point the emulator at the preview config before any options object is
    # built -- RGBMatrixEmulatorConfig reads CONFIG_PATH at construction, and we
    # must not disturb the user's own emulator_config.json.
    from RGBMatrixEmulator.internal.emulator_config import RGBMatrixEmulatorConfig

    RGBMatrixEmulatorConfig.CONFIG_PATH = str(EMULATOR_CONFIG)

    from driver import RGBMatrix, RGBMatrixOptions
    from data.config.color import Color
    from data.config.layout import Layout
    from data.scoreboard import Scoreboard
    from data.scoreboard.postgame import Postgame
    from data.scoreboard.pregame import Pregame
    from renderers.games import irregular, postgame as postgamerender, pregame as pregamerender, teams
    from renderers.games import game as gamerender

    layout = Layout(_coords_for(size), width, height)
    colors = Color(_load_json(REPO / "colors" / "scoreboard.json", REPO / "colors" / "scoreboard.example.json"))
    team_colors = Color(_load_json(REPO / "colors" / "teams.json", REPO / "colors" / "teams.example.json"))

    fixture = SCREENS[screen]
    game = _build_game(fixture)
    scoreboard = Scoreboard(game)
    layout.state_for_game(game)

    options = RGBMatrixOptions()
    options.cols, options.rows = width, height
    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()

    bg = colors.color("default.background")
    canvas.Fill(bg["r"], bg["g"], bg["b"])

    # Mirrors MlbRenderer.__draw_game. text_pos is parked at the canvas width so
    # scrolling text renders at its start position rather than mid-scroll.
    text_pos = width
    if screen == "pregame":
        pregamerender.render_pregame(canvas, layout, colors, Pregame(game, "12h"), text_pos, False, False, False)
    elif screen == "final":
        postgamerender.render_postgame(canvas, layout, colors, Postgame(game), scoreboard, text_pos, False, False)
    elif screen == "irregular":
        short_text = layout.coords("status.text")["short_text"]
        irregular.render_irregular_status(canvas, layout, colors, scoreboard, short_text, text_pos)
    else:
        gamerender.render_live_game(canvas, layout, colors, scoreboard, text_pos, 0)

    # Always last, so it paints over the screen content -- the fixed draw order.
    teams.render_team_banner(
        canvas,
        layout,
        team_colors,
        scoreboard.home_team,
        scoreboard.away_team,
        show_score=(screen != "pregame"),
        scoreboard_colors=colors,
    )

    matrix.SwapOnVSync(canvas)

    adapter = canvas.display_adapter
    image = adapter._get_masked_image(adapter._last_frame())
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
