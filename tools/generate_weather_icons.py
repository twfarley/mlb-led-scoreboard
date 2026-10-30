#!/usr/bin/env python
"""Draw the news plugin's weather icons.

The icons are generated rather than hand-painted for the same reason the
example configs are: so a change is a readable diff in one place instead of
eighteen binary files nobody can review. Run it after editing a shape:

    PYTHONPATH=news/src venv/bin/python tools/generate_weather_icons.py

Shapes are filled, not outlined. The 128x64 board scales the 15x15 art up by 2
with nearest-neighbour, which turns a one-pixel outline into a two-pixel one
with a hole in the middle -- the reason the previous set read as a blob at
size. Filled shapes survive the doubling.

Icon names are OpenWeatherMap condition codes. `d`/`n` are day and night, and
the pairs that OWM distinguishes are drawn differently here so they stay
distinguishable on the board:

  01 clear          sun / moon
  02 few clouds     sun or moon behind a small cloud
  03 scattered      one cloud
  04 broken         two clouds
  09 shower rain    cloud, heavy rain, no sun
  10 rain           sun or moon, cloud, lighter rain
  11 thunderstorm   cloud and a bolt
  13 snow           cloud and flakes
  50 mist           banded haze
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "news" / "src"))

from mlb_led_scoreboard_news.palette import (  # noqa: E402
    BOLT,
    CLOUD,
    CLOUD_ALT,
    MIST,
    MOON,
    RAIN,
    SNOW,
    SUN,
)

OUT = REPO / "news" / "src" / "mlb_led_scoreboard_news" / "icons"

SIZE = 15

# Single letters keep a grid readable when you print one while tuning a shape.
INK = {
    "S": SUN,
    "M": MOON,
    "C": CLOUD,
    "c": CLOUD_ALT,
    "R": RAIN,
    "W": SNOW,
    "B": BOLT,
    "F": MIST,
}

EMPTY = "."

RAYS = [(0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)]

# A sun peeking out from behind a cloud only shows the rays on its free side.
# Drawing all eight puts rays inside the cloud and off the top of the frame.
RAYS_OPEN = [(0, -1), (-1, 0), (-1, -1), (1, -1), (-1, 1)]


def blank():
    return [[EMPTY] * SIZE for _ in range(SIZE)]


def put(g, x, y, ch):
    if 0 <= x < SIZE and 0 <= y < SIZE:
        g[y][x] = ch


def disc(g, cx, cy, r, ch):
    for y in range(SIZE):
        for x in range(SIZE):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                g[y][x] = ch


def carve(g, cx, cy, r):
    disc(g, cx, cy, r, EMPTY)


def rect(g, x0, y0, x1, y1, ch):
    for y in range(int(y0), int(y1) + 1):
        for x in range(int(x0), int(x1) + 1):
            put(g, x, y, ch)


def line(g, x0, y0, x1, y1, ch):
    steps = max(abs(x1 - x0), abs(y1 - y0))
    for i in range(steps + 1):
        t = i / steps if steps else 0
        put(g, round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t), ch)


def sun(g, cx, cy, r=3.3, dirs=RAYS, length=2, gap=1, ch="S"):
    """Disc plus rays, set `gap` pixels off the disc.

    The gap is measured from the disc's own edge in each direction, which is
    not the same in both: a disc of radius 3.3 reaches 3 pixels across but only
    2 along the diagonal, so a fixed distance would leave the diagonal rays
    adrift while the cardinals still touched.

    A small sun wants gap=0. The 128x64 board doubles the art, which doubles
    the gap with it, and a detached ray at that size reads as a stray pixel
    rather than as part of the sun.
    """
    disc(g, cx, cy, r, ch)
    for dx, dy in dirs:
        reach = int(r / 1.414) if (dx and dy) else int(r)
        for d in range(reach + 1 + gap, reach + 1 + gap + length):
            put(g, cx + dx * d, cy + dy * d, ch)


def moon(g, cx, cy, r=5.6, ch="M"):
    """Crescent: a disc with a slightly smaller one taken out of its right.

    The bite is kept under the disc's own radius so the horns stay thick. A
    bite as wide as the disc pares them to one pixel, which at this size reads
    as a letter C rather than a moon.
    """
    disc(g, cx, cy, r, ch)
    carve(g, cx + r * 0.72, cy - r * 0.12, r * 0.90)


def cloud(g, cx, base, ch="C", scale=1.0):
    """Three-lobe cloud with a flat bottom on row `base`."""
    disc(g, cx + 0.5 * scale, base - 3.4 * scale, 2.6 * scale, ch)
    disc(g, cx - 2.8 * scale, base - 2.0 * scale, 2.1 * scale, ch)
    disc(g, cx + 3.4 * scale, base - 1.9 * scale, 2.2 * scale, ch)
    rect(g, cx - 4.6 * scale, base - 2, cx + 4.6 * scale, base, ch)
    # The lobes overshoot the flat bottom; shear off whatever fell below it.
    for y in range(int(base) + 1, SIZE):
        for x in range(SIZE):
            if g[y][x] == ch:
                g[y][x] = EMPTY


def rain(g, rows, xs, ch="R", length=2):
    """Slanted strokes, one per x in `xs`, starting on the first of `rows`."""
    top = rows[0]
    for x in xs:
        line(g, x, top, x - length, top + length, ch)


def flake(g, x, y, ch="W"):
    """A three-pixel-wide plus; a single pixel reads as noise at this size."""
    put(g, x, y, ch)
    put(g, x - 1, y, ch)
    put(g, x + 1, y, ch)
    put(g, x, y - 1, ch)
    put(g, x, y + 1, ch)


# Drawn flat rather than from strokes: a bolt is small enough that every pixel
# is a decision, and a line-drawn one came out as an indistinct squiggle.
BOLT_SPRITE = [
    "..BB.",
    ".BB..",
    "BBBBB",
    "..BB.",
    ".BB..",
]


def bolt(g, x, y, ch="B"):
    """Lightning bolt with its top-left corner at (x, y)."""
    for dy, row in enumerate(BOLT_SPRITE):
        for dx, cell in enumerate(row):
            if cell != EMPTY:
                put(g, x + dx, y + dy, ch)


def grid_to_image(g):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            if g[y][x] != EMPTY:
                px[x, y] = INK[g[y][x]] + (255,)
    return img


def show(g, name=""):
    """Print a grid as text. For tuning a shape from the shell."""
    if name:
        print(f"--- {name}")
    for row in g:
        print("".join(row))


# --- the icons -------------------------------------------------------------
#
# Each builder returns a grid. Day and night differ only in which body is in
# the sky, so they share a builder wherever OWM shares a shape.


def clear(night):
    g = blank()
    if night:
        moon(g, 6.8, 7.2)
    else:
        sun(g, 7, 7)
    return g


def few_clouds(night):
    g = blank()
    if night:
        moon(g, 4.4, 4.0, r=3.4)
    else:
        sun(g, 5, 5, r=2.6, dirs=RAYS_OPEN, gap=0)
    cloud(g, 8, 13, scale=0.92)
    return g


def scattered():
    g = blank()
    cloud(g, 7, 11)
    return g


def broken():
    g = blank()
    cloud(g, 8, 7, ch="c", scale=0.78)
    cloud(g, 6, 13, scale=0.95)
    return g


def shower():
    g = blank()
    cloud(g, 7, 9)
    rain(g, [11], [4, 8, 12], length=3)
    return g


def rainy(night):
    g = blank()
    if night:
        moon(g, 3.4, 3.2, r=2.9)
    else:
        sun(g, 3, 3, r=2.2, dirs=RAYS_OPEN, length=2, gap=0)
    cloud(g, 8, 10, scale=0.90)
    rain(g, [12], [6, 10, 14], length=2)
    return g


def thunder():
    g = blank()
    cloud(g, 7, 9)
    bolt(g, 5, 10)
    return g


def snowy():
    g = blank()
    cloud(g, 7, 9)
    flake(g, 3, 12)
    flake(g, 7, 13)
    flake(g, 11, 12)
    return g


def mist():
    g = blank()
    for i, y in enumerate(range(2, 14, 3)):
        x0 = 1 if i % 2 else 2
        rect(g, x0, y, x0 + 10, y, "F")
    return g


ICONS = {
    "01d": lambda: clear(False),
    "01n": lambda: clear(True),
    "02d": lambda: few_clouds(False),
    "02n": lambda: few_clouds(True),
    "03d": scattered,
    "03n": scattered,
    "04d": broken,
    "04n": broken,
    "09d": shower,
    "09n": shower,
    "10d": lambda: rainy(False),
    "10n": lambda: rainy(True),
    "11d": thunder,
    "11n": thunder,
    "13d": snowy,
    "13n": snowy,
    "50d": mist,
    "50n": mist,
}


def main():
    for name, build in sorted(ICONS.items()):
        grid_to_image(build()).save(OUT / f"{name}.png")
    print(f"Wrote {len(ICONS)} icons to {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
