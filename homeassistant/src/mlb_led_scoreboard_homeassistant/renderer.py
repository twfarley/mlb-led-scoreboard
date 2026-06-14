"""Renders a Home Assistant dashboard onto the LED matrix.

Two layouts are supported:

* ``grid``      – a generic grid of label/value tiles, one per configured entity.
* ``powerwall`` – a Tesla-style solar/home/battery energy-flow screen, sourced
                  from Home Assistant entities instead of the gateway directly.

The renderer draws everything in code and falls back to the layout's default
font, so it works on any panel size without requiring edits to the scoreboard's
``coordinates`` or ``colors`` files. Any colour can still be overridden by
adding a ``homeassistant.*`` key to ``colors/scoreboard.json``.
"""

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import bullpen.api as api
from bullpen.logging import LOGGER
from bullpen.util import center_text_position, scrolling_text

from .config import Config
from .data import HomeAssistantData

if TYPE_CHECKING:
    from RGBMatrixEmulator.emulation.canvas import Canvas


# ── Default palette (RGB) ────────────────────────────────────────────────────
_DEFAULTS = {
    "background": (0, 0, 0),
    "title": (140, 140, 140),
    "label": (120, 120, 120),
    "value": (255, 255, 255),
    "solar_icon": (255, 200, 40),
    "home_icon": (90, 170, 255),
    "battery_icon": (120, 220, 120),
    "battery_fill_high": (80, 220, 100),
    "battery_fill_mid": (235, 200, 60),
    "battery_fill_low": (230, 90, 70),
    "battery_wave": (255, 255, 255),
    "battery_arrow": (255, 255, 255),
    "grid_icon": (235, 90, 70),
    "solar_flow_active": (255, 200, 40),
    "solar_flow_idle": (60, 60, 60),
    "battery_flow": (120, 220, 120),
    "grid_flow": (235, 90, 70),
    "solar_value": (255, 200, 40),
    "home_value": (90, 170, 255),
    "battery_value": (120, 220, 120),
    # Energy-flow layout (Tesla-app style)
    "flow": (80, 220, 100),        # solar / battery / clean power
    "flow_grid": (235, 150, 40),   # power drawn from the grid
    "flow_idle": (45, 45, 45),     # inactive line
    "meter": (150, 150, 150),      # centre meter icon
}


class Renderer(api.PluginRenderer["HomeAssistantData"]):
    def __init__(self, config: Config, layout: api.Layout, colors: api.Color) -> None:
        self.config = config
        self.layout = layout
        self.colors = colors
        # Animation phase is advanced by wall-clock time (see _render_powerwall)
        # so the flow-dot/battery-wave speed stays constant regardless of the
        # frame rate. _ANIM_RATE is phase-units per second; 10 matches the look
        # of the old fixed 0.1s-per-frame cadence.
        self._phase = 0
        self._phase_accum = 0.0
        self._last_anim_t: Optional[float] = None

        self.width = getattr(layout, "width", 64)
        self.height = getattr(layout, "height", 32)

        # Both layouts pack a lot into a small panel, so use compact bundled
        # fonts rather than the layout's large default (sized for game screens,
        # e.g. 7x13). An explicit homeassistant.*_font coordinate override still
        # wins if present.
        self._value_font = self._compact_font("homeassistant.value_font", "5x7")
        self._label_font = self._compact_font("homeassistant.label_font", "4x6")
        self._scroll_font = self._compact_font("homeassistant.scroll_font", "5x7")

        # Precompute the dimmed background image once: a flat list of
        # (x, y, r, g, b) for the pixels worth drawing.
        self._bg_pixels: list = self._load_background()
        # Code-drawn house behind the powerwall energy-flow screen, used only
        # when no background image is configured. Precomputed once.
        self._house_pixels: list = (
            self._build_house() if config.layout_mode == "powerwall" else []
        )

    def _load_background(self) -> list:
        name = self.config.background_image
        if not name:
            return []
        path = name if os.path.isfile(name) else str(Path(__file__).parent / "icons" / name)
        if not os.path.isfile(path):
            LOGGER.warning("[HOMEASSISTANT] Background image not found: %s", name)
            return []
        try:
            from PIL import Image
        except ImportError:
            LOGGER.warning("[HOMEASSISTANT] Pillow not installed; skipping background image")
            return []

        opacity = max(0.0, min(1.0, self.config.background_opacity))
        img = Image.open(path).convert("RGBA").resize((self.width, self.height), Image.LANCZOS)
        pixels = []
        for y in range(self.height):
            for x in range(self.width):
                r, g, b, a = img.getpixel((x, y))
                f = (a / 255.0) * opacity      # alpha-weighted, composited over black
                rr, gg, bb = round(r * f), round(g * f), round(b * f)
                if rr or gg or bb:
                    pixels.append((x, y, rr, gg, bb))
        return pixels

    def _draw_background(self, canvas) -> None:
        for x, y, r, g, b in self._bg_pixels:
            canvas.SetPixel(x, y, r, g, b)

    def _compact_font(self, keypath: str, default_name: str) -> dict:
        """Font for a keypath, defaulting to a compact bundled font.

        Honours an explicit ``font_name`` override in the coordinates file if
        one exists; otherwise loads ``default_name`` directly rather than
        falling back to the layout's (larger) default. Falls back to the public
        ``layout.font`` only if the named font can't be loaded.
        """
        try:
            name = self.layout.coords(keypath)["font_name"]
        except Exception:
            name = default_name
        loader = getattr(self.layout, "_Layout__get_font_object", None)
        font = loader(name) if loader is not None else None
        return font or self.layout.font(keypath)

    # ── bullpen API ──────────────────────────────────────────────────────────

    _ANIM_RATE = 10.0  # animation phase-units per second

    def wait_time(self) -> float:
        # The powerwall footer scrolls 1px per frame, so the frame budget sets
        # the scroll speed. Pace it at the scoreboard's configured scrolling
        # speed so the two match; the flow animation is time-based and stays put.
        # Powerwall (flow dots) and a charge-bar grid (sweep) want a smooth
        # refresh; their animation is time-based. Plain grids can idle.
        if self.config.layout_mode == "powerwall" or self.config.charge_bar:
            return 0.1
        return 0.5

    def _advance_phase(self) -> None:
        # Advance the animation phase by wall-clock time so flow dots, battery
        # waves and the charge-bar sweep keep a constant speed regardless of
        # the frame rate.
        now = time.monotonic()
        dt = 0.0 if self._last_anim_t is None else now - self._last_anim_t
        self._last_anim_t = now
        self._phase_accum = (self._phase_accum + dt * self._ANIM_RATE) % 1000
        self._phase = int(self._phase_accum)

    def can_render(self, data: HomeAssistantData) -> bool:
        # A `show_when`-gated dashboard is skipped in the rotation unless one of
        # its gate entities reads an active state (e.g. only show the pool screen
        # while a pump is running). Data keeps refreshing while hidden, so the
        # screen reappears on its own once the gate flips.
        sw = self.config.show_when
        if not sw:
            return True
        for eid in sw["entities"]:
            ent = data.get(eid)
            if ent is not None and ent.state.strip().lower() in sw["states"]:
                return True
        return False

    def reset(self):
        pass  # keep animation phase continuous across rotations

    def render(
        self,
        data: HomeAssistantData,
        canvas: "Canvas",
        graphics: api.renderer.graphics,
        scrolling_text_pos: int,
    ) -> Optional[int]:
        bg = self._color("background")
        canvas.Fill(*bg)
        self._draw_background(canvas)
        self._advance_phase()

        if not data.available:
            return self._render_offline(canvas, graphics, scrolling_text_pos)

        if self.config.layout_mode == "powerwall":
            return self._render_powerwall(data, canvas, graphics, scrolling_text_pos)
        return self._render_grid(data, canvas, graphics, scrolling_text_pos)

    # ── Colour helper ────────────────────────────────────────────────────────

    def _color(self, name: str) -> tuple[int, int, int]:
        """RGB tuple for ``name``, preferring a ``homeassistant.<name>`` colour
        key if the user defined one, else the built-in default."""
        try:
            c = self.colors.graphics_color(f"homeassistant.{name}")
            return (c.red, c.green, c.blue)
        except Exception:
            return _DEFAULTS.get(name, (255, 255, 255))

    def _gcolor(self, graphics, name: str):
        return graphics.Color(*self._color(name))

    # ── Offline notice ───────────────────────────────────────────────────────

    def _render_offline(self, canvas, graphics, scroll_pos) -> Optional[int]:
        msg = "Home Assistant unavailable"
        y = self.height // 2 + 3
        return scrolling_text(
            canvas, graphics, 0, y, self.width, self._scroll_font,
            graphics.Color(200, 60, 60), self._gcolor(graphics, "background"),
            msg, scroll_pos, center=True,
        )

    # ── Grid layout ──────────────────────────────────────────────────────────

    def _render_grid(self, data, canvas, graphics, scroll_pos) -> Optional[int]:
        # Tiles flagged hide_when_unavailable drop out (and free their slot)
        # when their entity has no data — e.g. a charge-ETA tile that only
        # appears while the car is charging.
        tiles = [t for t in self.config.tiles if self._tile_visible(data, t)]
        cols = max(1, self.config.columns)

        title_h = 0
        if self.config.title:
            self._draw_centered(canvas, graphics, self.config.title,
                                 self._label_font, 6, self._gcolor(graphics, "title"))
            title_h = 8

        # Reserve a bottom strip for the charge bar, if configured.
        strip_h = 22 if self.config.charge_bar else 0

        if not tiles:
            if self.config.charge_bar:
                self._render_charge_strip(canvas, graphics, data, self.height - strip_h, strip_h)
            return None

        rows = (len(tiles) + cols - 1) // cols
        cell_w = self.width // cols
        avail_h = self.height - title_h - strip_h
        cell_h = max(1, avail_h // rows)

        # Center the label+value pair vertically within each cell so the grid
        # reads well whether cells are tight (many tiles) or roomy (few tiles).
        lh = self._label_font["size"]["height"]
        vh = self._value_font["size"]["height"]
        gap = 2
        pad = max(0, (cell_h - (lh + gap + vh)) // 2)
        for i, tile in enumerate(tiles):
            col = i % cols
            row = i // cols
            cx = col * cell_w + cell_w // 2
            top = title_h + row * cell_h
            label_baseline = top + pad + lh
            value_baseline = label_baseline + gap + vh

            # Label (HA friendly_name unless overridden)
            label = tile.label or self._friendly(data, tile.entity)
            if label:
                self._draw_centered(canvas, graphics, label, self._label_font,
                                    label_baseline, self._gcolor(graphics, "label"),
                                    center_x=cx, color_override=tile.label_color)

            # Value
            value_text = self._format_value(data, tile)
            value_color = (graphics.Color(*tile.color) if tile.color
                           else self._gcolor(graphics, "value"))
            self._draw_centered(canvas, graphics, value_text, self._value_font,
                                value_baseline, value_color, center_x=cx)

        if self.config.charge_bar:
            self._render_charge_strip(canvas, graphics, data, self.height - strip_h, strip_h)
        return None

    def _render_charge_strip(self, canvas, graphics, data, top, h) -> None:
        cb = self.config.charge_bar
        ent = data.get(cb["active_entity"])
        state = (ent.state if ent else "").strip()
        s = state.lower()
        charging = bool(s) and not s.startswith("not") and any(a in s for a in cb["active_states"])

        cx = self.width // 2
        if not charging:
            # Idle: just the charging status, centered (e.g. "Disconnected").
            text = state.replace("_", " ").title() or "—"
            self._draw_centered(canvas, graphics, text, self._value_font,
                                top + h // 2 + 3, self._gcolor(graphics, "value"), center_x=cx)
            return

        # Charging: time-remaining text above an animated horizontal battery bar.
        eta = data.get(cb["eta_entity"]) if cb["eta_entity"] else None
        text = "Charging"
        if eta is not None and eta.state.strip().lower() not in self._NO_DATA_STATES:
            mins = self._eta_minutes(eta.state.strip())
            if mins is not None:
                hrs, mins_rem = divmod(max(0, int(round(mins))), 60)
                remaining = f"{hrs}h, {mins_rem}m" if hrs else f"{mins_rem}m"
                text = f'{cb["eta_prefix"]}{remaining}'
            else:
                text = eta.state.strip()  # unparseable — show whatever HA gave
        self._draw_centered(canvas, graphics, text, self._value_font,
                            top + self._value_font["size"]["height"],
                            self._gcolor(graphics, "value"), center_x=cx)

        level = max(0.0, min(100.0, data.get_float(cb["level_entity"])))
        y1 = top + h - 2
        self._hbar(canvas, graphics, 4, y1 - 6, self.width - 6, y1, level)

    @staticmethod
    def _eta_minutes(raw: str):
        """Minutes until charge completion, parsed from any of the forms HA
        hands us, or None if unrecognised:

        * ISO completion timestamp (Tesla time_to_full_charge) -> now until then
        * a leading duration like "25h 51m" / "1H 9M" / "51m" (golf charge_eta)
        * a plain number of hours
        """
        s = raw.strip()
        # ISO datetime -> remaining from now
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
            return (dt - now).total_seconds() / 60.0
        except ValueError:
            pass
        # leading "<h>h <m>m" duration
        import re
        m = re.match(r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", s, re.I)
        if m and (m.group(1) or m.group(2)):
            return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
        # plain number of hours
        try:
            return float(s) * 60.0
        except ValueError:
            return None

    def _hbar(self, canvas, graphics, x0, y0, x1, y1, pct) -> None:
        """Horizontal battery-style progress bar filled to pct (0..100)."""
        outline = self._gcolor(graphics, "battery_icon")
        graphics.DrawLine(canvas, x0, y0, x1, y0, outline)
        graphics.DrawLine(canvas, x0, y1, x1, y1, outline)
        graphics.DrawLine(canvas, x0, y0, x0, y1, outline)
        graphics.DrawLine(canvas, x1, y0, x1, y1, outline)
        midy = (y0 + y1) // 2
        graphics.DrawLine(canvas, x1 + 1, midy - 1, x1 + 1, midy + 1, outline)  # terminal nub

        if pct > 50:
            fill = self._color("battery_fill_high")
        elif pct > 25:
            fill = self._color("battery_fill_mid")
        else:
            fill = self._color("battery_fill_low")
        inner_w = (x1 - 1) - (x0 + 1)
        fill_w = int(round(pct / 100.0 * inner_w))
        # animated highlight column sweeping rightward through the fill
        wave_col = (x0 + 1 + int(self._phase) // 2 % fill_w) if fill_w > 0 else -1
        fill_c = graphics.Color(*fill)
        wave_c = self._gcolor(graphics, "battery_wave")
        for col in range(x0 + 1, x0 + 1 + fill_w):
            graphics.DrawLine(canvas, col, y0 + 1, col, y1 - 1,
                              wave_c if col == wave_col else fill_c)

    # States that mean "no data" — used to hide hide_when_unavailable tiles.
    _NO_DATA_STATES = {"unknown", "unavailable", "none", "", "—", "–", "-"}

    def _tile_visible(self, data, tile) -> bool:
        if not tile.hide_when_unavailable:
            return True
        ent = data.get(tile.entity)
        if ent is None:
            return False
        return ent.state.strip().lower() not in self._NO_DATA_STATES

    def _format_value(self, data, tile) -> str:
        ent = data.get(tile.entity)
        if ent is None:
            return "—"
        num = ent.as_float()
        if num is None:
            # non-numeric state (e.g. "home", "on") — title-case it
            return ent.state.replace("_", " ").title()
        num *= tile.scale
        if tile.decimals <= 0:
            body = f"{int(round(num))}"
        else:
            body = f"{num:.{tile.decimals}f}"
        unit = tile.unit if tile.unit is not None else (ent.unit or "")
        return f"{body}{unit}"

    def _friendly(self, data, entity_id: str) -> str:
        ent = data.get(entity_id)
        if ent and ent.friendly_name:
            return ent.friendly_name
        return entity_id.split(".", 1)[-1].replace("_", " ").title()

    # ── Powerwall layout ─────────────────────────────────────────────────────

    def _render_powerwall(self, data, canvas, graphics, scroll_pos) -> Optional[int]:
        """Tesla-app-style energy-flow screen: four corner readings around a
        centre meter, joined by flow lines that animate toward whatever is
        consuming power. Green = solar/battery/clean, orange = grid."""
        # House sits behind everything (unless a background image is set).
        if not self._bg_pixels:
            for x, y, rgb in self._house_pixels:
                self._px(canvas, x, y, rgb)

        ents = self.config.entities
        ps = self.config.power_scale

        solar = data.get_float(ents.get("solar", "")) * ps
        home = data.get_float(ents.get("home", "")) * ps
        grid = data.get_float(ents.get("grid", "")) * ps
        battery = data.get_float(ents.get("battery", "")) * ps
        charge = data.get_float(ents.get("charge", ""))

        # Sign conventions: grid +import, battery -charging / +discharging.
        importing = grid > 0.05
        exporting = grid < -0.05
        charging = battery < -0.05
        discharging = battery > 0.05

        w, h = self.width, self.height
        green = self._color("flow")
        orange = self._color("flow_grid")
        art = bool(self._bg_pixels)

        def pt(fx, fy):
            return (round(w * fx), round(h * fy))

        cx, cy = w // 2, h // 2
        if art:
            # Flow segments hand-aligned to the house art's lead lines. Each is
            # (meter-end, element-end); the art already draws the lines and the
            # meter, so we only animate dots on top (base=False).
            def A(x, y):
                return (round(x * w / 128), round(y * h / 64))
            sol_m, sol_e = A(68, 40), A(68, 32)
            hom_m, hom_e = A(70, 43), A(80, 40)
            grd_m, grd_e = A(69, 51), A(92, 58)
            pw_m, pw_e = A(67, 43), A(53, 45)
        else:
            sol_m = hom_m = grd_m = pw_m = (cx, cy)
            sol_e, hom_e = pt(0.27, 0.30), pt(0.73, 0.30)
            pw_e, grd_e = pt(0.27, 0.70), pt(0.73, 0.70)

        base = not art
        # Solar: panels -> meter while producing.
        self._flow_line(canvas, sol_e, sol_m, green, solar > 0.05, base)
        # Home: meter -> house (orange if the grid is feeding it).
        self._flow_line(canvas, hom_m, hom_e, orange if importing else green, home > 0.05, base)
        # Powerwall: meter -> battery when charging, battery -> meter when discharging.
        if charging:
            self._flow_line(canvas, pw_m, pw_e, green, True, base)
        elif discharging:
            self._flow_line(canvas, pw_e, pw_m, green, True, base)
        else:
            self._flow_line(canvas, pw_e, pw_m, green, False, base)
        # Grid: grid -> meter when importing (orange), meter -> grid when exporting.
        if importing:
            self._flow_line(canvas, grd_e, grd_m, orange, True, base)
        elif exporting:
            self._flow_line(canvas, grd_m, grd_e, green, True, base)
        else:
            self._flow_line(canvas, grd_e, grd_m, orange, False, base)

        if not art:
            self._meter(canvas, cx, cy)

        # Readings: white value over a grey label, like the app.
        white = self._gcolor(graphics, "value")

        def kw(v: float) -> str:
            return "0kW" if abs(v) < 0.05 else f"{abs(v):.1f}kW"

        if art:
            self._reading(canvas, graphics, round(w * 0.47), 6, kw(solar), "Solar", white)
            self._reading(canvas, graphics, round(w * 0.85), round(h * 0.50), kw(home), "Home", white)
            self._reading(canvas, graphics, round(w * 0.17), round(h * 0.80),
                          kw(battery), f"PW {charge:.0f}%", white)
            self._reading(canvas, graphics, round(w * 0.81), round(h * 0.80), kw(grid), "Grid", white)
        else:
            self._reading(canvas, graphics, sol_t[0], 7, kw(solar), "Solar", white)
            self._reading(canvas, graphics, hom_t[0], 7, kw(home), "Home", white)
            self._reading(canvas, graphics, pw_t[0], h - 13,
                          f"{kw(battery)} {charge:.0f}%", "Powerwall", white)
            self._reading(canvas, graphics, grd_t[0], h - 13, kw(grid), "Grid", white)
        return None

    def _reading(self, canvas, graphics, cx, value_y, value, label, vcolor) -> None:
        self._draw_centered(canvas, graphics, value, self._value_font, value_y,
                            vcolor, center_x=cx)
        self._draw_centered(canvas, graphics, label, self._label_font,
                            value_y + self._label_font["size"]["height"] + 1,
                            self._gcolor(graphics, "label"), center_x=cx)

    def _flow_line(self, canvas, a, b, color, active: bool, base: bool = True) -> None:
        """Animate dots from a toward b. When ``base`` is set, also draw a faint
        static line underneath (skipped when a background image already provides
        the lead lines, e.g. the Tesla house art)."""
        x0, y0 = a
        x1, y1 = b
        steps = max(abs(x1 - x0), abs(y1 - y0))
        if steps <= 0:
            return
        pts = [(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
               for i in range(steps + 1)]
        if base:
            idle = self._color("flow_idle")
            for x, y in pts:
                self._px(canvas, x, y, idle)
        if not active:
            return
        spacing = 4
        head = int(self._phase) % spacing
        for i, (x, y) in enumerate(pts):
            if (i - head) % spacing == 0:
                self._px(canvas, x, y, color)

    def _meter(self, canvas, cx, cy) -> None:
        c = self._color("meter")
        for x in range(cx - 3, cx + 4):
            self._px(canvas, x, cy - 4, c)
            self._px(canvas, x, cy + 4, c)
        for y in range(cy - 4, cy + 5):
            self._px(canvas, cx - 3, y, c)
            self._px(canvas, cx + 3, y, c)
        self._px(canvas, cx, cy, c)

    @staticmethod
    def _pip(x, y, poly) -> bool:
        """Point-in-polygon (ray casting)."""
        inside = False
        n = len(poly)
        j = n - 1
        for i in range(n):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
            j = i
        return inside

    def _build_house(self) -> list:
        """A dim isometric solar-roof house, matching the Tesla app's
        composition: solar roof up top, walls below, drawn once and blitted
        behind the energy-flow readings. Colours are deliberately dark so the
        readings and flow lines stay legible on top."""
        w, h = self.width, self.height
        sx, sy = w / 128.0, h / 64.0

        def P(x, y):
            return (x * sx, y * sy)

        # Isometric cuboid: roof rhombus on top, two wall faces below.
        A, B, F, L = P(64, 9), P(105, 26), P(64, 43), P(23, 26)   # roof corners
        B2, F2, L2 = P(105, 41), P(64, 58), P(23, 41)             # wall bottoms
        roof, rface, lface = [A, B, F, L], [F, B, B2, F2], [L, F, F2, L2]

        px: dict = {}

        def fill(poly, color):
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            for yy in range(int(min(ys)), int(max(ys)) + 1):
                for xx in range(int(min(xs)), int(max(xs)) + 1):
                    if self._pip(xx, yy, poly):
                        px[(xx, yy)] = color

        fill(rface, (28, 34, 50))   # right wall (lighter)
        fill(lface, (18, 24, 38))   # left wall (shaded)
        fill(roof, (13, 19, 33))    # solar roof base

        def lerp(p, q, t):
            return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)

        def line(p, q, color):
            steps = int(max(abs(q[0] - p[0]), abs(q[1] - p[1]))) or 1
            for i in range(steps + 1):
                pt = lerp(p, q, i / steps)
                key = (round(pt[0]), round(pt[1]))
                if key in px:
                    px[key] = color

        # Solar panel grid on the roof (lines parallel to each pair of edges).
        grid_c = (40, 54, 78)
        for t in (0.2, 0.4, 0.6, 0.8):
            line(lerp(A, B, t), lerp(L, F, t), grid_c)
        for t in (0.33, 0.66):
            line(lerp(A, L, t), lerp(B, F, t), grid_c)

        # A couple of lit windows on the right wall.
        win = (84, 88, 62)
        for (a, b) in ((P(73, 46), P(80, 53)), (P(86, 43), P(93, 50))):
            for yy in range(int(a[1]), int(b[1]) + 1):
                for xx in range(int(a[0]), int(b[0]) + 1):
                    if (xx, yy) in px:
                        px[(xx, yy)] = win

        return [(x, y, c) for (x, y), c in px.items()]

    # ── Text helper ──────────────────────────────────────────────────────────

    def _draw_centered(self, canvas, graphics, text, font, baseline_y, color,
                       center_x=None, color_override=None, **_):
        if center_x is None:
            center_x = self.width // 2
        if color_override is not None:
            color = graphics.Color(*color_override)
        char_w = font["size"]["width"]
        x = center_text_position(text, center_x, char_w)
        graphics.DrawText(canvas, font["font"], x, baseline_y, color, text)

    # ── Pixel primitive ──────────────────────────────────────────────────────

    def _px(self, canvas, x, y, rgb):
        if 0 <= x < self.width and 0 <= y < self.height:
            canvas.SetPixel(x, y, rgb[0], rgb[1], rgb[2])
