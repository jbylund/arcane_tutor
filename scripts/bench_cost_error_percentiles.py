"""The full estimate/real distribution per plan, at fixed percentiles.

Every other view here reports a median, which hides the shape. A plan can have a perfect median while
over-costing a fifth of queries by 10x, and the fix for that is nothing like the fix for a uniform
shift. This prints p1/p10/p20/p50/p70/p90/p99 so the shape is visible: a flat row is a uniform scale
error (recalibrate a rate), a steep row is a missing feature or wrong shape, and a row that is fine
in the middle with a bad tail is a specific query class rather than a general defect.

Ratio is **estimate / real**, so **>1 means OVER-costed** — the reciprocal of the measured/predicted
convention the agreement harness uses. Stated here because mixing them up inverts every conclusion.

Covers all six plans, not just the two carrying executor counters, since it needs only `predicted_ns`
and `trials_ns`.

    .venv/bin/python scripts/bench_cost_error_percentiles.py --seconds 180
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import random
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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
PERCENTILES = (1, 10, 20, 50, 70, 90, 99)
MIN_ROWS = 40


def percentile(sorted_vals: list[float], pct: int) -> float:
    """Nearest-rank percentile; no interpolation, so every printed number is a real observation."""
    if not sorted_vals:
        return float("nan")
    idx = min(round(pct / 100.0 * len(sorted_vals) + 0.5) - 1, len(sorted_vals) - 1)
    return sorted_vals[max(idx, 0)]


def collect(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per (query, plan) that ran, with the plan's own cost isolated the usual way."""
    rows: list[dict] = []
    deadline = time.monotonic() + seconds
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
        for p in res["plans"]:
            if not p["trials_ns"] or p["predicted_ns"] <= 0:
                continue
            # Only a Prep::Range acquire makes the routed path re-pay prepare_candidates, so elsewhere
            # it is not this plan's cost to carry. Same netting as every other harness here.
            real = float(min(p["trials_ns"]))
            if p["ns_round_total"] and acq["count_source"] not in RANGE_ACQUIRES:
                netted = real - p["ns_prepare"]
                if netted < real * 0.5:
                    continue  # netting overshot; the residual is noise, not a measurement
                real = netted
            rows.append(
                {
                    "plan": p["plan"],
                    "acquire": acq["count_source"],
                    "unique": kw["unique"],
                    "ratio": p["predicted_ns"] / real,
                }
            )
    return rows


def table(rows: list[dict], key: Callable[[dict], object], label: str, *, limit: int = 40) -> None:
    """Percentile table for one grouping of the rows."""
    groups: dict[object, list[float]] = collections.defaultdict(list)
    for r in rows:
        groups[key(r)].append(r["ratio"])
    head = "".join(f"{f'p{p}':>8}" for p in PERCENTILES)
    print(f"\n{label:<38}{'n':>7}{head}{'p90/p10':>9}")
    for name, vals in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]:
        if len(vals) < MIN_ROWS:
            continue
        vals.sort()
        cells = "".join(f"{percentile(vals, p):>8.2f}" for p in PERCENTILES)
        spread = percentile(vals, 90) / percentile(vals, 10) if percentile(vals, 10) > 0 else float("inf")
        print(f"{name!s:<38}{len(vals):>7}{cells}{spread:>9.1f}")


def main() -> None:
    """Sample, then print the estimate/real distribution per plan and per (plan, acquire)."""
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
    table(rows, lambda r: r["plan"], "plan")
    # distinct-on splits errors that cancel when pooled, and the cancellation hides real defects: the
    # compose arm reads 0.79 (too cheap) on artwork and 1.15 on printing, which pools to "roughly fine"
    # while the two drive OPPOSITE routing errors -- compose over-picked 38:1 in artwork, under-picked
    # 10:1 in printing. Per (plan, unique) is the smallest slice that shows it.
    table(rows, lambda r: f"{r['plan']} / {r['unique']}", "plan / distinct-on", limit=24)
    table(rows, lambda r: f"{r['plan']} [{r['acquire']}]", "plan [acquire]")
    table(rows, lambda r: f"{r['plan']} [{r['acquire']}] / {r['unique']}", "plan [acquire] / distinct-on", limit=20)
    print("\n  p90/p10 is the spread: ~1 means a uniform scale error (recalibrate), large means the")
    print("  error depends on something unmodelled. Nearest-rank percentiles, so each is a real query.")


if __name__ == "__main__":
    main()
