"""Where routing loss actually concentrates: the regret distribution, sliced several ways.

The companion to `bench_cost_error_percentiles.py`. That one says where the ESTIMATE is wrong; this
says where being wrong COSTS something. They disagree often enough to matter — an estimate can be off
by 100x on a plan that never wins anyway, and correct to 5% on one where the margin decides every
query.

Regret is `routed_dispatch_ns - best_dispatch_ns`: what the router gave up against the best plan that
ran, so a correct pick contributes exactly 0. It is measured against the routed path rather than the
picked plan's own trial because those differ in the case that matters most — when the picked plan
DECLINES at runtime, dispatch re-chooses among materializing plans only, and a non-materializing fast
path that would have won is never reconsidered.

**Both sides are DISPATCH, and that is load-bearing.** The engine's routed path has two bins: acquire
runs once before any plan is chosen and is identical whichever wins, and dispatch runs the winner.
`cost::plan_cost` prices only the second — "only what happens AFTER the acquire step" — so a plan
comparison that includes acquire charges the router for work no plan choice could have avoided. This
used to subtract from `routed_ns`, the whole path, and acquire is ~45% of a candidate-acquired query.

The size of that mistake, measured on the population where regret is provably zero (21,463 queries
whose picked plan WAS the best): mean `6.13 us` against `routed_ns`, `-0.01 us` against dispatch.
Those rows were **63% of all reported regret**, and `StreamedSelect -> StreamedSelect` — picked equals
best, so not misrouting by definition — was the single largest slice at a 14.15 us mean. On this basis
it is 1.16. The genuine misroutes are unmoved (`PrintingCompose -> GatheredScan` 65.94 -> 65.22).

Needs a `routed-phases` build. Without the feature the phase keys publish as zeros and those queries
are skipped with a warning rather than measured against a zero baseline.

Regret is mostly zeros, so a median is useless here: read the SHARE column, which is what fraction of
all lost time a slice accounts for. That ranks the work. p90/p99/max show whether a slice loses a
little constantly or a lot rarely.

SHARE sums SIGNED regret. A transition can have a negative mean — the routed path beating the best
forced plan, via the lazy re-choose or simply a forced run paying a cold cache the routed one does not
— and clamping each row at zero let such a slice accumulate share as though it were losing time.
`GatheredScan -> StreamedSelect` is the live example: mean `-18.91 us`, and it held 23% of share under
the clamp.

    .venv/bin/python scripts/bench_regret_matrix.py --seconds 180   # build --features routed-phases
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

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

# Below this a "miss" costs nothing and only inflates the rate.
NOISE_FLOOR_US = costbench.NOISE_FLOOR_US
MIN_ROWS = costbench.MIN_ROWS
# Regret is mostly zeros, so the low percentiles carry no information and the shared p0..p100 row
# would be six columns of 0.00. Only the tail says anything here.
PERCENTILES = (90, 99)


percentile = costbench.percentile


def collect(engine: object, sampler: QuerySampler, rng: random.Random, seconds: float) -> list[dict]:
    """One row per multi-plan query: what it lost, and everything to slice that by.

    Both sides of the subtraction are DISPATCH, which is what makes the difference attributable to
    plan choice at all. Acquire is a separate bin: it runs once, before any plan is chosen, is
    identical whichever plan wins, and `cost::plan_cost` prices none of it. Including it in the
    baseline charged the router for work no plan choice could have avoided.

    Measured, on the population where that is provably zero -- 21,463 queries whose picked plan WAS
    the best one, so there is no misrouting by construction:

        mean vs routed_ns          6.13 us
        mean vs routed_dispatch   -0.01 us

    Those rows were 63% of all reported regret. `StreamedSelect -> StreamedSelect` alone read a 14.15
    us mean and the top share; on this basis it is 1.16. The genuine misroutes barely move
    (`PrintingCompose -> GatheredScan` 65.94 -> 65.22), which is the signature of having removed
    acquire rather than signal.

    `plan_self_ns` IS used, and its netting is what makes the two sides comparable rather than a
    correction for anything: on a candidates acquire the routed path runs `prepare_candidates` inside
    `acquire_plan_features` and dispatch reuses the artifact, so netting turns a forced trial
    (prepare + executor) into the executor-only quantity dispatch measures. On a range or compose
    acquire nothing is prepared during acquire, dispatch pays it if a materializing plan wins, and
    both sides already include it -- which is exactly what `RANGE_ACQUIRES` excludes.

    Requires a `routed-phases` build; without the feature the phase keys are published as zeros and
    there is nothing to re-base on, so those runs are skipped rather than silently measured wrong.
    """
    rows: list[dict] = []
    skipped_unphased = 0
    skipped_unpriced = 0
    for sample in costbench.iter_samples(engine, sampler, rng, costbench.Budget(seconds=seconds)):
        ran = [p for p in sample.plans if p["trials_ns"]]
        if len(ran) < 2:  # noqa: PLR2004 - one applicable plan means there is nothing to get wrong
            continue
        picked = next((p for p in sample.plans if p["picked"]), None)
        declined = picked is not None and not picked["trials_ns"]
        # Best on the SAME dispatch-equivalent definition the baseline uses, and EVERY plan that ran
        # must be priceable on it. `plan_self_ns` returns None when netting overshoots
        # no phase timing, and taking the best of what is left silently substitutes a slower
        # plan for the true best -- which reads as the router beating it. Measured on plane queries,
        # where the plane build is a large share of a forced trial: PlanePopcountOrder is dropped 37%
        # of the time (median ns_prepare/trial 0.38), and those rows drove the plane slice to a mean
        # of -6.27us. A query with an unpriceable plan has no computable regret; skip it and say so.
        selves = {id(p): costbench.plan_self_ns(p, sample.acquire) for p in ran}
        if any(v is None for v in selves.values()):
            skipped_unpriced += 1
            continue
        best = min(ran, key=lambda p: selves[id(p)])
        dispatch = sample.res["acquire"].get("routed_dispatch_ns")
        if not dispatch or not any(dispatch):
            skipped_unphased += 1
            continue
        rows.append(
            {
                # SIGNED. A negative row is the routed path beating the best forced plan, which
                # happens (the lazy re-choose, or a forced run paying a cold cache the routed one
                # does not) and is information, not zero. Clamping it to 0 let a transition whose
                # MEAN is negative still accumulate share as if it were loss.
                "lost": min(dispatch) / 1000.0 - selves[id(best)] / 1000.0,
                "acquire": sample.acquire["count_source"],
                "unique": sample.kw["unique"],
                "picked": f"{picked['plan']}{'(declined)' if declined else ''}" if picked else "?",
                "best": best["plan"],
            }
        )
    if skipped_unpriced:
        print(f"skipped {skipped_unpriced:,} queries where a plan that ran published no phase timing and so cannot be priced")
    if skipped_unphased:
        print(f"skipped {skipped_unphased:,} queries with no routed phase split -- build with --features routed-phases")
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
