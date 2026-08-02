"""Does the cost model ORDER each pair of plans correctly? The quantity routing actually depends on.

`bench_cost_model_agreement.py` scores each plan's absolute cost. That is the wrong target for plan
selection, and the gap is not academic: three successive reworkings of the per-card residual term
improved absolute agreement (n-weighted |ln| 0.212 -> 0.166) and moved routing by a measured
-0.003 us, CI [-0.206, +0.214]. `cost.rs` explains why in its own module docs -- the verify tier is
added to BOTH materializing plans, so it largely cancels in their argmin.

An argmin depends only on DIFFERENCES between plans. A term shared by every plan can be arbitrarily
wrong without changing a single routing decision; a small error in a term that differs between two
plans can flip many. So this measures, per plan PAIR:

- how often the model orders the pair correctly (`sign(pred_a - pred_b) == sign(meas_a - meas_b)`)
- what it costs when wrong, in microseconds of avoidable time
- how well the model's predicted GAP tracks the measured gap

Read it to decide what to fix next. A pair that is ordered correctly 99% of the time needs no work on
its relative costs however wrong both absolute numbers are; a pair near 50% is where routing is
actually being lost.

    .venv/bin/python scripts/bench_pairwise_ordering.py --seconds 300 --mode realistic
"""

from __future__ import annotations

import argparse
import collections
import itertools
import pathlib
import random
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from scripts.bench_bitplanes import load_engine  # noqa: E402
from scripts.bench_cost_model_agreement import RANGE_ACQUIRES  # noqa: E402
from scripts.query_sampler import MODES, QuerySampler  # noqa: E402

NUM_WARMUPS = 2
NUM_TRIALS = 7
LIMITS = (10, 100, 175)
OFFSETS = (0, 0, 0, 100)
# A gap smaller than this is inside measurement noise, so calling the order "wrong" says nothing.
TIE_FLOOR_US = 1.0
MIN_PAIRS_TO_REPORT = 30


def collect(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per (query, plan) that ran, carrying a query id so pairs can be reconstructed."""
    rows: list[dict] = []
    deadline = time.monotonic() + seconds
    qid = 0
    while time.monotonic() < deadline:
        limit, offset = rng.choice(LIMITS), rng.choice(OFFSETS)
        kw = {
            "filters": None,
            "unique": sampler.unique(rng),
            "orderby": sampler.orderby(rng),
            "direction": rng.choice(("asc", "desc")),
            "limit": limit,
            "offset": offset,
        }
        try:
            kw["filters"] = parse_scryfall_query(sampler.query(rng))
            acq = engine.explain(**kw)["acquire"]
            res = engine.explain_analyze(prefer="default", num_warmups=NUM_WARMUPS, num_trials=NUM_TRIALS, **kw)
        except Exception:  # noqa: BLE001, S112 - a rejected query is a skipped sample
            continue
        qid += 1
        for p in res["plans"]:
            if not p["trials_ns"] or p["predicted_ns"] <= 0:
                continue
            # Same netting as the agreement harness: only a Prep::Range acquire makes the routed path
            # re-pay prepare_candidates, so elsewhere it is not the plan's cost to carry.
            measured = float(min(p["trials_ns"]))
            if p["ns_round_total"] and acq["count_source"] not in RANGE_ACQUIRES:
                measured = max(measured - p["ns_prepare"], 1.0)
            rows.append(
                {
                    "qid": qid,
                    "plan": p["plan"],
                    "acquire": acq["count_source"],
                    "measured_us": measured / 1000.0,
                    "predicted_us": p["predicted_ns"] / 1000.0,
                }
            )
    return rows


def report(rows: list[dict], by_acquire: bool) -> None:
    """Per plan pair: ordering accuracy, regret when wrong, and how well the gap is predicted."""
    per_query: dict[int, list[dict]] = collections.defaultdict(list)
    for r in rows:
        per_query[r["qid"]].append(r)

    stats: dict[tuple[str, ...], dict] = collections.defaultdict(
        lambda: {"n": 0, "right": 0, "ties": 0, "regret": [], "gap_ratio": []}
    )
    for plans in per_query.values():
        for a, b in itertools.combinations(sorted(plans, key=lambda p: p["plan"]), 2):
            key = (a["plan"], b["plan"]) + ((a["acquire"],) if by_acquire else ())
            s = stats[key]
            meas_gap = a["measured_us"] - b["measured_us"]
            pred_gap = a["predicted_us"] - b["predicted_us"]
            if abs(meas_gap) < TIE_FLOOR_US:
                s["ties"] += 1
                continue
            s["n"] += 1
            if (meas_gap > 0) == (pred_gap > 0):
                s["right"] += 1
            else:
                # Picking the model's winner costs the whole measured gap.
                s["regret"].append(abs(meas_gap))
            if abs(pred_gap) > 0:
                s["gap_ratio"].append(meas_gap / pred_gap)

    label = "pair / acquire" if by_acquire else "plan pair"
    print(f"\n{label:<52}{'n':>7}{'ordered right':>15}{'mean regret':>13}{'gap meas/pred':>15}")
    ranked = sorted(stats.items(), key=lambda kv: -sum(kv[1]["regret"]))
    for key, s in ranked:
        if s["n"] < MIN_PAIRS_TO_REPORT:
            continue
        name = f"{key[0]} vs {key[1]}" + (f"  [{key[2]}]" if by_acquire else "")
        regret = sum(s["regret"]) / s["n"]
        gap = statistics.median(s["gap_ratio"]) if s["gap_ratio"] else float("nan")
        print(f"{name:<52}{s['n']:>7}{s['right'] / s['n']:>14.0%}{regret:>12.2f}µs{gap:>15.2f}")
    print(f"  ties (|measured gap| < {TIE_FLOOR_US:g}µs) excluded; ranked by TOTAL regret contributed.")
    print("  'gap meas/pred' is the ratio of measured to predicted GAP — 1.00 means the model sizes")
    print("  the difference right, which is what argmin needs. Absolute per-plan accuracy is not.")


def main() -> None:
    """Sample, then report pairwise ordering overall and split by acquire branch."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--mode", choices=MODES, default="uniform", help="diagnostic: uniform reaches the rare tails where ordering errors hide"
    )
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".pairwise.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    rows = collect(engine, sampler, random.Random(args.seed), args.seconds)
    print(f"\n{len(rows):,} plan-rows over {len({r['qid'] for r in rows}):,} queries, mode={args.mode}")
    report(rows, by_acquire=False)
    report(rows, by_acquire=True)


if __name__ == "__main__":
    main()
