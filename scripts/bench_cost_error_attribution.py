"""When a cost estimate is wrong, WHICH of the three possible causes is it?

Agreement tells you a cell is off. It does not tell you what to fix, and the three candidates need
completely different work:

1. **Features** — the cardinality/domain estimates fed to the model are wrong. No coefficient can
   repair this, and a fit will quietly bury the error in whichever term correlates with it.
2. **Model form** — the arm is not the right shape. No coefficients fit, however you choose them.
3. **Coefficients** — the shape is right and the rates are stale or mis-fit.

They are separable because the executors publish what they REALLY did. Substituting realized counters
for estimated features isolates (1); refitting coefficients on those realized features isolates (3);
whatever error survives both is (2), the floor imposed by the arm's shape.

    err_shipped   = |ln(measured / predict(shipped coeffs, ESTIMATED features))|   <- what agreement sees
    err_features  = |ln(measured / predict(shipped coeffs, REALIZED features))|
    err_best      = |ln(measured / predict(FITTED coeffs,  REALIZED features))|

    feature-error share      = err_shipped  - err_features
    coefficient-error share  = err_features - err_best
    model-form floor         = err_best

Scoped to GatheredScan and StreamedSelect: they are the two plans with full executor counters, and
they take the large majority of queries. `cards_visited` and `matches_pushed` are exact
(verified 1.00 against their features on the candidates branch).

`scan_units` is now substituted too, from `printings_examined`. It used to be left alone because the
only counter available was `printing_span` (then named `printings_scanned`), which counts the printing
SPAN of each visited card rather than rows examined -- so in card mode, where the kernels break at the
first qualifying printing, it overstates real work and substituting it would have manufactured a
feature error that was really a counter definition. `printings_examined` is reported by the match
kernels themselves, so the feature share here is no longer a LOWER bound.

    .venv/bin/python scripts/bench_cost_error_attribution.py --seconds 600 --mode realistic
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import pathlib
import random
import statistics
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.fit_cost_model as fitmod  # noqa: E402
from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402
from scripts.fit_cost_model import collect, fit_log_ratio  # noqa: E402

# Mirrors cost.rs. Kept in the order each arm's design_row emits, so a fitted vector prints against
# the shipped one term by term.
SHIPPED: dict[str, dict[str, float]] = {
    "GatheredScan": {
        "card_pass": 5.77,
        "scan_per_row": 1.72,
        "residual_floor": 15.0,
        "push": 3.30,
        "page_slot": 2.79,
        "fixed": 237.0,
    },
    "StreamedSelect": {
        "card_pass": 3.29,
        "scan_per_row": 2.13,
        "residual_floor": 9.0,
        "emit": 0.12,
        "small_total_floor": 0.81,
        "corpus_pass": 0.288,
        "fixed": 233.0,
    },
}
STREAM_MIN_MATCHES = 1024
# The attribution needs the TRUE best fit, so the ridge that anchors `fit_log_ratio` to the shipped
# values for shipping purposes is switched off here. Left on, it holds coefficients near where they
# already are and the error it cannot remove gets misattributed to the model's shape.
UNANCHORED_RIDGE = 0.0
MIN_ROWS_PER_CELL = 200


@dataclasses.dataclass(frozen=True)
class Feats:
    """The handful of feature values an arm reads, bundled so `terms` stays a two-argument call."""

    eval_domain: float
    scan_units: float
    matches: float
    n_cards: float
    tier_ns: float
    page_span: float


def terms(plan: str, f: Feats) -> list[float]:
    """The arm's design row: one entry per coefficient, in SHIPPED order. Mirrors cost.rs exactly."""
    residual_on = 1.0 if f.tier_ns > 0.0 else 0.0
    if plan == "GatheredScan":
        # eval_domain*(card_pass + max(tier,floor) if residual) + scan_units*scan_per_row
        #   + matches*push + page_span*page_slot + fixed.
        # Where the tier EXCEEDS the floor its excess is not scaled by any coefficient, so it rides
        # `fixed_offset` rather than a fitted column.
        return [f.eval_domain, f.scan_units, f.eval_domain * residual_on, f.matches, f.page_span, 1.0]
    floor_on = f.n_cards if f.matches <= STREAM_MIN_MATCHES else 0.0
    return [f.eval_domain, f.scan_units * residual_on, f.eval_domain * residual_on, f.matches, floor_on, f.n_cards, 1.0]


def fixed_offset(plan: str, eval_domain: float, tier_ns: float) -> float:
    """The part of the residual charge the coefficients do not scale: the tier where it beats the floor."""
    if tier_ns <= 0.0:
        return 0.0
    floor = SHIPPED[plan]["residual_floor"]
    return eval_domain * max(tier_ns - floor, 0.0)


def predict(coeffs: list[float], row: list[float], offset: float) -> float:
    """A prediction from a coefficient vector, plus the unscaled tier offset."""
    return sum(c * v for c, v in zip(coeffs, row, strict=True)) + offset


def rows_for(samples: list[dict], plan: str, *, realized: bool) -> tuple[list[list[float]], list[float], list[float]]:
    """Design rows, targets and tier offsets for one plan, on estimated or realized features."""
    design, targets, offsets = [], [], []
    for s in samples:
        if s["plan"] != plan or not s["ns_round_total"] or not s["cards_visited"]:
            continue
        a = s["acq"]
        tier = a["residual_tier_ns100"] / 100.0
        # Realized: substitute all three counters. `printings_examined` is the match kernels' own
        # report of rows touched, so unlike the `printing_span` it replaces it IS a drop-in for
        # `scan_units` -- see the module docstring.
        eval_domain = float(s["cards_visited"] if realized else a["eval_domain"])
        matches = float(s["matches_pushed"] if realized else a["matches"])
        scan_units = float(s["printings_examined"] if realized else a["scan_units"])
        design.append(
            terms(
                plan,
                Feats(
                    eval_domain=eval_domain,
                    scan_units=scan_units,
                    matches=matches,
                    n_cards=float(a["n_cards"]),
                    tier_ns=tier,
                    page_span=float(min(s["offset"] + s["limit"], matches)),
                ),
            )
        )
        targets.append(s["measured"])
        offsets.append(fixed_offset(plan, eval_domain, tier))
    return design, targets, offsets


def err_median(design: list[list[float]], targets: list[float], offsets: list[float], coeffs: list[float]) -> float:
    """MEDIAN |ln(measured/predicted)| — what a TYPICAL query sees.

    Reported alongside the mean because the two diverge sharply here and only the mean is usable for
    the attribution. `GatheredScan/printing_compose` reads 0.95 mean against ~0.15 median: most
    queries are close and a tail is 10x+ out. A mean of 0.95 invites reading "2.6x typical", which is
    wrong. Use the mean to compare the three stages, the median to judge the typical query, and the
    gap between them to spot a cell whose problem is its tail rather than its centre.
    """
    out = []
    for row, y, off in zip(design, targets, offsets, strict=True):
        p = predict(coeffs, row, off)
        if p > 0 and y > 0:
            out.append(abs(math.log(y / p)))
    return statistics.median(out) if out else math.nan


def err(design: list[list[float]], targets: list[float], offsets: list[float], coeffs: list[float]) -> float:
    """MEAN |ln(measured/predicted)|.

    Mean, not median, on purpose: `fit_log_ratio` minimises squared log error, so scoring with the
    mean makes the three stages monotone by construction -- each can only reduce error or hold. Scored
    on the median, a refit that improves the bulk while worsening the middle produces a NEGATIVE
    coefficient share, which is meaningless as an attribution (observed on StreamedSelect/plane).
    """
    out = []
    for row, y, off in zip(design, targets, offsets, strict=True):
        p = predict(coeffs, row, off)
        if p > 0 and y > 0:
            out.append(abs(math.log(y / p)))
    return statistics.fmean(out) if out else math.nan


def attribute(samples: list[dict], plan: str, cell: str) -> dict | None:
    """Split one cell's error into feature, coefficient and model-form parts."""
    subset = [s for s in samples if s["acq"]["count_source"] == cell] if cell else samples
    est = rows_for(subset, plan, realized=False)
    real = rows_for(subset, plan, realized=True)
    if len(est[0]) < MIN_ROWS_PER_CELL:
        return None
    shipped = list(SHIPPED[plan].values())
    order = [k for k in SHIPPED[plan] if k != "residual_floor"]
    # `terms` emits the residual column third, where the shipped floor already sits, so the shipped
    # vector is already in emitted order.
    emitted = list(shipped)
    e_ship = err(*est, emitted)
    e_ship_med = err_median(*est, emitted)
    e_feat = err(*real, emitted)
    # `fit_log_ratio` knows nothing about `fixed_offset`, so it must be fitted against the target with
    # that offset REMOVED -- otherwise the fit solves `Xc ~= y` while the metric scores `Xc + offset`,
    # which is a different problem and shows up as a negative (impossible) coefficient share.
    net = [max(y - off, 1.0) for y, off in zip(real[1], real[2], strict=True)]
    saved, fitmod.RIDGE_STRENGTH = fitmod.RIDGE_STRENGTH, UNANCHORED_RIDGE
    try:
        fitted = fit_log_ratio(real[0], net, emitted, [1.0] * len(net))
    finally:
        fitmod.RIDGE_STRENGTH = saved
    e_best = err(*real, fitted)
    return {
        "n": len(est[0]),
        "shipped": e_ship,
        "shipped_median": e_ship_med,
        "features": e_feat,
        "best": e_best,
        "fitted": fitted,
        "emitted": emitted,
        "order": ["card_pass", "scan_units", "residual", *order[2:]],
    }


def main() -> None:
    """Collect a large sample, then attribute each cell's error to features / coefficients / form."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=600.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--mode", choices=MODES, default="uniform", help="diagnostic: uniform reaches the rare tails where model error hides"
    )
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".attrib.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    samples = collect(engine, random.Random(args.seed), args.seconds, sampler)
    print(f"\n{len(samples):,} plan-rows, mode={args.mode}")

    cells = ["", *sorted({s["acq"]["count_source"] for s in samples})]
    for plan in ("GatheredScan", "StreamedSelect"):
        print(f"\n=== {plan} ===")
        print(f"  {'acquire':<22}{'n':>7}{'mean':>8}{'median':>8}{'FEATURES':>10}{'COEFFS':>9}{'form floor':>12}  dominant")
        for cell in cells:
            a = attribute(samples, plan, cell)
            if a is None:
                continue
            feat, coef = a["shipped"] - a["features"], a["features"] - a["best"]
            parts = {"features": max(feat, 0.0), "coefficients": max(coef, 0.0), "model form": a["best"]}
            tail = "  TAIL" if a["shipped"] > 2.5 * a["shipped_median"] else ""
            print(
                f"  {(cell or 'ALL'):<22}{a['n']:>7}{a['shipped']:>8.3f}{a['shipped_median']:>8.3f}"
                f"{feat:>+10.3f}{coef:>+9.3f}{a['best']:>12.3f}  {max(parts, key=parts.get)}{tail}"
            )
        a = attribute(samples, plan, "")
        if a:
            print(
                "  fitted vs shipped: "
                + "  ".join(f"{n}={f:.2f}/{s:.2f}" for n, f, s in zip(a["order"], a["fitted"], a["emitted"], strict=True))
            )
    print("\n  mean |ln| drives the attribution (monotone by construction); median is the TYPICAL query.")
    print("  TAIL marks a cell whose mean far exceeds its median -- its problem is outliers, not its centre.")
    print("  FEATURES/COEFFS are error REMOVED by fixing that cause (bigger = more of the problem).")
    print("  'form floor' is what survives exact features AND refitted coefficients: the arm's shape.")


if __name__ == "__main__":
    main()
