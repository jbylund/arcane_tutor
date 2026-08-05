"""Fit the P3/P4 pair as LEVEL + DELTA, so absolute accuracy and ordering stop competing.

`argmin` consumes only differences, and `cost.rs` records why fitting each arm's absolute time cannot
settle these two: "P3/P4 could NOT be fit -- SCAN goes negative because `scan_units` and `matches` both
scale with printing count in the workload, a STRUCTURAL collinearity no corpus size fixes". A collinear
direction is one the absolute objective cannot see. It is often one the DIFFERENCE can.

And the difference is where the losses are. On the dispatch basis `StreamedSelect -> GatheredScan` and
`GatheredScan -> StreamedSelect` are 36% and 21% of all routing regret, both directions large -- the
signature of a model that cannot separate two plans near the margin rather than one biased about
either. The confusion is almost entirely on queries WITH a residual (22% order-wrong against 1% for
`all_match`) and ABOVE `STREAM_MIN_MATCHES` (25% against 3%), which is exactly where the arms diverge:
P3 charges `scan_units * 5.97` gated on a residual, P4 charges `scan_units * 2.06` unconditionally.

## Why fitting the gap alone is not enough

Fitting only `log(real3/real4)` was tried and it works on its own terms -- held-out order agreement
86.9% -> 94.4%, and 80.5% -> 92.1% on residual queries. But it wrecked what it was not looking at:
StreamedSelect's absolute within-25% fell 58% -> 23%, and the regret it bought in one direction it
gave back in the other (`GatheredScan -> StreamedSelect` share 22% -> 6%, `StreamedSelect ->
GatheredScan` 34% -> 43%). A gap objective is blind to the overall level, so nothing stopped it
wandering there.

## The re-parameterisation

Terms whose FEATURE appears in both arms are written as a level plus a delta:

    P4 cost = sum   x_k        * f_k
    P3 cost = sum ( x_k + d_k) * f_k

`x` is what the absolute objective identifies; `d` is what the gap objective identifies. Written this
way the two objectives inform different parameters instead of pulling on the same ones, so both can be
optimised at once:

    minimise  ABS_WEIGHT * sum over both plans (log pred - log real)^2
            + GAP_WEIGHT * sum (log(pred3/pred4) - log(real3/real4))^2
            + ridge

with the ridge TIGHT on `x` and loose on `d` -- the level is already calibrated and the delta is the
part known to be wrong. Terms unique to one arm (P3's artwork/small-total/corpus-pass/emit, P4's
push/select) have no counterpart and are fitted directly.

The gap term is weighted by |real gap|, because regret is milliseconds and not decisions: a wrong call
on a 300us gap costs 300x one on a 1us gap. Unweighted, the fit took held-out order agreement
86.2% -> 94.1% and left total regret FLAT (63.6 -> 62.1 ms, 66.5 -> 68.0 ms on two seeds) by trading
404 cheap wrong-GatheredScan picks for 230 dear wrong-StreamedSelect ones.

## DO NOT SHIP THIS FITTER'S OUTPUT. It is a diagnostic.

Every version of this was validated against `bench_regret_matrix.py`, and the best of them made real
routing WORSE:

    variant                     held-out order   held-out lost   ACTUAL total regret
    current                              86.5%         188 ms    60.4 / 69.7 ms
    gap only                             94.4%              -    62.1 / 68.0 ms
    level+delta, unweighted              94.1%              -    62.1 / 68.0 ms
    level+delta, time-weighted           94.1%          22 ms    80.1 / 77.9 ms  <-- worse

The reason is a flaw in the objective, not in the arithmetic: **routing is a multi-way argmin and this
fits a pair.** `P3 = level + delta`, so moving the delta moves P3's ABSOLUTE cost -- and P3 then wins
more often against `PrintingCompose` and the range plans too, which this objective cannot see. The
time-weighted fit dropped P3's rates hard (CARD_PASS 5.05 -> 3.08, SCAN_PER_ROW 5.97 -> 2.45), fixed
the direction it was looking at (`GatheredScan -> StreamedSelect` 544 -> 106 misroutes) and created
far more of the other (`StreamedSelect -> GatheredScan` 703 -> 1184).

What the pairwise view DID establish, and what is worth keeping:

- The confusion is localised: residual queries above `STREAM_MIN_MATCHES`, 22% order-wrong against 1%
  for `all_match`.
- The discriminating term is the per-row scan rate, and its shipped 3x split (5.97 against 2.06) is
  far too wide. Every weighting drove `D:SCAN_PER_ROW` down hard -- to 0.37 when time-weighted, i.e.
  the two rates want to be nearly EQUAL. That is a real finding about the model's shape.
- Absolute agreement and ordering do NOT have to trade, once re-parameterised: the time-weighted fit
  improved both medians and `within` while improving order.

A sound version needs the objective to be the actual routing outcome over ALL applicable plans -- the
regret matrix as the loss, not a pairwise proxy for it.

    .venv/bin/python scripts/fit_plan_gap.py --seconds 300   # build --features routed-phases
"""

from __future__ import annotations

import argparse
import math
import pathlib
import random
import statistics
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts import fit_cost_model as fitmod  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

P3, P4 = "StreamedSelect", "GatheredScan"
#: Terms whose feature column is the same in both arms, so a level/delta split is meaningful. Keyed by
#: the P3 term name; the value is P4's name for the same feature.
SHARED: dict[str, str] = {
    "CARD_PASS": "CARD_PASS",
    "SCAN_PER_ROW": "SCAN_PER_ROW",
    "RESIDUAL_FLOOR": "RESIDUAL_FLOOR",
    "FIXED": "FIXED",
}
# Both objectives matter, and the gap one is what routing reads, so it carries more weight. Absolute
# accuracy is not free to abandon though: several harnesses read `predicted_ns` as a time.
ABS_WEIGHT = 1.0
GAP_WEIGHT = 3.0
# Ridge, relative to the shipped value. TIGHT on the level (already calibrated by the absolute fit) and
# loose on the delta (the part the collinearity left unidentified).
RIDGE_LEVEL = 0.5
RIDGE_DELTA = 0.01
NUM_SWEEPS = 10
SEARCH_SPAN = 4.0
GOLDEN_ITERS = 32
BRACKET_EPS = 1e-9
# A gap below this is a tie no model can be expected to call; grading ties makes order agreement look
# worse than the routing it produces.
TIE_FLOOR_US = 1.0
# Below this a shipped value is effectively zero and a relative ridge would divide by nothing.
ZERO_EPS = 1e-9
# The agreement band every other harness grades against, so these numbers are comparable to theirs.
AGREE_LO, AGREE_HI = 0.8, 1.25


def term_names() -> dict[str, list[str]]:
    """Each arm's term names, straight from `design_row` so they cannot drift from the design."""
    dummy = {
        "eval_domain": 1,
        "scan_units": 1,
        "matches": 1,
        "n_cards": 1,
        "n_printings": 1,
        "residual_tier_ns100": 100,
        "artwork_seen_cards": 0,
        "compose_paging": "Gather",
        "broadcast_printings": 0,
        "scatter_printings": 0,
        "project_printings": 0,
        "popcount_words": 0,
        "compose_scan_printings": 0,
        "gather_group_printings": 0,
    }
    return {plan: fitmod.design_row(plan, dummy, 100, 0)[1] for plan in (P3, P4)}


def collect_pairs(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per query where BOTH plans ran and are priceable, with each side's design row."""
    rows: list[dict] = []
    budget = costbench.Budget(seconds=seconds, warmups=fitmod.NUM_WARMUPS, trials=fitmod.NUM_TRIALS)
    for sample in costbench.iter_samples(engine, sampler, rng, budget, vary_prefer=True):
        plans = {p["plan"]: p for p in sample.plans}
        if not all(n in plans and plans[n]["trials_ns"] for n in (P3, P4)):
            continue
        selves = {n: costbench.plan_self_ns(plans[n], sample.acquire) for n in (P3, P4)}
        if any(v is None or v <= 0 for v in selves.values()):
            continue
        designs = {n: fitmod.design_row(n, sample.acquire, sample.kw["limit"], sample.kw["offset"]) for n in (P3, P4)}
        if any(d is None for d in designs.values()):
            continue
        rows.append(
            {
                "real": {n: selves[n] for n in (P3, P4)},
                "row": {n: designs[n][0] for n in (P3, P4)},
                "off": {n: designs[n][2] for n in (P3, P4)},
                "gap_us": (selves[P3] - selves[P4]) / 1000.0,
                # TIME weight. Regret is milliseconds, not decisions: getting the order wrong on a
                # 300us gap costs 300x what getting it wrong on a 1us gap does. An unweighted fit
                # treats those equally and will happily trade many cheap wins for a few dear losses --
                # measured, exactly that happened. The level+delta fit took held-out order agreement
                # 86.2% -> 94.1% and left total regret flat (63.6 -> 62.1 ms, 66.5 -> 68.0 ms on two
                # seeds), because it converted 404 cheap wrong-GatheredScan picks into 230 expensive
                # wrong-StreamedSelect ones (28.66us against 12.46us mean).
                "weight": abs(selves[P3] - selves[P4]) / 1000.0,
                "tier": "residual" if sample.acquire["residual_tier_ns100"] > 0 else "all_match",
            }
        )
    return rows


def coeffs_from(params: dict[str, float], names: dict[str, list[str]]) -> dict[str, list[float]]:
    """Expand the level/delta parameters back into one coefficient vector per arm."""
    out: dict[str, list[float]] = {}
    out[P4] = [params[f"{P4}:{nm}"] for nm in names[P4]]
    p3: list[float] = []
    for nm in names[P3]:
        if nm in SHARED:
            p3.append(max(params[f"{P4}:{SHARED[nm]}"] + params[f"D:{nm}"], 0.0))
        else:
            p3.append(params[f"{P3}:{nm}"])
    out[P3] = p3
    return out


def predict(coeffs: dict[str, list[float]], r: dict, plan: str) -> float:
    """One arm's predicted dispatch cost, mirroring `design_row`'s row-plus-offset contract."""
    return max(sum(c * v for c, v in zip(coeffs[plan], r["row"][plan], strict=True)) + r["off"][plan], 1.0)


def objective(params: dict[str, float], rows: list[dict], names: dict[str, list[str]], base: dict[str, float]) -> float:
    """Weighted absolute + gap log error, plus the split ridge."""
    coeffs = coeffs_from(params, names)
    total = 0.0
    for r in rows:
        q3, q4 = predict(coeffs, r, P3), predict(coeffs, r, P4)
        total += ABS_WEIGHT * (math.log(q3 / r["real"][P3]) ** 2 + math.log(q4 / r["real"][P4]) ** 2)
        # The gap term carries the TIME weight -- see the `weight` field. The absolute term does not:
        # absolute accuracy is wanted uniformly across queries, not concentrated on the expensive ones.
        total += GAP_WEIGHT * r["weight"] * (math.log(q3 / q4) - math.log(r["real"][P3] / r["real"][P4])) ** 2
    pull = 0.0
    for k, v in params.items():
        b = base[k]
        strength = RIDGE_DELTA if k.startswith("D:") else RIDGE_LEVEL
        denom = abs(b) if abs(b) > ZERO_EPS else 1.0
        pull += strength * ((v - b) / denom) ** 2
    # Scaled by the same total weight the data term carries, so the ridge's relative strength does not
    # change when the gap weighting does.
    scale = sum(1.0 + GAP_WEIGHT * r["weight"] for r in rows)
    return total + scale * pull


def fit(rows: list[dict], names: dict[str, list[str]], base: dict[str, float]) -> dict[str, float]:
    """Coordinate descent with a golden-section line search per parameter."""
    params = dict(base)
    inv = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(NUM_SWEEPS):
        for key in params:
            b = base[key]
            span = max(abs(b) * SEARCH_SPAN, 1.0)
            lo, hi = (b - span, b + span) if key.startswith("D:") else (0.0, b + span)
            a, c = lo + (1 - inv) * (hi - lo), lo + inv * (hi - lo)
            params[key] = a
            fa = objective(params, rows, names, base)
            params[key] = c
            fc = objective(params, rows, names, base)
            for _ in range(GOLDEN_ITERS):
                if hi - lo < BRACKET_EPS:
                    break
                if fa < fc:
                    hi, c, fc = c, a, fa
                    a = lo + (1 - inv) * (hi - lo)
                    params[key] = a
                    fa = objective(params, rows, names, base)
                else:
                    lo, a, fa = a, c, fc
                    c = lo + inv * (hi - lo)
                    params[key] = c
                    fc = objective(params, rows, names, base)
            params[key] = (lo + hi) / 2.0
    return params


def report(label: str, coeffs: dict[str, list[float]], rows: list[dict]) -> None:
    """Order agreement AND the time those wrong calls cost, plus each arm's absolute ratio.

    Both columns are needed and they can disagree. Order agreement counts queries; regret counts
    milliseconds. A model that calls more queries right can still lose more time, by trading cheap
    wins for dear losses -- measured, and it is why `lost ms` is here.
    """
    graded = [r for r in rows if abs(r["gap_us"]) >= TIE_FLOOR_US]
    right, lost_us = 0, 0.0
    for r in graded:
        ok = (predict(coeffs, r, P3) > predict(coeffs, r, P4)) == (r["real"][P3] > r["real"][P4])
        right += ok
        if not ok:
            lost_us += abs(r["gap_us"])
    abs3 = statistics.median(predict(coeffs, r, P3) / r["real"][P3] for r in rows)
    abs4 = statistics.median(predict(coeffs, r, P4) / r["real"][P4] for r in rows)
    within = statistics.fmean(
        1.0 if AGREE_LO <= predict(coeffs, r, p) / r["real"][p] <= AGREE_HI else 0.0 for r in rows for p in (P3, P4)
    )
    print(f"  {label:<18}{right / max(len(graded), 1):>14.1%}{lost_us / 1000:>11.1f}{abs3:>10.2f}{abs4:>10.2f}{within:>12.0%}")


def main() -> None:
    """Collect pairs, fit level+delta on half, and score ordering and absolutes on the held-out half."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=240.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".gapfit.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    rows = collect_pairs(engine, sampler, random.Random(args.seed), args.seconds)
    print(f"\n{len(rows):,} queries where both {P3} and {P4} ran and are priceable")
    if len(rows) < fitmod.MIN_ROWS_FOR_FIT:
        print("too few rows to fit")
        return

    names = term_names()
    cur = {plan: list(fitmod.CURRENT[plan]) for plan in (P3, P4)}
    base: dict[str, float] = {f"{P4}:{nm}": v for nm, v in zip(names[P4], cur[P4], strict=True)}
    for nm, v in zip(names[P3], cur[P3], strict=True):
        if nm in SHARED:
            base[f"D:{nm}"] = v - cur[P4][names[P4].index(SHARED[nm])]
        else:
            base[f"{P3}:{nm}"] = v

    train = [r for i, r in enumerate(rows) if i % 2 == 0]
    test = [r for i, r in enumerate(rows) if i % 2 == 1]
    fitted = fit(train, names, base)

    print(f"\n{'parameter':<34}{'current':>10}{'fitted':>10}{'x':>8}")
    for key in sorted(base):
        b, f = base[key], fitted[key]
        print(f"{key:<34}{b:>10.2f}{f:>10.2f}{(f / b if abs(b) > ZERO_EPS else float('nan')):>8.2f}")

    print(f"\n  {'variant':<18}{'order correct':>14}{'lost ms':>11}{'P3 abs':>10}{'P4 abs':>10}{'within':>12}")
    for label, rs in (("-- train", train), ("-- TEST", test)):
        print(f" {label}")
        report("current", cur, rs)
        report("level+delta", coeffs_from(fitted, names), rs)
    for tier in ("residual", "all_match"):
        sub = [r for r in test if r["tier"] == tier]
        if len(sub) < 200:  # noqa: PLR2004 - a thin tier says nothing
            continue
        print(f" -- TEST, {tier} ({len(sub):,})")
        report("current", cur, sub)
        report("level+delta", coeffs_from(fitted, names), sub)


if __name__ == "__main__":
    main()
