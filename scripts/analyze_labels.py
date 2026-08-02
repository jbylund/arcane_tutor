#!/usr/bin/env python3
"""Score candidate artwork features against collected preference labels.

Takes the JSONL exported by the labelling page (scripts/gen_labeller.py) and asks,
for each candidate feature, how often it agrees with the human verdict. Rejoins the
labelled scryfall_ids against magic.cards, so the labelling page never has to know
or display any feature value.

The headline question for `art-only` labels: **does reprint count predict artwork
preference better than chance?** `illustration_count` is the only component of
prefer_score that refers to the artwork at all, so if it does not predict these
verdicts then the scoring function is effectively blind to artwork and needs a new
feature rather than a re-weighting. See
docs/issues/local-prefer-score-label-harness.md.

Usage:
    python scripts/analyze_labels.py ~/Downloads/prefer-score-labels.jsonl
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import subprocess
import sys
from pathlib import Path

DB_CONTAINER = "sylvan_blue-postgres-1"
DB_USER = "foouser"
DB_NAME = "magic"

# Two-sided binomial significance threshold for "better than a coin flip".
ALPHA = 0.05

# Features fetched per labelled printing. Each is a candidate predictor of which
# artwork a human prefers; `prefer_score` is the current model's overall answer.
FEATURE_SQL = """
SELECT c.scryfall_id::text AS sid,
       c.prefer_score,
       (SELECT count(*) FROM magic.cards o
         WHERE o.illustration_id = c.illustration_id AND o.illustration_id IS NOT NULL
           AND o.card_name = c.card_name)                                AS reprints_same_name,
       (SELECT count(DISTINCT o.card_set_code) FROM magic.cards o
         WHERE o.illustration_id = c.illustration_id AND o.illustration_id IS NOT NULL) AS distinct_sets,
       -- days since epoch, so it compares numerically like the other features
       (SELECT min(o.released_at) - DATE '1970-01-01' FROM magic.cards o
         WHERE o.illustration_id = c.illustration_id AND o.illustration_id IS NOT NULL) AS art_first_seen
FROM magic.cards c
WHERE c.scryfall_id::text IN ({ids})
"""

# name -> (higher value means preferred?, human-readable hypothesis)
CANDIDATES = {
    "prefer_score": (True, "the current scoring function, overall"),
    "reprints_same_name": (True, "more-reprinted art is preferred (today's illustration_count)"),
    "distinct_sets": (True, "art appearing in more distinct sets is preferred"),
    "art_first_seen": (False, "the ORIGINAL art is preferred (earlier first appearance)"),
}


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


def binomial_two_sided_p(wins: int, n: int) -> float:
    """Exact two-sided binomial test against p=0.5, no scipy dependency."""
    if n == 0:
        return 1.0
    k = min(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", type=Path, help="JSONL exported from the labelling page")
    args = ap.parse_args()

    labels = [json.loads(line) for line in args.labels.read_text().splitlines() if line.strip()]
    if not labels:
        sys.exit("no labels in file")

    decided = [x for x in labels if x["verdict"] in ("left", "right")]
    other = len(labels) - len(decided)
    print(f"{len(labels)} labels: {len(decided)} decided, {other} no-preference/can't-tell ({100 * other / len(labels):.0f}%)")
    by_mode: dict[str, int] = {}
    for x in labels:
        by_mode[x["mode"]] = by_mode.get(x["mode"], 0) + 1
    print(f"  by mode: {by_mode}")
    if not decided:
        sys.exit("\nNo decided labels yet — nothing to score.")

    sids = {s for x in decided for s in (x["left_sid"], x["right_sid"])}
    rows = run_query(FEATURE_SQL.format(ids=",".join(f"'{s}'" for s in sorted(sids))))
    feats = {r["sid"]: r for r in rows}
    missing = sids - feats.keys()
    if missing:
        print(f"  warning: {len(missing)} labelled printings not found in the DB (stale labels?)")

    print(f"\nAgreement with {len(decided)} decided labels (chance = 50%):\n")
    print(f"  {'feature':22s} {'agree':>7s} {'n':>5s} {'p':>8s}   hypothesis")
    for name, (higher_is_better, hypothesis) in CANDIDATES.items():
        agree = n = 0
        for x in decided:
            chosen, rejected = x["chosen_sid"], (x["right_sid"] if x["verdict"] == "left" else x["left_sid"])
            if chosen not in feats or rejected not in feats:
                continue
            a, b = feats[chosen].get(name), feats[rejected].get(name)
            if a in (None, "") or b in (None, ""):
                continue
            a, b = float(a), float(b)
            if a == b:
                continue  # feature cannot discriminate this pair; not a miss
            n += 1
            agree += (a > b) if higher_is_better else (a < b)
        if n == 0:
            print(f"  {name:22s} {'—':>7s} {0:5d} {'—':>8s}   {hypothesis} (never discriminates)")
            continue
        pct = 100 * agree / n
        p = binomial_two_sided_p(agree, n)
        flag = "  *" if p < ALPHA else ""
        print(f"  {name:22s} {pct:6.1f}% {n:5d} {p:8.3f}{flag}   {hypothesis}")

    print(
        f"\n  * = differs from chance at p < {ALPHA}. Pairs where a feature ties are excluded from its"
        "\n  own row, so `n` differs per feature — a feature that ties constantly is weak evidence"
        "\n  even at high agreement."
    )


if __name__ == "__main__":
    main()
