#!/usr/bin/env python3
"""Watches the board's layout for anomalies and snapshots what it finds.

Renders every game state through the real renderers, inspects the resulting
pixels, and reports layout faults. Runs once with --once, or on a loop.

The checks are aimed at the failure modes this layout has actually produced:

  overdrawn   The team banner is drawn last, so it paints over anything beneath
              it. This is what silently hid the "F" of FINAL behind the home
              score. Found by diffing a banner-less render against a full one,
              then attributing the covered pixels to the elements whose boxes
              contain them.

  out_of_bounds
              An element's extent leaves the panel, so part of it is cut off.
              Softened for text, whose BDF cell carries a descent most glyphs
              never use, and skipped for anything switched off.

  collision   Two elements overlap. Only reported between exact extents:
              text widths depend on live data and a BDF cell is taller than the
              glyphs in it, so text-box overlap is routine and says nothing. An
              earlier version reported 187 of those on one board.

  changed     The render differs from the previous cycle. On live data that is
              usually just the game moving on, but it is how a layout fault that
              only appears with certain data (a two-digit score, a long name)
              gets caught at all.

Known-and-accepted findings (w32h32 parks unsupported features off-panel, for
instance) are suppressed via a baseline file, so a cycle stays quiet unless
something new appears.

Usage:
    PYTHONPATH=. venv/bin/python tools/layout_monitor.py --once
    PYTHONPATH=. venv/bin/python tools/layout_monitor.py --once --update-baseline
    PYTHONPATH=. venv/bin/python tools/layout_monitor.py --interval 60
    PYTHONPATH=. venv/bin/python tools/layout_monitor.py --sizes w128h64 --once
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

import layout_preview  # noqa: E402

DEFAULT_OUTDIR = REPO / "logs" / "layout_monitor"


# ── pixel helpers ────────────────────────────────────────────────────────────


def _background(size: str) -> tuple:
    colors = layout_preview._load_json(REPO / "colors" / "scoreboard.json", REPO / "colors" / "scoreboard.example.json")
    c = colors["default"]["background"]
    return (c["r"], c["g"], c["b"])


def _image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def _pixels(image: Image.Image):
    """image.load() is Optional in the stubs but never None for a loaded image."""
    px = image.load()
    assert px is not None
    return px


def _lit(image: Image.Image, bg: tuple) -> set:
    px = _pixels(image)
    return {(x, y) for y in range(image.height) for x in range(image.width) if px[x, y] != bg}


def _attribute(points: set, elements: list) -> dict:
    """Map pixels to the elements whose boxes contain them."""
    hits: dict = {}
    for el in elements:
        if not el.get("box"):
            continue
        x0, y0, x1, y1 = el["box"]
        owned = [p for p in points if x0 <= p[0] <= x1 and y0 <= p[1] <= y1]
        if owned:
            hits[el["keypath"]] = len(owned)
    return hits


def _renders(el) -> bool:
    """Whether the board will actually draw this element.

    Elements are switched off two ways: an `enabled` flag, and `draw` on the
    due-up divider. Several layouts also park unsupported features off-panel
    (w32h32 puts atbat.batter at x=33 on a 32-wide board), which is deliberate
    -- but that only stays quiet if the off switch is respected first.
    """
    coords = el.get("coords") or {}
    return bool(coords.get("enabled") is not False and coords.get("draw") is not False)


def _bounds(points) -> list:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


# ── checks ───────────────────────────────────────────────────────────────────

EXACT_EXTENTS = {"square", "diamond", "vline", "squares", "image"}


def check_overdrawn(size, screen, elements, bg) -> list:
    """Pixels drawn by the screen and then covered by the team banner.

    This is the trustworthy check: it compares real pixels rather than nominal
    boxes, so it does not guess. It is the one that would have caught FINAL
    disappearing under the home score.
    """
    content_img = _image(layout_preview.render(size, screen, skip_banner=True))
    full_img = _image(layout_preview.render(size, screen))
    cpx, fpx = _pixels(content_img), _pixels(full_img)

    # "Covered" means the banner CHANGED a pixel the screen had already drawn --
    # not merely that it erased it to the background. The banner fills its team
    # rectangles with the team's colour, so an earlier version of this check,
    # which only looked for pixels turning background-coloured, found nothing at
    # all when fed the real FINAL-under-the-score bug.
    covered = {
        (x, y)
        for y in range(content_img.height)
        for x in range(content_img.width)
        if cpx[x, y] != bg and fpx[x, y] != cpx[x, y]
    }
    if not covered:
        return []

    # Attribute the covered pixels. For text, the nominal box width is a guess
    # (NOMINAL_CHARS), so x cannot be trusted -- "FINAL 14" is twice the assumed
    # width and reaches well left of its box. The y band, however, is exact, so
    # fall back to matching rows. The banner's own elements are the culprit here,
    # never the victim, so they are not candidates.
    victims: dict = {}
    candidates = [e for e in elements if e.get("box") and not e["keypath"].startswith("teams.")]
    for point in covered:
        inside = [e for e in candidates if _contains(e["box"], point)]
        if not inside:
            inside = [
                e for e in candidates if e["extent"] in ("text", "scroll") and e["box"][1] <= point[1] <= e["box"][3]
            ]
        if not inside:
            continue
        best = min(inside, key=lambda e: _area(e["box"]))
        victims[best["keypath"]] = victims.get(best["keypath"], 0) + 1

    # Report even when nothing could be named: the pixels are measured fact, and
    # dropping a real finding because the culprit is hard to label is backwards.
    return [
        {
            "check": "overdrawn",
            "severity": "high",
            "pixels": len(covered),
            "bounds": _bounds(covered),
            "elements": victims,
            "detail": (
                "drawn, then covered by the team banner (which the board draws last)"
                if victims
                else "drawn, then covered by the team banner; could not attribute to an element"
            ),
        }
    ]


def check_out_of_bounds(size, screen, elements, bg) -> list:
    """Elements whose extent leaves the panel, so part of them is cut off."""
    m = layout_preview._SIZE_RE.match(size)
    if m is None:
        return []
    w, h = int(m.group(1)), int(m.group(2))
    out = []
    for el in elements:
        if not el.get("box") or not _renders(el):
            continue
        x0, y0, x1, y1 = el["box"]
        # For text, the BDF cell includes a descent that most glyphs never use,
        # so a baseline near the bottom edge overflows the cell without anything
        # actually being cut off. Measure the glyph band instead.
        font = el.get("font") or {}
        if el["extent"] in ("text", "scroll") and font.get("baseline"):
            y1 = y0 + font["baseline"] - 1
        over = []
        if x0 < 0:
            over.append(f"left by {-x0}")
        if y0 < 0:
            over.append(f"top by {-y0}")
        if x1 > w - 1:
            over.append(f"right by {x1 - (w - 1)}")
        if y1 > h - 1:
            over.append(f"bottom by {y1 - (h - 1)}")
        if not over:
            continue
        out.append(
            {
                "check": "out_of_bounds",
                # A dynamic width is an estimate, so overflowing it is a hint,
                # not a fact.
                "severity": "low" if el["dynamic"] else "medium",
                "elements": {el["keypath"]: 0},
                "bounds": el["box"],
                "detail": "extends past the panel: " + ", ".join(over),
            }
        )
    return out


def check_collisions(size, screen, elements, bg) -> list:
    """Overlapping element boxes, reported only where the boxes are exact.

    Text and scroll widths depend on live data, and a BDF cell is taller than the
    glyphs inside it, so box overlap between text elements is routine and says
    nothing. Reporting it drowns out the real findings -- the first version of
    this check produced 187 of them on one board.
    """
    out = []
    boxed = [e for e in elements if e.get("box") and _renders(e) and e["extent"] in EXACT_EXTENTS]
    for i, a in enumerate(boxed):
        for b in boxed[i + 1 :]:
            # Siblings under one parent (bases.1B/2B/3B, outs.1/2/3, the due-up
            # rows) are positioned as a set on purpose.
            if _parent(a["keypath"]) == _parent(b["keypath"]):
                continue
            if a["keypath"].startswith(b["keypath"] + ".") or b["keypath"].startswith(a["keypath"] + "."):
                continue
            overlap = _intersect(a["box"], b["box"])
            if overlap is None:
                continue
            out.append(
                {
                    "check": "collision",
                    "severity": "medium",
                    "elements": {a["keypath"]: 0, b["keypath"]: 0},
                    "bounds": overlap,
                    "detail": "two elements with exact extents overlap",
                }
            )
    return out


def _contains(box, point) -> bool:
    x0, y0, x1, y1 = box
    return bool(x0 <= point[0] <= x1 and y0 <= point[1] <= y1)


def _area(box) -> int:
    x0, y0, x1, y1 = box
    return int(max(1, (x1 - x0 + 1) * (y1 - y0 + 1)))


def _parent(keypath: str) -> str:
    return keypath.rsplit(".", 1)[0] if "." in keypath else keypath


def _intersect(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return [x0, y0, x1, y1] if x0 <= x1 and y0 <= y1 else None


CHECKS = (check_overdrawn, check_out_of_bounds, check_collisions)


# ── cycle ────────────────────────────────────────────────────────────────────


def run_cycle(sizes, screens, outdir: Path, previous: dict, save_all: bool) -> tuple:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    findings = []
    snapshots = {}

    for size in sizes:
        bg = _background(size)
        for screen in screens:
            key = f"{size}/{screen}"
            try:
                png = layout_preview.render(size, screen)
                elements = layout_preview.elements(size, screen)
            except Exception as exc:
                findings.append(
                    {"case": key, "check": "render", "severity": "high", "detail": f"{type(exc).__name__}: {exc}"}
                )
                continue

            snapshots[key] = png
            case_findings = []
            for check in CHECKS:
                try:
                    case_findings += check(size, screen, elements, bg)
                except Exception as exc:
                    case_findings.append(
                        {"check": check.__name__, "severity": "high", "detail": f"{type(exc).__name__}: {exc}"}
                    )

            if key in previous and previous[key] != png:
                case_findings.append(
                    {"check": "changed", "severity": "info", "detail": "render differs from the previous cycle"}
                )

            for finding in case_findings:
                finding["case"] = key
            findings += case_findings

            # Keep a picture of anything worth looking at.
            if save_all or any(f["severity"] in ("high", "medium") for f in case_findings):
                shot = outdir / stamp / f"{size}_{screen}.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
                shot.write_bytes(png)

    return findings, snapshots, stamp


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def fingerprint(finding) -> str:
    """Stable identity for a finding, so known ones can be accepted."""
    elements = ",".join(sorted(finding.get("elements", {}) or []))
    return f"{finding['case']}|{finding['check']}|{elements}"


def load_baseline(path: Path) -> set:
    if not path.is_file():
        return set()
    return set(json.loads(path.read_text()).get("accepted", []))


def save_baseline(path: Path, findings) -> None:
    """Accept everything currently reported.

    Some findings are deliberate and permanent -- w32h32 parks atbat.batter at
    x=33 on a 32-wide panel because that board does not support the feature.
    Without a way to accept those, the monitor reports the same dozen things
    every cycle and nobody reads it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "accepted_at": datetime.now().isoformat(timespec="seconds"),
                "accepted": sorted({fingerprint(f) for f in findings}),
            },
            indent=2,
        )
        + "\n"
    )


def report(findings, stamp, outdir: Path, verbose: bool) -> None:
    # Low and info findings are logged but not printed: a monitor that prints
    # a hundred maybes every minute stops being read.
    shown = findings if verbose else [f for f in findings if f["severity"] in ("high", "medium")]
    counts: dict = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    summary = " ".join(f"{k}={counts[k]}" for k in sorted(counts, key=lambda s: SEVERITY_ORDER.get(s, 9)))
    print(f"[{stamp}] {len(findings)} finding(s) {summary or '- clean'}")

    for f in sorted(shown, key=lambda f: SEVERITY_ORDER.get(f["severity"], 9)):
        where = f.get("bounds")
        els = ", ".join(f.get("elements", {}) or [])
        line = f"  {f['severity']:<6} {f['case']:<28} {f['check']:<10} {f['detail']}"
        if els:
            line += f"\n         -> {els}"
        if where:
            line += f"  @ {where}"
        print(line)

    log = outdir / "findings.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        for f in findings:
            fh.write(json.dumps({"at": stamp, **f}) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between cycles (default 60)")
    ap.add_argument("--once", action="store_true", help="run a single cycle and exit")
    ap.add_argument("--sizes", default=None, help="comma-separated board sizes (default: all)")
    ap.add_argument("--screens", default=None, help="comma-separated screens (default: all)")
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--save-all", action="store_true", help="snapshot every case, not just the suspect ones")
    ap.add_argument("--verbose", action="store_true", help="print low-confidence and informational findings too")
    ap.add_argument("--baseline", type=Path, default=None, help="file of accepted findings to suppress")
    ap.add_argument("--update-baseline", action="store_true", help="accept everything currently reported, then exit")
    args = ap.parse_args()

    sizes = args.sizes.split(",") if args.sizes else layout_preview.sizes()
    screens = args.screens.split(",") if args.screens else list(layout_preview.SCREENS)

    print(f"layout monitor: {len(sizes)} size(s) x {len(screens)} screen(s) = {len(sizes) * len(screens)} cases")
    print(f"  sizes:   {', '.join(sizes)}")
    print(f"  screens: {', '.join(screens)}")
    print(f"  output:  {args.outdir}")
    if not args.once:
        print(f"  every {args.interval:g}s — Ctrl-C to stop")
    print()

    baseline_path = args.baseline or (args.outdir / "baseline.json")
    baseline = load_baseline(baseline_path)
    if baseline:
        print(f"baseline: {len(baseline)} accepted finding(s) from {baseline_path}\n")

    previous: dict = {}
    try:
        while True:
            started = time.time()
            findings, snapshots, stamp = run_cycle(sizes, screens, args.outdir, previous, args.save_all)

            if args.update_baseline:
                save_baseline(baseline_path, findings)
                print(f"accepted {len({fingerprint(f) for f in findings})} finding(s) into {baseline_path}")
                return 0

            suppressed = sum(1 for f in findings if fingerprint(f) in baseline)
            findings = [f for f in findings if fingerprint(f) not in baseline]
            report(findings, stamp, args.outdir, verbose=args.verbose)
            if suppressed:
                print(f"  ({suppressed} known finding(s) suppressed by the baseline)")
            previous = snapshots
            if args.once:
                high = sum(1 for f in findings if f["severity"] == "high")
                return 1 if high else 0
            elapsed = time.time() - started
            print(f"  cycle took {elapsed:.1f}s\n")
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
