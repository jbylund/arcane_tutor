#!/usr/bin/env python3
"""Fit artwork-preference weights from labels and compare models by cross-validation.

Reads both label formats produced so far:

  v1 pairwise  {left_sid, right_sid, verdict: left|right|other}
  v2 grid      {shown: [{sid, art}], chosen_sid, verdict: pick|other}

A grid pick expands to N-1 pairwise observations (chosen beats each other shown), so
a 10-artwork card yields 9 observations from one keypress.

Only ARTWORK-level features are fitted, on purpose. The v2 labeller showed nothing but
the cropped art, so the verdict cannot have been influenced by border, frame, finish or
rarity -- those were invisible. Fitting them would be attributing to the printing what
was judged about the picture. (v1 showed whole cards, so its labels are weaker in
exactly that respect.)

Features, all oriented so LARGER IS PREFERRED, which is what lets every weight be
constrained non-negative -- the declared-signs approach from
docs/issues/local-prefer-score-label-harness.md.

  art_age            decades since this artwork first appeared
  art_pop            log1p(printings of this artwork under this card name)
  artist_prominence  log1p(total printings of the artist's OTHER work)
  artist_reuse       log(printings per artwork for the artist's OTHER work)

Both artist features are leave-one-out: this artwork's own printings are subtracted
before aggregating, or they would partly re-encode art_pop and steal its credit
invisibly. Prominence and reuse are used rather than (illustrations, printings) because
those two correlate at 0.986 -- collinear enough that their individual coefficients
would be noise. Prominence/reuse correlate at 0.697.

Usage:
    python scripts/fit_artwork_score.py labels1.jsonl labels2.jsonl
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import subprocess
import sys
from pathlib import Path

DB_CONTAINER, DB_USER, DB_NAME = "sylvan_blue-postgres-1", "foouser", "magic"

FOLDS = 5
SPLIT_SEED = 20260725
LEARNING_RATE = 0.08
ITERATIONS = 3000
L2 = 1e-3
# Reference year for artwork age. Keep in step with the corpus, not with wall-clock.
NOW_YEAR = 2026

FEATURES = ("art_age", "art_pop", "artist_prominence", "artist_reuse")

FEATURE_SQL = """
WITH illus AS (   -- per artwork: printings and debut
    SELECT illustration_id, card_name, count(*) AS n_print, min(released_at) AS first_seen
    FROM magic.cards
    WHERE raw_card_blob ->> 'lang' = 'en' AND illustration_id IS NOT NULL
    GROUP BY illustration_id, card_name
),
art_one AS (      -- one row per artwork, with its artist
    SELECT DISTINCT ON (illustration_id) illustration_id, raw_card_blob ->> 'artist' AS artist
    FROM magic.cards
    WHERE raw_card_blob ->> 'lang' = 'en' AND illustration_id IS NOT NULL
    ORDER BY illustration_id
),
artist_tot AS (   -- artist-level aggregates over all their artworks
    SELECT a.artist,
           count(*) AS n_illus,
           sum(i.n_print) AS n_print_total
    FROM art_one a JOIN illus i USING (illustration_id)
    GROUP BY a.artist
)
SELECT c.scryfall_id::text AS sid,
       c.prefer_score,
       i.n_print,
       extract(year from i.first_seen) AS first_year,
       t.n_illus,
       t.n_print_total
FROM magic.cards c
JOIN illus i ON i.illustration_id = c.illustration_id AND i.card_name = c.card_name
JOIN art_one a ON a.illustration_id = c.illustration_id
JOIN artist_tot t ON t.artist = a.artist
WHERE c.scryfall_id::text IN ({ids})
"""


def run_query(sql: str) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "--csv", "-f", "-"],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed:\n{proc.stderr}")
    return list(csv.DictReader(io.StringIO(proc.stdout)))


def expand(paths: list[Path]) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """Both formats -> (chosen_sid, rejected_sid, provenance) triples."""
    pairs: list[tuple[str, str, str]] = []
    stats = {"v1_records": 0, "v2_records": 0, "other": 0}
    for p in paths:
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if "shown" in r:  # v2 grid
                stats["v2_records"] += 1
                if r["verdict"] != "pick":
                    stats["other"] += 1
                    continue
                for s in r["shown"]:
                    if s["sid"] != r["chosen_sid"]:
                        pairs.append((r["chosen_sid"], s["sid"], "v2"))
            else:  # v1 pairwise
                stats["v1_records"] += 1
                if r["verdict"] not in ("left", "right"):
                    stats["other"] += 1
                    continue
                rejected = r["right_sid"] if r["verdict"] == "left" else r["left_sid"]
                pairs.append((r["chosen_sid"], rejected, "v1"))
    return pairs, stats


def to_vector(row: dict[str, str]) -> list[float] | None:
    try:
        n_print = float(row["n_print"])
        first_year = float(row["first_year"])
        n_illus = float(row["n_illus"])
        tot = float(row["n_print_total"])
    except (ValueError, TypeError):
        return None
    # Leave-one-out: remove this artwork's own contribution to the artist totals.
    other_print = max(0.0, tot - n_print)
    other_illus = max(0.0, n_illus - 1.0)
    reuse = (other_print / other_illus) if other_illus > 0 else 0.0
    return [
        (NOW_YEAR - first_year) / 10.0,  # art_age, decades
        math.log1p(n_print),  # art_pop
        math.log1p(other_print),  # artist_prominence (LOO)
        math.log1p(reuse),  # artist_reuse (LOO)
    ]


def softplus(x: float) -> float:
    return math.log1p(math.exp(x)) if x < 30 else x


def sigmoid(x: float) -> float:
    return 0.0 if x < -30 else 1.0 if x > 30 else 1.0 / (1.0 + math.exp(-x))


def fit(deltas: list[list[float]], k: int) -> list[float]:
    """Non-negative-constrained logistic fit via w = softplus(theta)."""
    theta = [0.0] * k
    for _ in range(ITERATIONS):
        w = [softplus(t) for t in theta]
        grad = [0.0] * k
        for d in deltas:
            g = 1.0 - sigmoid(sum(wi * di for wi, di in zip(w, d, strict=True)))
            for j, dj in enumerate(d):
                grad[j] += g * dj
        n = max(1, len(deltas))
        for j in range(k):
            theta[j] += LEARNING_RATE * (grad[j] / n) * sigmoid(theta[j]) - LEARNING_RATE * L2 * theta[j]
    return [softplus(t) for t in theta]


def agree(deltas: list[list[float]], w: list[float]) -> tuple[int, int]:
    hit = n = 0
    for d in deltas:
        z = sum(wi * di for wi, di in zip(w, d, strict=True))
        if z == 0:
            continue
        n += 1
        hit += z > 0
    return hit, n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", type=Path, nargs="+")
    ap.add_argument(
        "--only",
        choices=("v1", "v2", "both"),
        default="both",
        help="restrict to one label generation (v2 labels saw only the art crop)",
    )
    args = ap.parse_args()

    pairs, stats = expand(args.labels)
    if args.only != "both":
        pairs = [p for p in pairs if p[2] == args.only]
    print(
        f"{stats['v1_records']} v1 + {stats['v2_records']} v2 records, "
        f"{stats['other']} no-preference -> {len(pairs)} pairwise observations"
        + (f" (filtered to {args.only})" if args.only != "both" else "")
    )

    sids = sorted({s for a, b, _ in pairs for s in (a, b)})
    rows = {r["sid"]: r for r in run_query(FEATURE_SQL.format(ids=",".join(repr(s) for s in sids)))}

    deltas, base, prov = [], [], []
    for chosen, rejected, src in pairs:
        rc, rr = rows.get(chosen), rows.get(rejected)
        if not rc or not rr:
            continue
        vc, vr = to_vector(rc), to_vector(rr)
        if vc is None or vr is None:
            continue
        deltas.append([a - b for a, b in zip(vc, vr, strict=True)])
        base.append((float(rc["prefer_score"]), float(rr["prefer_score"])))
        prov.append(src)
    print(f"usable after feature lookup: {len(deltas)}  (v1 {prov.count('v1')} / v2 {prov.count('v2')})\n")

    idx = list(range(len(deltas)))
    random.Random(SPLIT_SEED).shuffle(idx)
    folds = [idx[i::FOLDS] for i in range(FOLDS)]

    def cv(keep: tuple[str, ...]) -> tuple[int, int, list[float]]:
        ks = [FEATURES.index(f) for f in keep]
        sub = [[d[j] for j in ks] for d in deltas]
        hit = n = 0
        for f in range(FOLDS):
            te = folds[f]
            tr = [i for g, fold in enumerate(folds) if g != f for i in fold]
            w = fit([sub[i] for i in tr], len(ks))
            h, c = agree([sub[i] for i in te], w)
            hit += h
            n += c
        return hit, n, fit(sub, len(ks))

    models: tuple[tuple[str, ...], ...] = (
        ("art_age",),
        ("art_pop",),
        ("artist_prominence",),
        ("artist_reuse",),
        ("art_age", "art_pop"),
        ("art_age", "artist_prominence"),
        ("art_age", "art_pop", "artist_prominence", "artist_reuse"),
    )
    print(f"{FOLDS}-fold CV, out-of-fold agreement (chance = 50%):\n")
    print(f"  {'model':46s} {'agree':>7s} {'n':>5s} {'95% band':>9s}")
    best = None
    for keep in models:
        hit, n, w = cv(keep)
        if not n:
            continue
        pct, band = 100 * hit / n, 1.96 * math.sqrt(0.25 / n) * 100
        label = " + ".join(keep)
        print(f"  {label:46s} {pct:6.1f}% {n:5d} {'±' + f'{band:.0f}':>9s}")
        if best is None or pct > best[0]:
            best = (pct, keep, w)

    hit = n = 0
    for c, r in base:
        if c != r:
            n += 1
            hit += c > r
    if n:
        band = 1.96 * math.sqrt(0.25 / n) * 100
        print(f"  {'current prefer_score (baseline)':46s} {100 * hit / n:6.1f}% {n:5d} {'±' + f'{band:.0f}':>9s}")

    if best:
        pct, keep, w = best
        scale = max(w) or 1.0
        print(f"\nbest model: {' + '.join(keep)}  ({pct:.1f}%)")
        print("  relative weights (only ratios are identified):")
        for name, wk in sorted(zip(keep, w, strict=True), key=lambda kv: -kv[1]):
            print(f"    {name:20s} {wk / scale:6.3f}")


if __name__ == "__main__":
    main()
