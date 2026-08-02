#!/usr/bin/env python3
"""Fit prefer-score weights to preference labels, then measure the change.

Two phases:

  fit     Learn weights from pairwise labels and report held-out agreement against
          the current prefer_score on the same held-out pairs. This is the only
          number that says whether the new score is better.
  impact  Score every printing under both weightings and count how many cards
          change their representative printing. Agreement says "right where we have
          labels"; impact says "how much else moved".

Method, per docs/issues/local-prefer-score-label-harness.md:

  * Bradley-Terry with covariates == logistic regression on feature differences,
    fit within-card: P(a beats b) = sigmoid(w . (x_a - x_b)).
  * **Signs are declared, not learned.** The maintainer already knows which frame
    or border they prefer; what is unknown is the magnitudes. Constraining signs
    removes the degeneracy (a deterministic answer on a one-dimension pair would
    otherwise drive its coefficient to infinity), shrinks the search space, and
    guarantees the fit can never contradict a known preference.
  * Constraints are imposed by reparameterisation, w_k = softplus(theta_k), so the
    problem stays unconstrained and plain gradient descent suffices. No numpy.

Usage:
    python scripts/fit_prefer_score.py fit ~/Downloads/prefer-score-labels.jsonl
    python scripts/fit_prefer_score.py impact ~/Downloads/prefer-score-labels.jsonl
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

# Fraction of labels held out. 70/30 mirrors the cost-model calibration in
# card_engine/src/tests.rs, whose comment names the hazard: "the 22-query trap".
TEST_FRACTION = 0.30
SPLIT_SEED = 20260725

# Plain gradient descent on the logistic likelihood. Few parameters, few hundred
# observations, so nothing cleverer is warranted.
LEARNING_RATE = 0.05
ITERATIONS = 4000
# Ridge penalty, in the reparameterised space. Small: with signs declared the fit is
# already well-posed, so this only discourages a runaway magnitude.
L2 = 1e-3

# Every feature is defined so that LARGER IS PREFERRED, which is what lets all
# weights be constrained non-negative. That encodes the declared signs.
#   art_pop      reprint count of this artwork, log-shaped like today's term
#   art_spread   distinct sets the artwork appears in, log-shaped
#   art_age      years since the artwork first appeared (older art preferred)
#   nonfoil      a nonfoil version of this printing is obtainable
#   rarity_low   commons/uncommons preferred, as today's rarity term asserts
#   highres      a high-resolution scan exists
FEATURES = ("art_pop", "art_spread", "art_age", "nonfoil", "rarity_low", "highres")

FEATURE_SQL = """
SELECT c.scryfall_id::text AS sid,
       c.prefer_score,
       (SELECT count(*) FROM magic.cards o
         WHERE o.illustration_id = c.illustration_id AND o.illustration_id IS NOT NULL
           AND o.card_name = c.card_name)                                           AS n_reprints,
       (SELECT count(DISTINCT o.card_set_code) FROM magic.cards o
         WHERE o.illustration_id = c.illustration_id AND o.illustration_id IS NOT NULL) AS n_sets,
       (SELECT min(o.released_at) FROM magic.cards o
         WHERE o.illustration_id = c.illustration_id AND o.illustration_id IS NOT NULL) AS art_first,
       COALESCE((c.raw_card_blob -> 'finishes') ? 'nonfoil', false)                  AS nonfoil,
       c.card_rarity_int                                                             AS rarity,
       ((c.raw_card_blob ->> 'image_status') = 'highres_scan')                        AS highres
FROM magic.cards c
WHERE {where}
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


def to_vector(row: dict[str, str]) -> list[float] | None:
    """Feature vector, oriented so larger is preferred for every entry."""
    try:
        n_reprints = float(row["n_reprints"] or 0)
        n_sets = float(row["n_sets"] or 0)
        rarity = float(row["rarity"]) if row["rarity"] not in (None, "") else 2.0
    except ValueError:
        return None
    if not row["art_first"]:
        return None
    year = int(row["art_first"][:4])
    return [
        math.log1p(n_reprints),  # art_pop
        math.log1p(n_sets),  # art_spread
        (2026 - year) / 10.0,  # art_age, decades old
        1.0 if row["nonfoil"] == "t" else 0.0,  # nonfoil
        (3.0 - rarity) / 3.0,  # rarity_low: common=1.0, mythic=0.0
        1.0 if row["highres"] == "t" else 0.0,  # highres
    ]


def softplus(x: float) -> float:
    return math.log1p(math.exp(x)) if x < 30 else x


def sigmoid(x: float) -> float:
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def fit(deltas: list[list[float]]) -> list[float]:
    """Maximise sum log sigmoid(w . delta) with w = softplus(theta) >= 0.

    Every delta is oriented chosen-minus-rejected, so a correct model gives every
    observation a positive score.
    """
    theta = [0.0] * len(FEATURES)
    for _ in range(ITERATIONS):
        grad = [0.0] * len(FEATURES)
        w = [softplus(t) for t in theta]
        for d in deltas:
            z = sum(wk * dk for wk, dk in zip(w, d, strict=True))
            # d/dz of log sigmoid(z) is 1 - sigmoid(z)
            g = 1.0 - sigmoid(z)
            for k, dk in enumerate(d):
                grad[k] += g * dk
        for k in range(len(FEATURES)):
            dw_dtheta = sigmoid(theta[k])  # derivative of softplus
            theta[k] += LEARNING_RATE * (grad[k] / max(1, len(deltas))) * dw_dtheta
            theta[k] -= LEARNING_RATE * L2 * theta[k]
    return [softplus(t) for t in theta]


def agreement(deltas: list[list[float]], w: list[float]) -> tuple[int, int]:
    """How many oriented deltas the weighting scores positively."""
    hit = n = 0
    for d in deltas:
        z = sum(wk * dk for wk, dk in zip(w, d, strict=True))
        if z == 0:
            continue  # cannot discriminate; not a miss
        n += 1
        hit += z > 0
    return hit, n


def load_labelled(labels_path: Path) -> tuple[list[list[float]], list[tuple[float, float]]]:
    """Oriented feature deltas plus (chosen, rejected) current prefer_score pairs."""
    labels = [json.loads(x) for x in labels_path.read_text().splitlines() if x.strip()]
    decided = [x for x in labels if x["verdict"] in ("left", "right")]
    print(f"{len(labels)} labels, {len(decided)} decided ({100 * (len(labels) - len(decided)) / len(labels):.0f}% no-preference)")

    sids = sorted({s for x in decided for s in (x["left_sid"], x["right_sid"])})
    where = f"c.scryfall_id::text IN ({','.join(repr(s) for s in sids)})"
    rows = {r["sid"]: r for r in run_query(FEATURE_SQL.format(where=where))}

    deltas, baseline = [], []
    for x in decided:
        chosen = x["chosen_sid"]
        rejected = x["right_sid"] if x["verdict"] == "left" else x["left_sid"]
        rc, rr = rows.get(chosen), rows.get(rejected)
        if not rc or not rr:
            continue
        vc, vr = to_vector(rc), to_vector(rr)
        if vc is None or vr is None:
            continue
        deltas.append([a - b for a, b in zip(vc, vr, strict=True)])
        baseline.append((float(rc["prefer_score"]), float(rr["prefer_score"])))
    return deltas, baseline


def cmd_fit(labels_path: Path) -> None:
    deltas, baseline = load_labelled(labels_path)
    idx = list(range(len(deltas)))
    random.Random(SPLIT_SEED).shuffle(idx)
    cut = int(len(idx) * (1 - TEST_FRACTION))
    train, test = idx[:cut], idx[cut:]
    print(f"usable pairs: {len(deltas)}  ->  {len(train)} train / {len(test)} held out\n")

    w = fit([deltas[i] for i in train])
    scale = max(w) or 1.0
    print("fitted weights (relative; only ratios are identified):")
    for name, wk in sorted(zip(FEATURES, w, strict=True), key=lambda kv: -kv[1]):
        print(f"  {name:12s} {wk / scale:6.3f}")

    print("\nagreement:")
    print(f"  {'':22s} {'train':>14s} {'HELD OUT':>14s}")
    for label, weights in (("fitted", w),):
        th, tn = agreement([deltas[i] for i in train], weights)
        hh, hn = agreement([deltas[i] for i in test], weights)
        print(f"  {label:22s} {f'{100 * th / tn:.1f}% ({tn})':>14s} {f'{100 * hh / hn:.1f}% ({hn})':>14s}")
    # Baseline: the current prefer_score on the identical pairs.
    for label, subset in (("current prefer_score", train), ("", test)):
        hit = n = 0
        for i in subset:
            c, r = baseline[i]
            if c == r:
                continue
            n += 1
            hit += c > r
        if label:
            cur_train = (hit, n)
        else:
            cur_test = (hit, n)
    print(
        f"  {'current prefer_score':22s} "
        f"{f'{100 * cur_train[0] / cur_train[1]:.1f}% ({cur_train[1]})':>14s} "
        f"{f'{100 * cur_test[0] / cur_test[1]:.1f}% ({cur_test[1]})':>14s}"
    )

    n = cur_test[1]
    margin = 1.96 * math.sqrt(0.25 / n) * 100 if n else 0
    print(
        f"\n  Held-out n is {n}, so the 95% band on any single figure is about "
        f"+/-{margin:.0f} points.\n  Treat held-out differences smaller than that as unproven."
    )
    Path("/tmp/fitted_weights.json").write_text(json.dumps(dict(zip(FEATURES, w, strict=True)), indent=1))
    print("  weights -> /tmp/fitted_weights.json")


def cmd_impact(labels_path: Path) -> None:
    """How many cards change representative printing under the fitted weights."""
    weights_path = Path("/tmp/fitted_weights.json")
    if not weights_path.exists():
        sys.exit("run `fit` first (writes /tmp/fitted_weights.json)")
    w = [json.loads(weights_path.read_text())[f] for f in FEATURES]

    print("scoring the whole corpus under both weightings (English, non-promo)...")
    where = "c.raw_card_blob ->> 'lang' = 'en' AND NOT COALESCE((c.raw_card_blob ->> 'promo')::bool, false)"
    rows = run_query(FEATURE_SQL.format(where=where) + " AND c.card_name IS NOT NULL")

    by_card: dict[str, list[tuple[float, float, str]]] = {}
    skipped = 0
    {r["sid"]: r for r in rows}
    # Card name is not in FEATURE_SQL's projection, so fetch the mapping separately.
    names = {
        r["sid"]: r["card_name"]
        for r in run_query(f"SELECT scryfall_id::text AS sid, card_name FROM magic.cards WHERE {where.replace('c.', '')}")
    }
    for r in rows:
        v = to_vector(r)
        if v is None or r["sid"] not in names:
            skipped += 1
            continue
        new = sum(wk * vk for wk, vk in zip(w, v, strict=True))
        by_card.setdefault(names[r["sid"]], []).append((float(r["prefer_score"]), new, r["sid"]))

    changed = multi = 0
    for printings in by_card.values():
        if len(printings) < 2:
            continue
        multi += 1
        old_pick = max(printings, key=lambda t: t[0])[2]
        new_pick = max(printings, key=lambda t: t[1])[2]
        changed += old_pick != new_pick
    print(f"\n  multi-printing cards : {multi}")
    print(f"  representative changes: {changed}  ({100 * changed / multi:.1f}%)")
    if skipped:
        print(f"  skipped (no art date): {skipped} printings")
    print(
        "\n  A large number here is not automatically bad -- it is the blast radius, and it\n"
        "  needs eyeballing against the sampled changes before the weights are trusted."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("fit", "impact"))
    ap.add_argument("labels", type=Path)
    args = ap.parse_args()
    (cmd_fit if args.cmd == "fit" else cmd_impact)(args.labels)


if __name__ == "__main__":
    main()
