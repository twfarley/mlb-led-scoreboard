"""Inning-by-inning line score drawn across the team banner.

This draws *after* `teams.render_team_banner`, not inside the game renderer: the
banner paints the colour bands last so it is always on top, and these digits sit
on those bands. Drawing earlier would simply be painted over. It also borrows the
banner's per-team text colour, so the numbers stay legible on either team's
background without a second palette to keep in sync.
"""

from data import status
from data.scoreboard.inning import Inning
from driver import graphics


def enabled(layout, key):
    """True when the layout wants a line score and says where to put it.

    One switch, `teams.line_score.show_innings`, sits with the other display
    toggles rather than on the blocks themselves -- those carry only geometry,
    and two switches for one feature is a trap. The per-screen block still has
    to exist, so a board size that has not been given one draws nothing.

    Callers ask this before drawing anything the line score would replace: the
    banner's score and the season records report the same numbers, and the
    break screen's furniture wants the same pixels.
    """
    try:
        if not layout.coords("teams.line_score").get("show_innings", False):
            return False
    except KeyError:
        return False
    return __placement(layout, key) is not None


def render_line_score(canvas, layout, colors, team_colors, scoreboard, key):
    """Draw the line score described by the coordinate block at `key`.

    `key` selects which screen's block to use -- "inning.break.line_score" or
    "final.line_score" -- so the two screens can place it differently while
    sharing this code. Absent or disabled, nothing is drawn.
    """
    if not enabled(layout, key):
        return
    coords = layout.coords(key)

    frames = scoreboard.line_score
    font = layout.font(key)
    label_font = layout.font("{}.label".format(key))
    label_color = colors.graphics_color("line_score.label")

    columns = __columns(coords, label_font, len(frames))

    # Extra innings push the oldest columns off the left rather than squeezing
    # more of them in: five legible columns beat fourteen unreadable ones, and
    # the recent innings are the ones worth reading.
    start = max(0, len(frames) - columns)
    window = frames[start : start + columns]

    # Headers fill the grid even before the innings under them are played --
    # a line score is a shape you read the game into, not a list that grows.
    __render_labels(canvas, coords, columns, label_font, label_color, start)

    for side, team in (("away", scoreboard.away_team), ("home", scoreboard.home_team)):
        # lookup_color hands back the raw {r, g, b} dict the colour files store,
        # the same as the banner does before drawing the team name.
        palette = team.lookup_color(team_colors)
        rgb = palette["text"]
        text_color = graphics.Color(rgb["r"], rgb["g"], rgb["b"])
        __extend_band(canvas, layout, coords, side, palette["home"])
        row_y = coords[side]["y"]
        runs = [frame[0 if side == "away" else 1] for frame in window]

        __render_separator(canvas, coords, row_y, text_color)
        __render_innings(canvas, coords, columns, font, row_y, text_color, runs)
        __render_totals(canvas, coords, font, row_y, text_color, team)

    __render_current_inning(canvas, colors, coords, columns, scoreboard, start)


def __render_labels(canvas, coords, columns, font, color, start):
    """Column headers: the real inning numbers, then R / H / E."""
    y = coords["label"]["y"]
    width = font["size"]["width"]

    for offset in range(columns):
        # The window slides in extra innings, so these are absolute inning
        # numbers -- a 12th-inning game reads 8..12, not 1..5.
        number = str(start + offset + 1)
        x = __centered(number, __inning_center(coords, columns, offset), width)
        graphics.DrawText(canvas, font["font"], x, y, color, number)

    for index, label in enumerate(("R", "H", "E")):
        x = __centered(label, __total_center(coords, index), width)
        graphics.DrawText(canvas, font["font"], x, y, color, label)


def __render_innings(canvas, coords, columns, font, y, color, runs):
    width = font["size"]["width"]
    for offset, scored in enumerate(runs):
        # None means that half was never played -- a walk-off, or the bottom of
        # an inning still in progress. Blank is honest; a nought is not.
        if scored is None:
            continue
        text = str(scored)
        graphics.DrawText(
            canvas,
            font["font"],
            __centered(text, __inning_center(coords, columns, offset), width),
            y,
            color,
            text,
        )


def __render_totals(canvas, coords, font, y, color, team):
    width = font["size"]["width"]
    for index, total in enumerate((team.runs, team.hits, team.errors)):
        text = str(total)
        graphics.DrawText(canvas, font["font"], __centered(text, __total_center(coords, index), width), y, color, text)


def __render_separator(canvas, coords, row_y, color):
    """The rule between the inning columns and the R/H/E totals.

    There used to be a matching one on the left, closing the grid off from the
    team abbreviation. It read as a letter: a 1px bar the height of the caps,
    one gap away from "MIL", makes the row say MILI. The totals need separating
    from the innings and nothing needs separating from the abbreviation, so only
    the right-hand rule survives.

    Derived from the column geometry so it keeps its gap when the cell width
    changes, and cut to the height of the team abbreviation beside it rather
    than the full band.
    """
    separator = coords["separator"]
    top = row_y + separator["offset"]
    bottom = top + separator["height"] - 1
    x = coords["x"] + __band(coords) + 1
    graphics.DrawLine(canvas, x, top, x, bottom, color)


def __render_current_inning(canvas, colors, coords, columns, scoreboard, start):
    """Mark the half-inning in play with a small block in its cell.

    A completed game has no inning in play, so nothing is drawn. During a break
    the mark sits on the half that is *about* to bat, which is the question the
    break screen is answering.
    """
    if status.is_complete(scoreboard.game_status):
        return

    inning = scoreboard.inning
    if inning.state in (Inning.TOP, Inning.MIDDLE):
        number, side = inning.number, "away" if inning.state == Inning.TOP else "home"
    elif inning.state == Inning.BOTTOM:
        number, side = inning.number, "home"
    else:  # End of an inning: next up is the top of the following one.
        number, side = inning.number + 1, "away"

    offset = number - start - 1
    if not 0 <= offset < columns:
        return

    size = coords["marker"]["size"]
    center_x = __inning_center(coords, columns, offset)
    # Below the baseline, in the clear rows at the foot of that team's band: a
    # half that is already scoring has a digit in the cell, so a mark centred on
    # the cell would land on top of it.
    top = coords[side]["y"] + coords["marker"]["offset"]
    color = colors.graphics_color("line_score.marker")
    for row in range(size):
        graphics.DrawLine(canvas, center_x - size // 2, top + row, center_x - size // 2 + size - 1, top + row, color)


def __extend_band(canvas, layout, coords, side, rgb):
    """Carry the team's colour band out under the columns.

    `teams.background` stops partway across -- on the live screen the space to
    its right belongs to the bases and outs. The line score only draws on the
    break and final screens, so it can extend the band itself rather than force
    a wider one onto every screen.
    """
    band = layout.coords("teams.background.{}".format(side))
    color = graphics.Color(rgb["r"], rgb["g"], rgb["b"])
    for y in range(band["y"], band["y"] + band["height"]):
        graphics.DrawLine(canvas, band["x"] + band["width"], y, canvas.width - 1, y, color)


def __columns(coords, label_font, played):
    """How many inning columns fit, given how wide their headers have to be.

    The width of the inning grid is fixed at `innings * cell_width`, so a
    game that reaches double figures buys legible headers by giving up columns
    rather than by growing: nine 6px cells become five 10px ones once a label
    needs two digits. That is the trade the early innings are there to make.

    The two spare pixels are what separates one header from the next. Without
    them the gap between "11" and "12" is the same 1px that sits between their
    own digits, and the row reads as one long number -- which is exactly what
    nine 6px cells do at the tenth inning.
    """
    digits = 2 if played >= 10 else 1
    needed = max(digits * label_font["size"]["width"] + 2, coords["cell_width"])
    return max(1, __band(coords) // needed)


def __centered(text, center, width):
    """Left edge for `text` with its ink centred on `center`.

    bullpen's center_text_position centres the ADVANCE box, which carries a
    trailing spacing column no glyph draws in, so text lands half a pixel left
    of where it was asked to go -- and by a different amount in a 4px font than
    a 5px one. Here the headers sit directly above the values, so that half
    pixel showed up as a whole one: a column of 0s stood 1px right of its own
    inning number.

    n characters occupy `n * width` of advance but only `n * width - 1` of ink,
    which is the span this centres.
    """
    return center - (len(text) * width - 1) // 2


def __band(coords):
    """Width of the inning grid, which `__render_separator` draws the right edge of."""
    return coords["innings"] * coords["cell_width"]


def __inning_center(coords, columns, offset):
    """Centre of one column, as a share of the band rather than a fixed cell.

    Dividing the band up each time leaves the spare pixels of an uneven split
    spread across the gutters instead of dumped after the last column. At the
    configured nine columns it lands on exactly the same centres a plain
    `offset * cell_width` would.
    """
    return coords["x"] + (__band(coords) * (2 * offset + 1)) // (2 * columns)


def __total_center(coords, index):
    return coords["rhe_x"] + index * coords["rhe_width"] + coords["rhe_width"] // 2


def __placement(layout, key):
    """Return the geometry block at `key`, or None when this size has none.

    These blocks hold no `enabled` flag of their own -- the single switch is
    `teams.line_score.show_innings` -- so simply having a block is what says a
    board size knows where to put one.
    """
    try:
        return layout.coords(key)
    except KeyError:
        return None
