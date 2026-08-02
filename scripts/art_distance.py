#!/usr/bin/env python3
"""Perceptual distance between two printings' artwork, for filtering the label pool.

Scryfall assigns distinct illustration_ids to printings whose art is the same
composition with a recolour or effect treatment (observed: Arashin War Beast
frf/123 vs ugin/123). Attribute-level filters cannot see that, so "art-only" pairs
sometimes show what a human reads as one picture — a wasted click.

The metric, deliberately the simple one: crop to the art window, convert to
grayscale, blur by downscaling hard, normalise brightness, and take the mean
absolute pixel difference. Small distance means "looks the same". Runs on `dwebp`
plus the standard library — no image-library dependency.

Two design notes that matter more than the algorithm choice:

  * **Crop to the art before comparing.** Card frames carry the title, text box,
    set symbol and collector line, all of which differ between printings of the
    same art. Uncropped, that text dominates the distance and the filter inverts:
    identical art in two different sets scores as "different".
  * **Normalise brightness, and use grayscale.** The failure case this exists for
    is a recolour of one composition, so colour and exposure differences are
    exactly what should be ignored.

Calibrate rather than guess the threshold: run --calibrate against a labelled
JSONL and see where the "other" verdicts sit in the distance distribution.

Usage:
    python scripts/art_distance.py --pair frf/123 ugin/123
    python scripts/art_distance.py --calibrate ~/Downloads/prefer-score-labels.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CDN_BASE = "https://d1hot9ps2xugbc.cloudfront.net/img"
# Smallest CDN size: enough for a blurred comparison and cheap to fetch in bulk.
FETCH_WIDTH = 280

# Fractional art window of a card face, as (left, top, right, bottom). Generous
# and frame-agnostic: modern frames put art at roughly y 0.11-0.46 and older ones
# slightly lower, so this covers the art in both while excluding the title bar,
# the text box, and the collector line. Pairs always share a frame version (the
# labelling pool holds it constant), so a single window is consistent within a pair.
ART_BOX = (0.09, 0.12, 0.91, 0.46)

# Downscale target after cropping. This IS the blur — 16x16 keeps composition and
# discards brushwork, which is the level of detail the comparison should work at.
GRID = 16

# Mean absolute grayscale difference, 0-255, below which two arts are treated as
# the same picture. Provisional until --calibrate says otherwise.
SAME_ART_THRESHOLD = 12.0


def fetch(set_code: str, collector: str, cache: Path) -> Path:
    """Download one card face, cached by set/collector."""
    dst = cache / f"{set_code}_{collector}.webp".replace("/", "_")
    if not dst.exists():
        # Collector numbers contain non-ASCII (e.g. "86★"), which urllib refuses to
        # send raw; quote the path segments so those printings are not silently skipped.
        url = f"{CDN_BASE}/{urllib.parse.quote(set_code)}/{urllib.parse.quote(collector)}/1/{FETCH_WIDTH}.webp"
        try:
            with urllib.request.urlopen(url, timeout=20) as r, dst.open("wb") as f:
                f.write(r.read())
        except Exception as exc:
            msg = f"{url}: {exc}"
            raise FileNotFoundError(msg) from exc
    return dst


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    """Parse a binary P6 PPM (what `dwebp -ppm` emits)."""
    data = path.read_bytes()
    fields: list[bytes] = []
    i = 0
    while len(fields) < 4:  # magic, width, height, maxval
        while data[i : i + 1].isspace():
            i += 1
        if data[i : i + 1] == b"#":
            i = data.index(b"\n", i) + 1
            continue
        j = i
        while not data[j : j + 1].isspace():
            j += 1
        fields.append(data[i:j])
        i = j
    return int(fields[1]), int(fields[2]), data[i + 1 :]


def fingerprint(webp: Path) -> list[float]:
    """Cropped, grayscale, downscaled, brightness-normalised GRID x GRID cells."""
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as tmp:
        ppm = Path(tmp.name)
    try:
        subprocess.run(["dwebp", "-quiet", "-ppm", "-o", str(ppm), str(webp)], check=True, capture_output=True)
        w, h, px = read_ppm(ppm)
    finally:
        ppm.unlink(missing_ok=True)

    x0, y0, x1, y1 = (int(ART_BOX[0] * w), int(ART_BOX[1] * h), int(ART_BOX[2] * w), int(ART_BOX[3] * h))
    cw, ch = x1 - x0, y1 - y0
    cells = []
    for gy in range(GRID):
        for gx in range(GRID):
            # Average every pixel in this cell: downscaling as blur.
            sx0, sx1 = x0 + cw * gx // GRID, x0 + cw * (gx + 1) // GRID
            sy0, sy1 = y0 + ch * gy // GRID, y0 + ch * (gy + 1) // GRID
            total = count = 0
            for y in range(sy0, max(sy0 + 1, sy1)):
                row = 3 * y * w
                for x in range(sx0, max(sx0 + 1, sx1)):
                    o = row + 3 * x
                    # Rec. 601 luma; the recolour case should compare on structure.
                    total += 0.299 * px[o] + 0.587 * px[o + 1] + 0.114 * px[o + 2]
                    count += 1
            cells.append(total / count)
    # Brightness-normalise so an exposure or tint shift is not read as difference.
    mean = sum(cells) / len(cells)
    return [c - mean for c in cells]


def distance(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pair", nargs=2, metavar=("SET/CN", "SET/CN"), help="compare two printings")
    g.add_argument(
        "--calibrate", type=Path, metavar="LABELS.jsonl", help="score every labelled pair and show where 'other' verdicts sit"
    )
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "art-cache")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)

    if args.pair:
        fps = []
        for spec in args.pair:
            set_code, collector = spec.split("/", 1)
            fps.append(fingerprint(fetch(set_code, collector, args.cache)))
        d = distance(*fps)
        verdict = "SAME art" if d < SAME_ART_THRESHOLD else "different art"
        print(f"distance {d:.2f}  ->  {verdict}  (threshold {SAME_ART_THRESHOLD})")
        return

    # --calibrate: the labelled batch tells us where to put the threshold. Pairs the
    # human called "other" are candidate near-duplicates; if they cluster at low
    # distance, that low region is what the filter should exclude.
    labels = [json.loads(x) for x in args.calibrate.read_text().splitlines() if x.strip()]
    sids = sorted({s for d in labels for s in (d["left_sid"], d["right_sid"])})
    sql = (
        "SELECT scryfall_id::text AS sid, card_set_code || '/' || collector_number AS loc "
        f"FROM magic.cards WHERE scryfall_id::text IN ({','.join(repr(s) for s in sids)})"
    )
    out = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "sylvan_blue-postgres-1",
            "psql",
            "-U",
            "foouser",
            "-d",
            "magic",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-F",
            "\t",
            "-f",
            "-",
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=True,
    )
    loc = dict(line.split("\t") for line in out.stdout.splitlines() if "\t" in line)

    fps: dict[str, list[float]] = {}
    rows = []
    for i, lab in enumerate(labels, 1):
        try:
            for sid in (lab["left_sid"], lab["right_sid"]):
                if sid not in fps:
                    set_code, collector = loc[sid].split("/", 1)
                    fps[sid] = fingerprint(fetch(set_code, collector, args.cache))
            rows.append((distance(fps[lab["left_sid"]], fps[lab["right_sid"]]), lab["verdict"], lab["card"]))
        except (FileNotFoundError, KeyError, subprocess.CalledProcessError) as exc:
            print(f"  skip {lab['card']}: {exc}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  ...{i}/{len(labels)}", file=sys.stderr)

    decided = [d for d, v, _ in rows if v in ("left", "right")]
    other = [d for d, v, _ in rows if v == "other"]
    print(f"\n{len(rows)} pairs scored: {len(decided)} decided, {len(other)} 'other'\n")
    print(f"  {'distance band':>16s} {'decided':>9s} {'other':>7s}  {'% other':>8s}")
    bands = [(0, 4), (4, 8), (8, 12), (12, 16), (16, 24), (24, 1000)]
    for lo, hi in bands:
        nd = sum(1 for d in decided if lo <= d < hi)
        no = sum(1 for d in other if lo <= d < hi)
        pct = f"{100 * no / (nd + no):.0f}%" if nd + no else "—"
        label = f"{lo}-{hi}" if hi < 1000 else f"{lo}+"
        print(f"  {label:>16s} {nd:9d} {no:7d}  {pct:>8s}")
    if decided:
        print(f"\n  median distance, decided : {sorted(decided)[len(decided) // 2]:.1f}")
    if other:
        print(f"  median distance, 'other' : {sorted(other)[len(other) // 2]:.1f}")
    print(
        "\n  A filter is worth adding if 'other' concentrates in the low bands: those are\n"
        "  pairs the labeller could not distinguish, and excluding them raises the yield\n"
        "  of every future click. If 'other' is spread evenly, the high rate is genuine\n"
        "  indifference between visibly different arts and no image filter will fix it."
    )


if __name__ == "__main__":
    main()
