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
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional, cast

REPO = Path(__file__).parent
EMULATOR_CONFIG = REPO / "preview_emulator_config.json"
ANCHORS_FILE = REPO / "schemas" / "coordinates" / "_anchors.json"

# Characters assumed when sizing a text box whose real width depends on live
# data. Such elements are returned with dynamic=True so the UI can show the box
# as approximate rather than pretending it is exact.
NOMINAL_CHARS = 4

# A screen is one branch of MlbRenderer.__draw_game, plus the always-on-top team
# banner. Several elements only exist in a particular game state -- the
# `nohit` / `perfect_game` / `warmup` sub-keys are alternate POSITIONS that
# layout.coords() swaps in when that state is active, so they are mutually
# exclusive and cannot all be shown at once. Hence variants rather than one
# "show everything" view.
#
# `timecode=None` means "latest", which for these games is Final.
Fixture = namedtuple("Fixture", ["game_id", "game_date", "timecode"])

_MIL_WSH = Fixture(565956, "2019-08-17", "20190817_231033")
_MIL_WSH_PRE = Fixture(565956, "2019-08-17", "20190817_223000")
_MIL_WSH_FINAL = Fixture(565956, "2019-08-17", None)
_DELAYED = Fixture(745808, "2024-06-26", "20240627_004712")


class Screen(NamedTuple):
    fixture: Fixture
    base: str  # which renderer branch to run
    label: str
    state: Optional[str] = None  # layout state: nohit | perfect_game | warmup
    inning_break: bool = False
    inning: Optional[int] = None  # force the inning number (see below)


SCREENS: dict[str, Screen] = {
    "live": Screen(_MIL_WSH, "live", "Live game (mid at-bat)"),
    "live_break": Screen(_MIL_WSH, "live", "Live — inning break", inning_break=True),
    # The NO-HITTER banner only draws past coords("nohitter").innings_until_display
    # (5 on most boards), and the fixture is in the 1st, so force a late inning or
    # the very element these variants exist to show would stay invisible.
    "live_nohitter": Screen(_MIL_WSH, "live", "Live — no-hitter", state="nohit", inning=7),
    "live_perfect": Screen(_MIL_WSH, "live", "Live — perfect game", state="perfect_game", inning=7),
    "pregame": Screen(_MIL_WSH_PRE, "pregame", "Pregame"),
    "pregame_warmup": Screen(_MIL_WSH_PRE, "pregame", "Pregame — warmup", state="warmup"),
    "final": Screen(_MIL_WSH_FINAL, "final", "Final"),
    "final_nohitter": Screen(_MIL_WSH_FINAL, "final", "Final — no-hitter", state="nohit"),
    "irregular": Screen(_DELAYED, "irregular", "Delayed / irregular status"),
}

_SIZE_RE = re.compile(r"^w(\d+)h(\d+)$")

_game_cache: dict[str, Any] = {}

# Live Game objects, kept across cycles so the monitor refreshes them rather
# than rebuilding (and re-fetching) one per pass.
_live_games: dict[Any, Any] = {}

# Sub-keys that are alternate positions rather than elements of their own.
_LAYOUT_STATES = ("nohit", "perfect_game", "warmup")

# Layout owns a per-instance BDF font cache, and loading a font costs ~28ms, so
# building a fresh Layout per render dominates the time (~390ms). Coordinates are
# usually unchanged between renders -- the monitor reuses them for a whole cycle,
# and the editor only alters one value per edit -- so key a small pool on the
# coordinates themselves. Bounded, because the editor produces a new variant on
# every drag.
_LAYOUT_POOL_MAX = 8
_layout_pool: dict[tuple, Any] = {}


def _layout_for(values: dict, width: int, height: int):
    from data.config.layout import Layout

    key = (width, height, json.dumps(values, sort_keys=True))
    cached = _layout_pool.get(key)
    if cached is not None:
        return cached
    if len(_layout_pool) >= _LAYOUT_POOL_MAX:
        _layout_pool.pop(next(iter(_layout_pool)))
    layout = Layout(values, width, height)
    _layout_pool[key] = layout
    return layout


def sizes() -> list[str]:
    """Board sizes that ship a coordinates schema, e.g. ['w128h64', ...]."""
    return sorted(p.stem.replace(".schema", "") for p in (REPO / "schemas" / "coordinates").glob("w*.schema.json"))


def screens() -> dict[str, str]:
    return {name: scr.label for name, scr in SCREENS.items()}


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


def screen_for_game(game) -> str:
    """The screen the board would draw for this game right now.

    Mirrors the branch order in MlbRenderer.__draw_game, so a live case is
    checked against the same renderer the board would actually use.
    """
    from data import status as game_status

    state = game.status()
    if game_status.is_pregame(state):
        return "pregame"
    if game_status.is_complete(state):
        return "final"
    if game_status.is_irregular(state):
        return "irregular"
    return "live"


def todays_games(leagues=("MLB",)) -> list:
    """Today's games as live Game objects, refreshed in place across calls.

    The canned fixtures cover every screen but only one set of data shapes. Real
    games are what surface the faults that only appear with particular values --
    a two-digit score widening a line score, an unusually long player name, an
    extra-innings "FINAL 14" reaching further than "FINAL" ever does.

    WPBL is left out by default: its adaptor talks to a separate host that is not
    always reachable, and a monitor should not fail on that.
    """
    import data.game
    from data.leagues import LEAGUES

    MockConfig = namedtuple("MockConfig", ["sync_amount", "api_refresh_rate", "uniform_types"])
    config = MockConfig(sync_amount=0, api_refresh_rate=10, uniform_types={})
    today = datetime.now().strftime("%Y-%m-%d")

    games = []
    for name in leagues:
        league = LEAGUES.get(name)
        if league is None:
            continue
        try:
            # data/leagues.py's StatAPI Protocol declares schedule() -> dict, but
            # statsapi.schedule() returns a list of games. Trust the runtime.
            scheduled = cast(list, league.statsapi.schedule(today, **league.schedule_params))
        except Exception:
            continue
        for entry in scheduled:
            game_id = entry["game_id"]
            cached = _live_games.get(game_id)
            if cached is not None:
                # Same object the board would hold, refreshed rather than rebuilt.
                cached.update(force=True)
                games.append(cached)
                continue
            game = data.game.Game.from_scheduled(
                {
                    "league": league,
                    "game_id": game_id,
                    "game_date": entry["game_date"],
                    "national_broadcasts": entry.get("national_broadcasts") or [],
                    "series_status": entry.get("series_status") or "",
                },
                config,
            )
            if game is not None:
                _live_games[game_id] = game
                games.append(game)
    return games


def _enable_all(values: dict) -> dict:
    """Copy of `values` with every `enabled: false` flipped on.

    Used for the preview only. Several elements ship switched off
    (atbat.pitch, atbat.play_result, teams.record...), so they render nothing
    and cannot be positioned by eye. This lets you see them without having to
    enable them for real and remember to switch them back.
    """
    import copy

    out = copy.deepcopy(values)

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("enabled") is False:
            node["enabled"] = True
        for child in node.values():
            walk(child)

    walk(out)
    return out


def _anchor_meta() -> dict:
    return dict(json.loads(ANCHORS_FILE.read_text()))


def _resolve(values: dict, keypath: str) -> Optional[dict]:
    node: Any = values
    for part in keypath.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _box_for(keypath, coords, meta, layout, board_width, chars):
    """Bounding box (x0, y0, x1, y1) in board pixels, or None if unplaceable.

    The anchor rules live here, in one place, rather than being reimplemented in
    the browser -- a second copy would drift from the renderer just as surely as
    a JS reimplementation of the board would.
    """
    anchor, extent = meta["anchor"], meta["extent"]
    x, y = coords.get("x"), coords.get("y")

    if extent in ("box", "image"):
        return (x, y, x + coords["width"] - 1, y + coords["height"] - 1)
    if extent in ("square", "diamond"):
        return (x, y, x + coords["size"], y + coords["size"])
    if extent == "vline":
        return (x, coords["y_start"], x, coords["y_end"])
    if extent == "squares":
        size, ys = coords["size"], coords["squares"]
        return (x, min(ys), x + size - 1, max(ys) + size - 1)
    if extent == "column" or anchor == "none":
        return None

    try:
        font = layout.font(keypath)
        fw, fh = font["size"]["width"], font["size"]["height"]
        # `y` is the BASELINE. The BDF cell sits from y - baseline down to
        # y + (height - baseline) - 1, so using the height alone puts the box a
        # pixel out at both ends. Measured against a real render: with the 7x13
        # font (baseline 11) the caps of "FINAL 14" land on y-9 .. y-1 inside a
        # cell of y-11 .. y+1.
        baseline = getattr(font["font"], "baseline", fh - 1)
    except Exception:
        fw, fh, baseline = 4, 6, 5

    if extent in ("arrow_up", "arrow_down"):
        size = layout.coords("inning.arrow")["size"]
        if extent == "arrow_up":
            return (x - size + 1, y, x + size - 1, y + size - 1)
        return (x - size + 1, y - size + 1, x + size - 1, y)

    top = y - baseline
    bottom = top + fh - 1

    if extent == "scroll":
        # A scroll window is a fixed clip region, so this one is exact.
        return (x, top, x + coords.get("width", board_width) - 1, bottom)

    w = max(1, chars) * fw
    if anchor == "center":
        x0 = x - w // 2
    elif anchor == "right":
        x0 = x - w + 1
    elif anchor == "board-right":
        x0 = board_width - w
    else:
        x0 = x
    return (x0, top, x0 + w - 1, bottom)


def elements(size: str, screen: str, coords: Optional[dict] = None) -> list[dict]:
    """Every element drawn on `screen`, with a bounding box for each.

    Only elements the screen actually draws are returned: the coordinates file
    holds all screens' elements at once, and showing them together is both
    unreadable and misleading -- it invites moving something into space that is
    only free on the screen you happen to be looking at.
    """
    m = _SIZE_RE.match(size)
    if not m:
        raise ValueError(f"bad size {size!r}, expected e.g. 'w128h64'")
    width, height = int(m.group(1)), int(m.group(2))

    _force_emulation()
    from data.config.layout import Layout

    meta_doc = _anchor_meta()
    anchors = meta_doc["anchors"]
    prefixes = meta_doc["screens"].get(screen)
    if prefixes is None:
        raise ValueError(f"unknown screen {screen!r}, expected one of {sorted(meta_doc['screens'])}")

    values = coords if coords is not None else _coords_for(size)
    layout = _layout_for(values, width, height)

    # `nohit` / `perfect_game` / `warmup` are not elements in their own right:
    # layout.coords() substitutes them for their parent when that state is
    # active. Resolve through the state exactly as the renderer does, so each
    # element appears once, in the position this screen will actually draw it.
    state = SCREENS[screen].state if screen in SCREENS else None
    previous_state = layout.state
    layout.set_state(state)

    try:
        out = []
        for keypath in sorted(anchors):
            if not any(keypath.startswith(p) for p in prefixes):
                continue
            if keypath.rsplit(".", 1)[-1] in _LAYOUT_STATES:
                continue  # an alternate position, reached via its parent
            node = _resolve(values, keypath)
            if node is None:
                continue

            # Where an edit should be written: the alternate when one is active.
            edit_keypath = keypath
            if state is not None and isinstance(node.get(state), dict):
                edit_keypath = f"{keypath}.{state}"
                node = node[state]

            meta = anchors[keypath]
            try:
                box = _box_for(keypath, node, meta, layout, width, NOMINAL_CHARS)
            except (KeyError, TypeError):
                continue
            if box is None:
                continue

            try:
                font = layout.font(keypath)
                font_info = {
                    "width": font["size"]["width"],
                    "height": font["size"]["height"],
                    "baseline": getattr(font["font"], "baseline", None),
                }
            except Exception:
                font_info = None

            out.append(
                {
                    "keypath": keypath,
                    "edit_keypath": edit_keypath,
                    "anchor": meta["anchor"],
                    "extent": meta["extent"],
                    "note": meta.get("note"),
                    "coords": node,
                    "box": list(box),
                    # Text width depends on live data, so the box is indicative only.
                    "dynamic": meta["extent"] == "text",
                    "enabled": node.get("enabled"),
                    "font": font_info,
                }
            )
    finally:
        layout.set_state(previous_state)

    # Things the board draws that own no coordinates. They get no box, but they
    # are listed so you can find them and see what governs them -- otherwise you
    # hunt the element list for an "ERA" that is never going to be there.
    for keypath, meta in sorted(meta_doc.get("derived", {}).items()):
        if screen not in meta.get("screens", []):
            continue
        toggle = meta.get("toggle") or {}
        owner = _resolve(values, toggle.get("keypath", "")) or {}
        out.append(
            {
                "keypath": keypath,
                "anchor": "derived",
                "extent": "derived",
                "label": meta.get("label"),
                "note": meta.get("note"),
                "controlled_by": meta.get("controlled_by", []),
                "toggle": toggle or None,
                "coords": {},
                "box": None,
                "dynamic": True,
                "enabled": owner.get(toggle.get("key")) if toggle else None,
                "font": None,
            }
        )

    return out


def render(
    size: str,
    screen: str,
    coords: Optional[dict] = None,
    show_disabled: bool = False,
    skip_banner: bool = False,
    game: Optional[Any] = None,
) -> bytes:
    """Render one screen of one board size. Returns PNG bytes at native resolution.

    `coords` overrides the on-disk coordinates without writing them, so the
    editor can show unsaved edits on the real board rather than only moving a
    box around over a stale image.

    `show_disabled` flips every `enabled: false` on for the render only, so an
    element that is switched off can still be seen and positioned. It never
    touches the coordinates the editor will save.

    `skip_banner` omits the team banner, which the board always draws last and
    therefore on top. Diffing a banner-less render against a full one reveals
    exactly which pixels the banner covers -- the failure that put "FINAL"
    underneath the home score. Used by tools/layout_monitor.py.
    """
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
    from data.scoreboard.inning import Inning
    from data.scoreboard.postgame import Postgame
    from data.scoreboard.pregame import Pregame
    from bullpen.time_formats import TIME_FORMAT_12H
    from renderers.games import irregular, postgame as postgamerender, pregame as pregamerender, teams
    from renderers.games import game as gamerender

    values = coords if coords is not None else _coords_for(size)
    if show_disabled:
        values = _enable_all(values)
    layout = _layout_for(values, width, height)
    colors = Color(_load_json(REPO / "colors" / "scoreboard.json", REPO / "colors" / "scoreboard.example.json"))
    team_colors = Color(_load_json(REPO / "colors" / "teams.json", REPO / "colors" / "teams.example.json"))

    spec = SCREENS[screen]
    # A caller can supply a real game; otherwise use this screen's canned one.
    game = game if game is not None else _build_game(spec.fixture)
    scoreboard = Scoreboard(game)

    # Derive the state from the game first, then let the variant override it.
    # The nohit/perfect_game/warmup sub-keys are alternate positions selected by
    # layout.state, so forcing the state is what makes those coordinates visible.
    layout.state_for_game(game)
    if spec.state is not None:
        layout.set_state(spec.state)

    if spec.inning is not None:
        scoreboard.inning.number = spec.inning

    if spec.inning_break:
        # The break branch keys off the half-inning state, not a layout state.
        scoreboard.inning.state = Inning.MIDDLE

    options = RGBMatrixOptions()
    options.cols, options.rows = width, height

    # The emulator's display adapter is a singleton: BaseAdapter.get_instance()
    # caches on the class and ignores its arguments, so the first size rendered
    # in a process would fix the frame geometry for every later one. Rendering
    # w128h32 then w128h64 handed back a 128x32 image -- silently wrong for both
    # the monitor's multi-size sweep and the editor's board dropdown. Drop the
    # cached instance so this size gets its own.
    options.display_adapter.INSTANCE = None

    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()

    bg = colors.color("default.background")
    canvas.Fill(bg["r"], bg["g"], bg["b"])

    # The play-by-play line keeps its scroll position in module globals so it can
    # animate across frames. That makes repeated renders of the same board drift,
    # which would show up as a phantom change every time the editor re-previews
    # and would make the monitor's change detection useless. Rewind it so every
    # render starts from the same frame.
    gamerender._play_desc_pos = None
    gamerender._play_desc_last = None
    gamerender._play_desc_finished = True

    # Mirrors MlbRenderer.__draw_game. text_pos is parked at the canvas width so
    # scrolling text renders at its start position rather than mid-scroll.
    text_pos = width
    if spec.base == "pregame":
        # Config.check_time_format() maps the config's "12h" onto a strftime token
        # before the renderer ever sees it; passing the raw string renders a
        # literal "12h:05" and, worse, the wrong text width for layout checks.
        pregame = Pregame(game, TIME_FORMAT_12H)
        pregamerender.render_pregame(canvas, layout, colors, pregame, text_pos, False, False, False)
    elif spec.base == "final":
        postgamerender.render_postgame(canvas, layout, colors, Postgame(game), scoreboard, text_pos, False, False)
    elif spec.base == "irregular":
        short_text = layout.coords("status.text")["short_text"]
        irregular.render_irregular_status(canvas, layout, colors, scoreboard, short_text, text_pos)
    else:
        gamerender.render_live_game(canvas, layout, colors, scoreboard, text_pos, 0)

    # Always last, so it paints over the screen content -- the fixed draw order.
    if not skip_banner:
        teams.render_team_banner(
            canvas,
            layout,
            team_colors,
            scoreboard.home_team,
            scoreboard.away_team,
            show_score=(spec.base != "pregame"),
            scoreboard_colors=colors,
        )

    matrix.SwapOnVSync(canvas)

    adapter = canvas.display_adapter
    image = adapter._get_masked_image(adapter._last_frame())
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
