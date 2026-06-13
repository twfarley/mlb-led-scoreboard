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

        # The grid packs a label + value into tiles only ~18px tall, so the
        # layout's default font (sized for game screens, e.g. 7x13) overflows
        # and the two lines collide. Use compact bundled fonts for the grid;
        # the powerwall layout has room, so it keeps the layout default.
        if config.layout_mode == "grid":
            self._value_font = self._grid_font("homeassistant.value_font", "5x7")
            self._label_font = self._grid_font("homeassistant.label_font", "4x6")
            self._scroll_font = self._grid_font("homeassistant.scroll_font", "5x7")
        else:
            self._value_font = layout.font("homeassistant.value_font")
            self._label_font = layout.font("homeassistant.label_font")
            self._scroll_font = layout.font("homeassistant.scroll_font")

        # Precompute the dimmed background image once: a flat list of
        # (x, y, r, g, b) for the pixels worth drawing.
        self._bg_pixels: list = self._load_background()

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

    def _grid_font(self, keypath: str, default_name: str) -> dict:
        """Font for a grid keypath, defaulting to a compact bundled font.

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
        # A grid with a charge bar wants a smooth refresh too; plain grids idle.
        if self.config.layout_mode == "powerwall":
            return self.config.scrolling_speed
        return 0.1 if self.config.charge_bar else 0.5

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
        return True

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
                h, m = divmod(max(0, int(round(mins))), 60)
                remaining = f"{h}h, {m}m" if h else f"{m}m"
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
        ents = self.config.entities
        ps = self.config.power_scale

        solar_kw = data.get_float(ents.get("solar", "")) * ps
        home_kw = data.get_float(ents.get("home", "")) * ps
        grid_kw = data.get_float(ents.get("grid", "")) * ps
        battery_kw = data.get_float(ents.get("battery", "")) * ps
        charge_pct = data.get_float(ents.get("charge", ""))

        # Tesla integration reports grid_power positive when importing.
        is_importing = grid_kw > 0.05
        is_charging = battery_kw < -0.05
        is_discharging = battery_kw > 0.05

        # Geometry scales to panel width; three columns centred on these x's.
        w, h = self.width, self.height
        solar_cx = w // 6
        home_cx = w // 2
        batt_cx = w - w // 6
        icon_y = 1
        icon_size = min(18, h // 3)

        self._sun(canvas, graphics, solar_cx, icon_y + icon_size // 2, icon_size // 2)
        self._house(canvas, graphics, home_cx, icon_y, icon_size)

        if is_importing:
            self._bolt(canvas, graphics, batt_cx, icon_y, icon_size)
        else:
            self._battery(canvas, graphics, batt_cx, icon_y, icon_size,
                          charge_pct, is_charging, is_discharging)

        # Flow dots between columns
        flow_y = icon_y + icon_size // 2
        gap_l = (solar_cx + home_cx) // 2
        gap_r = (home_cx + batt_cx) // 2
        if solar_kw > 0.05:
            self._flow(canvas, gap_l - 8, 16, flow_y, self._color("solar_flow_active"), True)
        else:
            self._flow_idle(canvas, gap_l - 8, 16, flow_y, self._color("solar_flow_idle"))
        if is_importing:
            self._flow(canvas, gap_r - 8, 16, flow_y, self._color("grid_flow"), False)
        elif is_discharging:
            self._flow(canvas, gap_r - 8, 16, flow_y, self._color("battery_flow"), False)

        # Values row
        val_y = icon_y + icon_size + self._value_font["size"]["height"] + 1
        self._draw_centered(canvas, graphics, f"{solar_kw:.1f}kW", self._value_font,
                            val_y, self._gcolor(graphics, "solar_value"), center_x=solar_cx)
        self._draw_centered(canvas, graphics, f"{home_kw:.1f}kW", self._value_font,
                            val_y, self._gcolor(graphics, "home_value"), center_x=home_cx)
        if is_importing:
            self._draw_centered(canvas, graphics, f"{grid_kw:.1f}kW", self._value_font,
                                val_y, self._gcolor(graphics, "grid_flow"), center_x=batt_cx)
        else:
            self._draw_centered(canvas, graphics, f"{int(round(charge_pct))}%", self._value_font,
                                val_y, self._gcolor(graphics, "battery_value"), center_x=batt_cx)

        # Optional scrolling footer: solar produced today
        solar_today_id = ents.get("solar_today", "")
        if solar_today_id and data.has(solar_today_id):
            kwh = data.get_float(solar_today_id)
            text = f"Solar Power Generated today: {kwh:.1f} kWh"
            sy = h - 1
            return scrolling_text(
                canvas, graphics, 0, sy, w, self._scroll_font,
                self._gcolor(graphics, "solar_value"),
                self._gcolor(graphics, "background"),
                text, scroll_pos, center=False, force_scroll=True,
            )
        return None

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

    # ── Icon primitives (code-drawn, scale with size) ────────────────────────

    def _sun(self, canvas, graphics, cx, cy, r):
        c = self._gcolor(graphics, "solar_icon")
        rgb = (c.red, c.green, c.blue)
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                self._px(canvas, cx + dx, cy + dy, rgb)
        for i in range(r - 2, r + 1):
            self._px(canvas, cx, cy - i, rgb)
            self._px(canvas, cx, cy + i, rgb)
            self._px(canvas, cx - i, cy, rgb)
            self._px(canvas, cx + i, cy, rgb)
        for d in (r - 2, r - 1):
            if d > 0:
                self._px(canvas, cx - d, cy - d, rgb)
                self._px(canvas, cx + d, cy - d, rgb)
                self._px(canvas, cx - d, cy + d, rgb)
                self._px(canvas, cx + d, cy + d, rgb)

    def _house(self, canvas, graphics, cx, iy, size):
        c = self._gcolor(graphics, "home_icon")
        half = size // 2
        peak_x = cx
        eave_y = iy + half
        base_y = iy + size - 1
        graphics.DrawLine(canvas, peak_x, iy, cx - half, eave_y, c)
        graphics.DrawLine(canvas, peak_x, iy, cx + half, eave_y, c)
        graphics.DrawLine(canvas, cx - half, eave_y, cx + half, eave_y, c)
        graphics.DrawLine(canvas, cx - half + 1, eave_y, cx - half + 1, base_y, c)
        graphics.DrawLine(canvas, cx + half - 1, eave_y, cx + half - 1, base_y, c)
        graphics.DrawLine(canvas, cx - half + 1, base_y, cx + half - 1, base_y, c)

    def _battery(self, canvas, graphics, cx, iy, size, charge_pct, charging, discharging):
        c = self._gcolor(graphics, "battery_icon")
        half = max(4, size // 2)
        left = cx - half // 2
        right = cx + half // 2
        top = iy + 2
        bottom = iy + size - 1
        # terminal
        graphics.DrawLine(canvas, cx - 1, iy, cx + 1, iy, c)
        # body
        graphics.DrawLine(canvas, left, top, right, top, c)
        graphics.DrawLine(canvas, left, bottom, right, bottom, c)
        graphics.DrawLine(canvas, left, top, left, bottom, c)
        graphics.DrawLine(canvas, right, top, right, bottom, c)
        # fill
        body_h = bottom - top - 1
        fill_h = int(round(max(0.0, min(100.0, charge_pct)) / 100.0 * body_h))
        fill_top = bottom - fill_h
        if charge_pct > 50:
            fill = self._color("battery_fill_high")
        elif charge_pct > 25:
            fill = self._color("battery_fill_mid")
        else:
            fill = self._color("battery_fill_low")
        wave_row = -1
        if (charging or discharging) and fill_h > 0:
            step = self._phase // 3 % fill_h
            wave_row = (bottom - 1 - step) if charging else (fill_top + step)
        fill_c = graphics.Color(*fill)
        wave_c = self._gcolor(graphics, "battery_wave")
        for row in range(fill_top, bottom):
            graphics.DrawLine(canvas, left + 1, row, right - 1, row,
                              wave_c if row == wave_row else fill_c)

    def _bolt(self, canvas, graphics, cx, iy, size):
        c = self._gcolor(graphics, "grid_icon")
        graphics.DrawLine(canvas, cx + 2, iy, cx - 2, iy + size // 2, c)
        graphics.DrawLine(canvas, cx - 2, iy + size // 2, cx + 2, iy + size // 2, c)
        graphics.DrawLine(canvas, cx + 2, iy + size // 2, cx - 2, iy + size - 1, c)

    def _flow(self, canvas, x_start, width, y, color, rightward):
        spacing, n = 4, width // 4
        for dot in range(n):
            pos = (self._phase // 2 + dot * spacing) % width
            px = x_start + (pos if rightward else width - 1 - pos)
            for dy in (-1, 0, 1):
                self._px(canvas, px, y + dy, color)

    def _flow_idle(self, canvas, x_start, width, y, color):
        for pos in range(1, width, 4):
            self._px(canvas, x_start + pos, y, color)

    def _px(self, canvas, x, y, rgb):
        if 0 <= x < self.width and 0 <= y < self.height:
            canvas.SetPixel(x, y, rgb[0], rgb[1], rgb[2])
