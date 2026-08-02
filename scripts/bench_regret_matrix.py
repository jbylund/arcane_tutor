"""Where routing loss actually concentrates: the regret distribution, sliced several ways.

The companion to `bench_cost_error_percentiles.py`. That one says where the ESTIMATE is wrong; this
says where being wrong COSTS something. They disagree often enough to matter — an estimate can be off
by 100x on a plan that never wins anyway, and correct to 5% on one where the margin decides every
query.

Regret is `routed_ns - best_measured_ns`: what the router gave up against the best plan that ran, so a
correct pick contributes exactly 0. It is measured against `routed_ns` rather than the picked plan's
own trial because those differ in the case that matters most — when the picked plan DECLINES at
runtime, dispatch re-chooses among materializing plans only, and a non-materializing fast path that
would have won is never reconsidered.

Regret is mostly zeros, so a median is useless here: read the SHARE column, which is what fraction of
all lost time a slice accounts for. That ranks the work. p90/p99/max show whether a slice loses a
little constantly or a lot rarely.

    .venv/bin/python scripts/bench_regret_matrix.py --seconds 180
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import random
import statistics
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402
from scripts.query_sampler import MODES, QuerySampler  # noqa: E402

# Below this a "miss" costs nothing and only inflates the rate.
NOISE_FLOOR_US = costbench.NOISE_FLOOR_US
MIN_ROWS = costbench.MIN_ROWS
# Regret is mostly zeros, so the low percentiles carry no information and the shared p0..p100 row
# would be six columns of 0.00. Only the tail says anything here.
PERCENTILES = (90, 99)


percentile = costbench.percentile


def collect(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per multi-plan query: what it lost, and everything to slice that by.

    Deliberately does NOT use `costbench.plan_self_ns`. That nets `ns_prepare` back out to make a
    plan comparable to the cost model's per-plan prediction; regret compares against `routed_ns`,
    a whole real execution with the prep included. Netting one side of that subtraction and not the
    other would invent time that was never saved.
    """
    rows: list[dict] = []
    for sample in costbench.iter_samples(engine, sampler, rng, costbench.Budget(seconds=seconds)):
        ran = [p for p in sample.plans if p["trials_ns"]]
        if len(ran) < 2:  # noqa: PLR2004 - one applicable plan means there is nothing to get wrong
            continue
        picked = next((p for p in sample.plans if p["picked"]), None)
        declined = picked is not None and not picked["trials_ns"]
        best = min(ran, key=lambda p: min(p["trials_ns"]))
        # routed_ns lives on explain_analyze's acquire; `explain` runs nothing and leaves it empty.
        routed = sample.res["acquire"]["routed_ns"]
        if not routed:
            continue
        routed_us = min(routed) / 1000.0
        rows.append(
            {
                "lost": max(routed_us - min(best["trials_ns"]) / 1000.0, 0.0),
                "acquire": sample.acquire["count_source"],
                "unique": sample.kw["unique"],
                "picked": f"{picked['plan']}{'(declined)' if declined else ''}" if picked else "?",
                "best": best["plan"],
            }
        )
    return rows


def table(rows: list[dict], key: Callable[[dict], object], label: str, *, limit: int = 12) -> None:
    """One slice of the regret distribution, ranked by share of all lost time."""
    groups: dict[object, list[float]] = collections.defaultdict(list)
    for r in rows:
        groups[key(r)].append(r["lost"])
    grand = sum(r["lost"] for r in rows) or 1.0
    head = "".join(f"{f'p{p}':>9}" for p in PERCENTILES)
    print(f"\n{label:<44}{'n':>7}{'miss%':>7}{'mean':>8}{head}{'max':>10}{'SHARE':>8}")
    ranked = sorted(groups.items(), key=lambda kv: -sum(kv[1]))
    for name, vals in ranked[:limit]:
        if len(vals) < MIN_ROWS:
            continue
        vals.sort()
        total = sum(vals)
        cells = "".join(f"{percentile(vals, p):>9.2f}" for p in PERCENTILES)
        miss = sum(1 for v in vals if v > NOISE_FLOOR_US) / len(vals)
        print(f"{name!s:<44}{len(vals):>7}{miss:>6.0%}{statistics.fmean(vals):>8.2f}{cells}{vals[-1]:>10.1f}{total / grand:>7.0%}")


def main() -> None:
    """Sample, then show where routing loss concentrates by acquire, mode, and transition."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".regret.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    rows = collect(engine, sampler, random.Random(args.seed), args.seconds)
    total = sum(r["lost"] for r in rows)
    print(
        f"\n{len(rows):,} multi-plan queries, mode={args.mode}.  total regret {total / 1000:.1f} ms, mean {total / max(len(rows), 1):.2f} µs"
    )
    table(rows, lambda r: r["acquire"], "acquire")
    table(rows, lambda r: r["unique"], "unique")
    table(rows, lambda r: f"{r['acquire']} / {r['unique']}", "acquire / unique")
    table(rows, lambda r: f"{r['picked']} -> {r['best']}", "picked -> best (only when they differ)")
    print("\n  SHARE is the fraction of ALL lost time, which is what ranks the work: frequency times")
    print("  severity. miss% counts queries losing more than 1µs. Regret is mostly zeros, so no median.")


if __name__ == "__main__":
    main()
