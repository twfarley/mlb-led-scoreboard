"""Draws the layout editor's bounding boxes over a real render, to check them by eye.

The boxes and the board come from the same code the editor uses
(``layout_preview.elements`` / ``layout_preview.render``), so if a box sits off
its pixels here it will sit off them in the editor too. That is the failure mode
worth catching: mistaking a centre anchor for a left edge is what put "FINAL"
underneath the home score.

Usage: PYTHONPATH=. venv/bin/python tools/verify_anchors.py [size] [screen]
"""

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

import layout_preview  # noqa: E402

SCALE = 8
EXACT = (0, 255, 0)  # box is the element's true extent
NOMINAL = (255, 160, 0)  # width depends on live data; box is indicative
ANCHOR = (255, 0, 255)  # the (x, y) the coordinates file actually stores


def main():
    size = sys.argv[1] if len(sys.argv) > 1 else "w128h64"
    screen = sys.argv[2] if len(sys.argv) > 2 else "final"

    base = Image.open(io.BytesIO(layout_preview.render(size, screen))).convert("RGB")
    board_w, board_h = base.size

    big = base.resize((board_w * SCALE, board_h * SCALE), Image.NEAREST)
    draw = ImageDraw.Draw(big)

    els = layout_preview.elements(size, screen)
    for el in els:
        x0, y0, x1, y1 = el["box"]
        draw.rectangle(
            [x0 * SCALE, y0 * SCALE, (x1 + 1) * SCALE - 1, (y1 + 1) * SCALE - 1],
            outline=NOMINAL if el["dynamic"] else EXACT,
        )
        coords = el["coords"]
        if coords.get("x") is not None and coords.get("y") is not None:
            ax, ay = coords["x"], coords["y"]
            draw.rectangle([ax * SCALE, ay * SCALE, (ax + 1) * SCALE - 1, (ay + 1) * SCALE - 1], fill=ANCHOR)

    out = Path(f"/tmp/anchors_{size}_{screen}.png")
    big.save(out)
    print(f"{len(els)} boxes over {size}/{screen} -> {out}")
    print(f"  green = exact extent, orange = width varies with live data, magenta = stored (x, y)")


if __name__ == "__main__":
    main()
