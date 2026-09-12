"""Verifies schemas/coordinates/_anchors.json against a real render.

Draws each element's anchor-derived bounding box over the actual rendered board.
If the metadata is right the boxes sit on the pixels; if an anchor is wrong the
box is visibly offset -- which is exactly the failure mode that produced the
FINAL/score overlap bug.

Usage: PYTHONPATH=. venv/bin/python tools/verify_anchors.py [size] [screen]
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

import layout_preview  # noqa: E402

SCALE = 8
BOX = (0, 255, 0)
ANCHOR = (255, 0, 255)


def resolve(values: dict, keypath: str):
    node = values
    for part in keypath.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def box_for(keypath, coords, meta, layout, width, text_len):
    """(x0, y0, x1, y1) in board pixels, or None if it can't be placed."""
    anchor, extent = meta["anchor"], meta["extent"]
    x, y = coords.get("x"), coords.get("y")

    if extent in ("box", "image"):
        return (x, y, x + coords["width"] - 1, y + coords["height"] - 1)
    if extent in ("square", "diamond"):
        return (x, y, x + coords["size"], y + coords["size"])
    if extent == "vline":
        return (x, coords["y_start"], x, coords["y_end"])
    if extent == "squares":
        size = coords["size"]
        ys = coords["squares"]
        return (x, min(ys), x + size - 1, max(ys) + size - 1)
    if extent == "column" or anchor == "none":
        return None

    font = layout.font(keypath)
    fw, fh = font["size"]["width"], font["size"]["height"]

    if extent == "arrow_up":
        size = layout.coords("inning.arrow")["size"]
        return (x - size + 1, y, x + size - 1, y + size - 1)
    if extent == "arrow_down":
        size = layout.coords("inning.arrow")["size"]
        return (x - size + 1, y - size + 1, x + size - 1, y)

    if extent == "scroll":
        w = coords.get("width", width)
        return (x, y - fh + 1, x + w - 1, y)

    # extent == "text": y is a BASELINE, so the box rises fh-1 above it.
    w = max(1, text_len) * fw
    if anchor == "center":
        x0 = x - w // 2
    elif anchor == "right":
        x0 = x - w + 1
    elif anchor == "board-right":
        x0 = width - w
    else:
        x0 = x
    return (x0, y - fh + 1, x0 + w - 1, y)


# Elements whose on-screen text length is known for the fixture games, so the
# box can be checked exactly rather than nominally.
KNOWN_TEXT = {
    "final.inning": 8,  # "FINAL 14" for the 14-inning fixture game
    "teams.line_score.away": 2,  # MIL 15
    "teams.line_score.home": 2,  # WSH 14
    "teams.name.away": 3,  # "MIL"
    "teams.name.home": 3,  # "WSH"
    "inning.number": 1,
    "status.text": 7,
    "pregame.start_time": 7,
}


def main():
    size = sys.argv[1] if len(sys.argv) > 1 else "w128h64"
    screen = sys.argv[2] if len(sys.argv) > 2 else "final"

    png = layout_preview.render(size, screen)
    out = Path(f"/tmp/anchors_{size}_{screen}.png")
    base = Image.open(__import__("io").BytesIO(png)).convert("RGB")
    board_w, board_h = base.size

    from data.config.layout import Layout

    values = layout_preview._coords_for(size)
    layout = Layout(values, board_w, board_h)
    anchors = json.loads((REPO / "schemas" / "coordinates" / "_anchors.json").read_text())["anchors"]

    big = base.resize((board_w * SCALE, board_h * SCALE), Image.NEAREST)
    draw = ImageDraw.Draw(big)

    meta_doc = json.loads((REPO / "schemas" / "coordinates" / "_anchors.json").read_text())
    prefixes = meta_doc["screens"].get(screen, [])

    drawn = 0
    for keypath, meta in sorted(anchors.items()):
        if not any(keypath.startswith(p) for p in prefixes):
            continue  # belongs to a different screen
        coords = resolve(values, keypath)
        if coords is None:
            continue
        try:
            box = box_for(keypath, coords, meta, layout, board_w, KNOWN_TEXT.get(keypath, 3))
        except (KeyError, TypeError):
            continue
        if box is None:
            continue
        x0, y0, x1, y1 = box
        draw.rectangle([x0 * SCALE, y0 * SCALE, (x1 + 1) * SCALE - 1, (y1 + 1) * SCALE - 1], outline=BOX)
        ax = coords.get("x")
        if ax is not None and coords.get("y") is not None:
            draw.rectangle(
                [ax * SCALE, coords["y"] * SCALE, (ax + 1) * SCALE - 1, (coords["y"] + 1) * SCALE - 1], fill=ANCHOR
            )
        drawn += 1

    big.save(out)
    print(f"{drawn} boxes drawn over {size}/{screen} -> {out}")


if __name__ == "__main__":
    main()
