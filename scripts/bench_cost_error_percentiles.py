"""The full estimate/real distribution per plan, at fixed percentiles.

Every other view here reports a median, which hides the shape. A plan can have a perfect median while
over-costing a fifth of queries by 10x, and the fix for that is nothing like the fix for a uniform
shift. This prints the whole percentile row so the shape is visible: a flat row is a uniform scale
error (recalibrate a rate), a steep row is a missing feature or wrong shape, and a row that is fine
in the middle with a bad tail is a specific query class rather than a general defect.

p0 and p100 are the real min and max, and they are not decoration. Estimates of zero cards and of
every card are both common, and they are exactly where a cost arm's shape breaks down — a p1/p99 view
clips the two cases most likely to be wrong.

Ratio is **estimate / real**, so **>1 means OVER-costed** — the reciprocal of the measured/predicted
convention the agreement harness uses. Stated here because mixing them up inverts every conclusion.

Covers all six plans, not just the two carrying executor counters, since it needs only `predicted_ns`
and `trials_ns`.

    .venv/bin/python scripts/bench_cost_error_percentiles.py --seconds 180
"""

from __future__ import annotations

import argparse
import functools
import pathlib
import random
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

# This harness pools finer than most (four nested slices), so it holds out for a thicker cell than
# the shared default before it will print a percentile row.
MIN_ROWS = 40


def collect(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per (query, plan) that ran, with the plan's own cost isolated the shared way."""
    rows: list[dict] = []
    for sample in costbench.iter_samples(engine, sampler, rng, costbench.Budget(seconds=seconds)):
        for p in sample.plans:
            real = costbench.plan_self_ns(p, sample.acquire)
            predicted = costbench.predicted_ns(p)
            if real is None or predicted is None:
                continue
            rows.append(
                {
                    "plan": p["plan"],
                    "acquire": sample.acquire["count_source"],
                    "unique": sample.kw["unique"],
                    "orderby": sample.kw["orderby"],
                    "ratio": predicted / real,
                }
            )
    return rows


def main() -> None:
    """Sample, then print the estimate/real distribution sliced four ways."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".pctile.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    rows = collect(engine, sampler, random.Random(args.seed), args.seconds)
    print(f"\n{len(rows):,} plan-rows, mode={args.mode}.  ratio = ESTIMATE / REAL, so >1 is OVER-costed.")

    table = functools.partial(costbench.percentile_table, rows, min_rows=MIN_ROWS)
    table(lambda r: r["plan"], "plan")
    # distinct-on splits errors that cancel when pooled, and the cancellation hides real defects: the
    # compose arm reads 0.79 (too cheap) on artwork and 1.15 on printing, which pools to "roughly fine"
    # while the two drive OPPOSITE routing errors -- compose over-picked 38:1 in artwork, under-picked
    # 10:1 in printing. Per (plan, unique) is the smallest slice that shows it.
    table(lambda r: f"{r['plan']} / {r['unique']}", "plan / distinct-on", limit=24)
    # Sort column decides which paging strategy the compose arm reaches for (Perm vs OrderbyWalk vs
    # Gather), and those have different shapes, so an arm can calibrate on one order and not another.
    # The sampler has always varied orderby; this is the slice that reads it back.
    table(lambda r: f"{r['plan']} / {r['orderby']}", "plan / orderby", limit=24)
    table(lambda r: f"{r['plan']} [{r['acquire']}]", "plan [acquire]")
    table(lambda r: f"{r['plan']} [{r['acquire']}] / {r['unique']}", "plan [acquire] / distinct-on", limit=20)
    print("\n  p90/p10 is the spread: ~1 means a uniform scale error (recalibrate), large means the")
    print("  error depends on something unmodelled. Nearest-rank percentiles, so each is a real query.")
    print("  p0/p100 are the real min and max -- the zero-card and all-cards estimates a p1/p99 view clips.")


if __name__ == "__main__":
    main()
