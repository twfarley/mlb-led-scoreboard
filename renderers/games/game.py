import time

from bullpen.util import scrolling_text

from data import status
from driver import graphics
from data.config.color import Color
from data.config.layout import Layout
from data.scoreboard import Scoreboard
from data.scoreboard.atbat import AtBat
from data.scoreboard.bases import Bases
from data.scoreboard.inning import Inning
from data.scoreboard.pitches import Pitches
from data.plays import PLAY_RESULTS

from renderers.games import nohitter


def render_live_game(canvas, layout: Layout, colors: Color, scoreboard: Scoreboard, text_pos, animation_time):
    pos = 0
    if not status.is_inning_break(scoreboard.inning.state):
        pos = _render_at_bat(
            canvas,
            layout,
            colors,
            scoreboard.atbat,
            text_pos,
            scoreboard.play_result,
            (animation_time // 6) % 2,
            scoreboard.pitches,
        )

        # Check if we're deep enough into a game and it's a no hitter or perfect game
        should_display_nohitter = layout.coords("nohitter")["innings_until_display"]
        if scoreboard.inning.number > should_display_nohitter:
            if layout.state_is_nohitter():
                nohitter.render_nohit_text(canvas, layout, colors)

        _render_count(canvas, layout, colors, scoreboard.pitches)
        _render_outs(canvas, layout, colors, scoreboard.outs)
        _render_bases(canvas, layout, colors, scoreboard.bases, scoreboard.homerun(), (animation_time % 16) // 5)

    else:
        # The inning indicator (number + blinking-half arrow) lives in the corner
        # display below; the break screen shows only the due-up batters here.
        pos = _render_due_up(canvas, layout, colors, scoreboard.atbat, text_pos)

    # Inning indicator (two stacked arrows + number) renders in both branches so
    # the upcoming-inning blink stays visible during a break.
    _render_inning_display(canvas, layout, colors, scoreboard.inning)

    return pos


# --------------- at-bat ---------------
def _render_at_bat(canvas, layout, colors, atbat: AtBat, text_pos, play_result, animation, pitches: Pitches):
    plength = __render_pitcher_text(canvas, layout, colors, atbat.pitcher, pitches, text_pos)
    __render_pitch_text(canvas, layout, colors, pitches)
    __render_pitch_count(canvas, layout, colors, pitches)
    results = list(PLAY_RESULTS.keys())
    if play_result in results and __should_render_play_result(play_result, layout):
        if animation:
            __render_play_result(canvas, layout, colors, play_result)
        return plength
    else:
        blength = __render_batter_text(canvas, layout, colors, atbat.batter, text_pos)
        return max(plength, blength)


def __should_render_play_result(play_result, layout):
    if "strikeout" in play_result:
        coords = layout.coords("atbat.strikeout")
    else:
        coords = layout.coords("atbat.play_result")
    return coords["enabled"]


def __render_play_result(canvas, layout, colors, play_result):
    if "strikeout" in play_result:
        color = colors.graphics_color("atbat.strikeout")
        coords = layout.coords("atbat.strikeout")
        font = layout.font("atbat.strikeout")
    else:
        color = colors.graphics_color("atbat.play_result")
        coords = layout.coords("atbat.play_result")
        font = layout.font("atbat.play_result")
    try:
        text = PLAY_RESULTS[play_result][coords["desc_length"].lower()]
    except KeyError:
        return
    graphics.DrawText(canvas, font["font"], coords["x"], coords["y"], color, text)


def __render_batter_text(canvas, layout, colors, batter, text_pos):
    coords = layout.coords("atbat.batter")
    color = colors.graphics_color("atbat.batter")
    font = layout.font("atbat.batter")
    bgcolor = colors.graphics_color("default.background")
    offset = coords.get("offset", 0)
    pos = scrolling_text(
        canvas,
        graphics,
        coords["x"] + font["size"]["width"] * 3,
        coords["y"],
        coords["width"],
        font,
        color,
        bgcolor,
        batter,
        text_pos + offset,
        center=False,
    )
    graphics.DrawText(canvas, font["font"], coords["x"], coords["y"], color, "AB:")
    return pos


def __render_pitcher_text(canvas, layout, colors, pitcher, pitches: Pitches, text_pos):
    coords = layout.coords("atbat.pitcher")
    color = colors.graphics_color("atbat.pitcher")
    font = layout.font("atbat.pitcher")
    bgcolor = colors.graphics_color("default.background")

    pitch_count = layout.coords("atbat.pitch_count")
    if pitch_count["enabled"] and pitch_count["append_pitcher_name"]:
        pitcher += f" ({pitches.pitch_count})"

    pos = scrolling_text(
        canvas,
        graphics,
        coords["x"] + font["size"]["width"] * 2,
        coords["y"],
        coords["width"],
        font,
        color,
        bgcolor,
        pitcher,
        text_pos,
        center=False,
    )
    graphics.DrawText(canvas, font["font"], coords["x"], coords["y"], color, "P:")
    return pos


def __render_pitch_text(canvas, layout, colors, pitches: Pitches):
    coords = layout.coords("atbat.pitch")
    color = colors.graphics_color("atbat.pitch")
    font = layout.font("atbat.pitch")
    if int(pitches.last_pitch_speed) and coords["enabled"]:
        mph = " "
        if coords["mph"]:
            mph = "mph "
        if coords["desc_length"].lower() == "long":
            pitch_text = str(pitches.last_pitch_speed) + mph + pitches.last_pitch_type_long
        elif coords["desc_length"].lower() == "short":
            pitch_text = str(pitches.last_pitch_speed) + mph + pitches.last_pitch_type
        else:
            pitch_text = ""
        graphics.DrawText(canvas, font["font"], coords["x"], coords["y"], color, pitch_text)


def __render_pitch_count(canvas, layout, colors, pitches: Pitches):
    coords = layout.coords("atbat.pitch_count")
    color = colors.graphics_color("atbat.pitch_count")
    font = layout.font("atbat.pitch_count")
    if coords["enabled"] and not coords["append_pitcher_name"]:
        pitch_count = f"{pitches.pitch_count}P"
        graphics.DrawText(canvas, font["font"], coords["x"], coords["y"], color, pitch_count)


# --------------- bases ---------------
def _render_bases(canvas, layout, colors, bases: Bases, home_run, animation):
    base_runners = bases.runners
    base_colors = []
    base_colors.append(colors.graphics_color("bases.1B"))
    base_colors.append(colors.graphics_color("bases.2B"))
    base_colors.append(colors.graphics_color("bases.3B"))

    base_px = []
    base_px.append(layout.coords("bases.1B"))
    base_px.append(layout.coords("bases.2B"))
    base_px.append(layout.coords("bases.3B"))

    for base in range(len(base_runners)):
        __render_base_outline(canvas, base_px[base], base_colors[base])

        # Fill in the base if there's currently a baserunner or cycle if theres a homer
        if base_runners[base] or (home_run and animation == base):
            __render_baserunner(canvas, base_px[base], base_colors[base])


def __render_base_outline(canvas, base, color):
    x, y = (base["x"], base["y"])
    size = base["size"]
    half = abs(size // 2)
    graphics.DrawLine(canvas, x + half, y, x, y + half, color)
    graphics.DrawLine(canvas, x + half, y, x + size, y + half, color)
    graphics.DrawLine(canvas, x + half, y + size, x, y + half, color)
    graphics.DrawLine(canvas, x + half, y + size, x + size, y + half, color)


def __render_baserunner(canvas, base, color):
    x, y = (base["x"], base["y"])
    size = base["size"]
    half = abs(size // 2)
    for offset in range(1, half + 1):
        graphics.DrawLine(canvas, x + half - offset, y + size - offset, x + half + offset, y + size - offset, color)
        graphics.DrawLine(canvas, x + half - offset, y + offset, x + half + offset, y + offset, color)


# --------------- count ---------------
def _render_count(canvas, layout, colors, pitches: Pitches):
    font = layout.font("batter_count")
    coords = layout.coords("batter_count")
    pitches_color = colors.graphics_color("batter_count")
    batter_count_text = "{}-{}".format(pitches.balls, pitches.strikes)
    graphics.DrawText(canvas, font["font"], coords["x"], coords["y"], pitches_color, batter_count_text)


# --------------- outs ---------------
def __out_colors(colors):
    outlines = []
    fills = []
    for i in range(3):
        color = colors.graphics_color(f"outs.{i+1}")
        outlines.append(color)
        try:
            color = colors.graphics_color(f"outs.fill.{i+1}")
        except KeyError:
            pass
        fills.append(color)
    return outlines, fills


def _render_outs(canvas, layout, colors, outs):
    out_px = []
    out_px.append(layout.coords("outs.1"))
    out_px.append(layout.coords("outs.2"))
    out_px.append(layout.coords("outs.3"))

    out_colors = []
    out_colors, fill_colors = __out_colors(colors)

    for out in range(len(out_px)):
        __render_out_circle(canvas, out_px[out], out_colors[out])
        # Fill in the circle if that out has occurred
        if outs.number > out:
            __fill_out_circle(canvas, out_px[out], fill_colors[out])


def __render_out_circle(canvas, out, color):
    x, y, size = (out["x"], out["y"], out["size"])

    graphics.DrawLine(canvas, x, y, x + size, y, color)
    graphics.DrawLine(canvas, x, y, x, y + size, color)
    graphics.DrawLine(canvas, x + size, y + size, x, y + size, color)
    graphics.DrawLine(canvas, x + size, y + size, x + size, y, color)


def __fill_out_circle(canvas, out, color):
    size = out["size"]
    x, y = (out["x"], out["y"])
    x += 1
    y += 1
    size -= 1
    for y_offset in range(size):
        graphics.DrawLine(canvas, x, y + y_offset, x + size - 1, y + y_offset, color)


# --------------- inning information ---------------
def _render_due_up(canvas, layout, colors, atbat: AtBat, text_pos):
    batter_font = layout.font("inning.break.due_up.leadoff")
    batter_color = colors.graphics_color("inning.break.due_up_names")

    leadoff = layout.coords("inning.break.due_up.leadoff")
    on_deck = layout.coords("inning.break.due_up.on_deck")
    in_hole = layout.coords("inning.break.due_up.in_hole")
    background = colors.graphics_color("default.background")

    p1 = scrolling_text(
        canvas,
        graphics,
        leadoff["x"],
        leadoff["y"],
        leadoff["width"],
        batter_font,
        batter_color,
        background,
        atbat.batter,
        text_pos,
        center=False,
    )
    p2 = scrolling_text(
        canvas,
        graphics,
        on_deck["x"],
        on_deck["y"],
        on_deck["width"],
        batter_font,
        batter_color,
        background,
        atbat.onDeck,
        text_pos,
        center=False,
    )
    p3 = scrolling_text(
        canvas,
        graphics,
        in_hole["x"],
        in_hole["y"],
        in_hole["width"],
        batter_font,
        batter_color,
        background,
        atbat.inHole,
        text_pos,
        center=False,
    )

    due_font = layout.font("inning.break.due_up.due")
    due_color = colors.graphics_color("inning.break.due_up")

    due = layout.coords("inning.break.due_up.due")
    up = layout.coords("inning.break.due_up.up")
    graphics.DrawText(canvas, due_font["font"], due["x"], due["y"], due_color, "Due")
    graphics.DrawText(canvas, due_font["font"], up["x"], up["y"], due_color, "Up:")

    divider = layout.coords("inning.break.due_up.divider")
    if divider["draw"]:
        graphics.DrawLine(
            canvas,
            divider["x"],
            divider["y_start"],
            divider["x"],
            divider["y_end"],
            colors.graphics_color("inning.break.due_up_divider"),
        )

    return max(p1, p2, p3)


def _render_inning_display(canvas, layout, colors, inning: Inning):
    __render_inning_arrows(canvas, layout, colors, inning)
    __render_inning_number(canvas, layout, colors, inning)


def __render_inning_arrows(canvas, layout, colors, inning: Inning):
    arrow_coords = layout.coords("inning.arrow")
    try:
        up = layout.coords("inning.arrow.up")
        down = layout.coords("inning.arrow.down")
        active = colors.graphics_color("inning.arrow.active")
        inactive = colors.graphics_color("inning.arrow.inactive")
    except KeyError:
        return
    size = arrow_coords["size"]

    if status.is_inning_break(inning.state):
        # Blink the upcoming half-inning at 1 Hz: Middle -> next is Bottom (down);
        # End -> next inning's Top (up).
        upcoming_is_top = inning.state == Inning.END
        blink_on = int(time.time()) % 2 == 0
        up_color = (active if blink_on else inactive) if upcoming_is_top else inactive
        down_color = inactive if upcoming_is_top else (active if blink_on else inactive)
    else:
        is_top = inning.state == Inning.TOP
        up_color = active if is_top else inactive
        down_color = inactive if is_top else active

    # Up arrow: tip at (x, y), grows downward
    for offset in range(size):
        graphics.DrawLine(canvas, up["x"] - offset, up["y"] + offset, up["x"] + offset, up["y"] + offset, up_color)
    # Down arrow: tip at (x, y), grows upward
    for offset in range(size):
        graphics.DrawLine(
            canvas, down["x"] - offset, down["y"] - offset, down["x"] + offset, down["y"] - offset, down_color
        )


def __render_inning_number(canvas, layout, colors, inning: Inning):
    coords = layout.coords("inning.number")
    font = layout.font("inning.number")
    color = colors.graphics_color("inning.number")
    num_str = str(inning.number)
    pos_x = coords["x"] - len(num_str) * font["size"]["width"]
    graphics.DrawText(canvas, font["font"], pos_x, coords["y"], color, num_str)
