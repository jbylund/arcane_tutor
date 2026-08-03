"""Do the cost model's FEATURES match what the executor actually does? Per cell, against counters.

First link in the chain the rest of this toolkit walks:

    cardinality estimate -> cost model -> coefficients -> plan choice

Everything else here measures the far end. `bench_cost_model_agreement.py` and
`bench_cost_error_percentiles.py` compare *predicted time* to *real time*, which conflates all four
links; `fit_cost_model.py` fits coefficients and can only do so honestly once the features are right.
This tool isolates the first link by comparing each feature to the executor counter that realizes it,
so a mis-counted feature is visible as itself rather than as a rate that will not calibrate.

Why it earns a place: the compose branch handed the PRINTING count to `result_total` in artwork mode,
where it is consumed as a per-result push count. `matches_pushed` is deduped, so the feature read a
median 1.95x the truth. That survived 154k paired A/B queries, both percentile matrices and a
coefficient fit, because a 2x feature error is absorbed by whatever rate correlates with it and shows
up only as spread. Sliced here it is a single cell reading 1.95 with everything else near 1.0.

Ratio is **feature / counter**, so **>1 means the feature OVER-counts** the work done.

Read the same way as `bench_cost_error_percentiles.py`: a tight row off 1.0 is a systematic bias worth
fixing outright, a wide row means the feature is right on average but driven by something unmodelled,
and slicing by distinct-on matters because a feature can be exact in one mode and 2x off in another
while the pooled median looks fine.

    .venv/bin/python scripts/bench_feature_accuracy.py --seconds 60
"""

from __future__ import annotations

import argparse
import math
import pathlib
import random
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

# Deliberately below the shared (2, 7) default, and the one place in the toolkit where that is
# justified: the counters this harness reads are deterministic for a given query, so the trials exist
# only to make each plan run once. Extra rounds buy nothing and cost sample breadth.
NUM_WARMUPS = 1
NUM_TRIALS = 2
MIN_ROWS = costbench.MIN_ROWS
# A cell's median must land inside this to be considered calibrated, matching the agreement bar in
# bench_cost_model_agreement.py so the two tools grade on the same scale.
AGREE_LO, AGREE_HI = 0.8, 1.25
# Below this the counter is too small for a ratio to mean anything -- a feature of 3 against a counter
# of 1 is a 3x "error" that costs nanoseconds.
MIN_COUNTER = 100

# feature name on the shared acquire vector -> the executor counter that realizes it.
#
# `scan_units` is graded against `printings_examined`, NOT `printing_span`. The latter is the
# printing SPAN under the candidate cards, computed by the caller before the match kernel runs, so it
# reports what a full scan would have cost rather than what happened. The two coincide in printing and
# artwork mode -- those loops really do traverse the span -- which is why grading against the span
# looked fine everywhere except card mode, where every kernel short-circuits. Reading the span as the
# work done is how `cost.rs` came to assert that the scan plans "walk the full printing span of their
# candidates in CARD mode too, not one row each"; they do not.
PAIRS = (
    ("matches", "matches_pushed"),
    ("eval_domain", "cards_visited"),
    ("scan_units", "printings_examined"),
)
# Kept alongside so one run shows both gradings. The gap between the two columns IS the miscount, and
# reporting it as a column beats asserting it in prose.
SPAN_COUNTER = "printing_span"


#: Which of the three PAIRS features each compose paging branch's cost arm actually multiplies by a
#: rate. `Perm` and `OrderbyWalk` are priced `printings_walked * WALK_STEP + limit * WALK_EMIT_PER_ROW`
#: -- they charge NEITHER `matches` nor `eval_domain`, and grading those against a walk that stops at
#: `page_offset + limit` produced cells reading 100-200x off numbers the model never reads. Only
#: `Gather` charges all three (`eval_domain`, `compose_scan_printings`, `matches`).
COMPOSE_ARM_CHARGES: dict[str, frozenset[str]] = {
    "Perm": frozenset({"printings_walked"}),
    "OrderbyWalk": frozenset({"printings_walked"}),
    "Gather": frozenset({"eval_domain", "compose_scan_printings", "matches"}),
    # The walk was available, was attempted, declined, and fell into the gather -- so the gather is
    # what ran and the gather's terms are what to grade.
    "GatherWalkDeclined": frozenset({"eval_domain", "compose_scan_printings", "matches"}),
}


def compose_grades(paging_taken: str, feat: str) -> bool:
    """Whether compose's arm charges `feat` on the branch it actually took.

    Keyed on `paging_taken` -- what RAN -- not on the acquire's `compose_paging`, which is the
    model's PREDICTION of the branch. Those disagree exactly where a walk was predicted and declined,
    and grading a gather's counters against a walk's terms is how the two get conflated.
    """
    charged = COMPOSE_ARM_CHARGES.get(paging_taken)
    # An exit with no cost arm of its own (EmptyPage, the declines): nothing ran that a term describes.
    return feat in charged if charged is not None else False


def scan_feature(plan: str, paging: str, tier_ns100: int) -> str | None:
    """Which feature the ARM actually charges its printing scan on, or None if it charges none.

    One shared vector costs every plan, but they do not all read the same field, and comparing a
    counter to a feature the arm never touches manufactures a defect. Compose walks the set bits of
    its composed bitmap (`compose_scan_printings`) when it pages by Gather, and stops at
    `page_offset + limit` when it pages by walking (`printings_walked`); only the materializing scan
    plans read `scan_units`.

    `StreamedSelect` reads it only WITH a residual. Its arm is
    `if tier_ns > 0.0 { scan_units * STREAM_SCAN_PER_ROW_NS } else { 0.0 }` -- with `all_match` (tier
    0) P3 walks no printings and the term is switched off entirely. Graded anyway, those rows read
    p50 2.72 / p70 3.08 against `printings_examined`, because `scan_units` there is GatheredScan's
    full-span quantity while StreamedSelect's counting kernel answers existence from the first
    matching printing. That is not a feature error -- it is a number the model never multiplies by
    anything -- and reporting it as one sent `fit_cost_model.py`'s `counter_check` into refusing to
    fit this plan at all.
    """
    if plan == "PrintingCompose":
        # `GatherWalkDeclined` IS the gather -- a walk was attempted, declined, and fell into it.
        return "compose_scan_printings" if paging in ("Gather", "GatherWalkDeclined") else "printings_walked"
    if plan == "StreamedSelect" and tier_ns100 == 0:
        return None
    return "scan_units"


percentile = costbench.percentile


def collect(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per (query, plan, feature) where the plan reported the matching counter."""
    rows: list[dict] = []
    budget = costbench.Budget(seconds=seconds, warmups=NUM_WARMUPS, trials=NUM_TRIALS)
    # `prefer` is sampled, not pinned: it decides whether the card-mode kernels stop at the first
    # qualifying printing or must score every one, which is the single largest per-card work
    # difference any sampled parameter reaches -- and the cost model cannot see it, since
    # `PlanFeatures` does not carry `prefer` (see `explain`'s doc in lib.rs). A run pinned to
    # `default` measures only the short-circuiting path and reads the feature as though the
    # long path did not exist.
    for sample in costbench.iter_samples(engine, sampler, rng, budget, vary_prefer=True):
        acq = sample.acquire
        for plan in sample.plans:
            if not plan["trials_ns"]:
                continue  # declined: it ran nothing, so there is no counter to check against
            # `compose_paging` is the model's PREDICTION; `paging_taken` is what ran. Label and grade
            # compose on the latter -- they disagree exactly where a walk was predicted and declined.
            paging = plan.get("paging_taken") if plan["plan"] == "PrintingCompose" else acq["compose_paging"]
            for feat, counter in PAIRS:
                if feat == "scan_units":
                    feat = scan_feature(plan["plan"], paging, acq["residual_tier_ns100"])  # noqa: PLW2901 - the arm decides
                    if feat is None:
                        continue  # this arm charges no scan term for this query; there is nothing to grade
                if plan["plan"] == "PrintingCompose" and not compose_grades(paging, feat):
                    continue  # this branch's arm never multiplies this feature by a rate
                got = plan.get(counter)
                if got is None or got < MIN_COUNTER:
                    continue
                span = plan.get(SPAN_COUNTER)
                rows.append(
                    {
                        "feature": feat,
                        "plan": plan["plan"],
                        "acquire": acq["count_source"],
                        "unique": sample.kw["unique"],
                        "orderby": sample.kw["orderby"],
                        # Sampled, so this slice is the one that separates the short-circuiting
                        # card-mode kernels from the ones that must score every printing.
                        "prefer": sample.kw.get("prefer", "default"),
                        # Compose's arm reads `scan_units` ONLY in its Gather branch; Perm/OrderbyWalk
                        # stop at page_offset+limit and never scan the candidates. A ratio measured on
                        # those is comparing the feature to work the arm never charges for.
                        "paging": paging,
                        "ratio": acq[feat] / got,
                        # What the same row would have read graded against the printing SPAN -- the
                        # old comparison. `nan` where the plan publishes no span, which `percentile`
                        # tolerates and `MIN_ROWS` cells then drop.
                        "span_ratio": (acq[feat] / span) if span else float("nan"),
                    }
                )
    return rows


def verdict(sorted_vals: list[float]) -> str:
    """Flag a cell whose median feature/counter ratio sits outside the agreement band."""
    med = costbench.percentile(sorted_vals, 50)
    if AGREE_LO <= med <= AGREE_HI:
        return ""
    return "  OVER-COUNTS" if med > AGREE_HI else "  UNDER-COUNTS"


def table(rows: list[dict], key: Callable[[dict], object], label: str, *, limit: int = 30, value: str = "ratio") -> None:
    """Feature/counter percentiles for one grouping, worst-calibrated cells first."""
    costbench.percentile_table(
        rows,
        key,
        label,
        value=value,
        rank=costbench.BY_MISCALIBRATION,
        limit=limit,
        min_rows=MIN_ROWS,
        annotate=verdict,
    )


def main() -> None:
    """Sample, then show which features disagree with the counters that realize them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".featacc.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    rows = collect(engine, sampler, random.Random(args.seed), args.seconds)
    print(f"\n{len(rows):,} feature-rows, mode={args.mode}.  ratio = FEATURE / COUNTER, so >1 is OVER-counted.")
    table(rows, lambda r: r["feature"], "feature (pooled -- hides per-cell errors that cancel)")
    table(rows, lambda r: f"{r['feature']} [{r['acquire']}]", "feature [acquire]")
    # The slice that catches mode-dependent features. A count taken in printing space is exact in
    # printing mode and ~2x off in artwork mode; pooled across modes it reads as mild spread.
    table(rows, lambda r: f"{r['feature']} [{r['acquire']}] / {r['unique']}", "feature [acquire] / distinct-on")
    # One shared feature vector costs every plan, but the plans do different work: `scan_units` feeds
    # StreamedSelect, GatheredScan and compose's Gather branch alike. If they disagree here, no single
    # value of the feature is right for all of them and the fix is per-arm, not per-mode.
    table(rows, lambda r: f"{r['feature']} <{r['plan']}> / {r['unique']}", "feature <plan> / distinct-on")
    # `prefer` decides whether the card-mode kernels early-break, and `PlanFeatures` does not carry
    # it, so one feature value has to serve both regimes. If these two rows differ, the feature is not
    # merely miscalibrated -- it is blind to a variable that changes the work.
    scan = [r for r in rows if r["feature"] == "scan_units"]
    table(scan, lambda r: f"scan_units / {r['unique']} / prefer={r['prefer']}", "scan_units by distinct-on and PREFER")
    # Orderby was always sampled and never sliced, so its effect has never been visible. It selects
    # the plan set (StreamedSelect needs a sort permutation; PlanePopcountOrder needs its column) and
    # therefore which arm reads the shared vector at all.
    table(rows, lambda r: f"{r['feature']} / orderby={r['orderby']}", "feature by ORDERBY", limit=40)
    # Compose only pays `scan_units` when it pages by Gather, so judge the feature on those rows.
    compose = [r for r in rows if r["plan"] == "PrintingCompose"]
    table(compose, lambda r: f"{r['feature']} <compose {r['paging']}> / {r['unique']}", "compose only: feature by PAGING branch")
    # The old grading, same rows: `scan_units` against the printing SPAN rather than the printings
    # actually examined. Printed last as the control -- if these cells sit at 1.0 where the real
    # column does not, the span is what the constants were fit against.
    spanned = [r for r in scan if math.isfinite(r["span_ratio"])]
    table(
        spanned,
        lambda r: f"scan_units / {r['unique']}",
        "CONTROL: scan_units vs printing SPAN (the old grading)",
        value="span_ratio",
    )
    print(f"\n  Cells outside [{AGREE_LO}, {AGREE_HI}] are flagged. A feature error cannot be fixed by any")
    print("  rate: fit_cost_model.py will bury it in whichever coefficient correlates with it.")


if __name__ == "__main__":
    main()
