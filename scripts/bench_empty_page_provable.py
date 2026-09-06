"""Is a query's emptiness already PROVEN at the routing decision point?

The proposed fastpath is `offset >= total -> return an empty page without running any plan`. At
`total == 0` a proven upper bound and the exact count coincide (`0 <= true <= 0`), so the bound
channel alone satisfies the API's "total_cards is always the unpaginated count" contract. This
script asks whether that bound is actually in hand before dispatch, and how often.

Two questions, and the SECOND one is the safety-critical one:

1. COVERAGE -- of the queries that really return nothing, how many does the acquire already call
   zero, split by `count_source` (the route) and by `unique=` (the space)?

2. SOUNDNESS -- does a zero ever LIE? Any row where the acquire says zero and the executor returns
   rows is a wrong-answer bug in the proposed fastpath, not a slow path. Reported per route,
   because a route that lies is a route the fastpath must exclude.

What is graded is the `provably_empty` FLAG each acquire branch sets from what that branch itself
can prove -- a popcount, an empty materialized candidate list, an empty index range, or the
`guaranteed` channel -- which is what `run_query_routed` acts on when it answers `(0, vec![])`
without choosing or dispatching. Deliberately NOT `matches == 0`: the two coincide today, but the
count is a derived value and grading it would re-introduce exactly the downstream reconstruction the
flag exists to avoid. On a build predating the flag this falls back to the count so an old baseline
still runs.

    PYTHONPATH=<enginedir> .venv/bin/python bench_empty_page_provable.py --n-queries 8000
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import random
import statistics
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts.costbench import Budget, iter_samples, load_engine  # noqa: E402

#: Timing is irrelevant here -- only the acquire's claim and the executor's realized total are read.
#: One trial and no warmups buys far more distinct queries per unit of CPU.
TRIALS = 1
WARMUPS = 0
DEFAULT_N_QUERIES = 8000
#: Cap on the individual lying queries printed; the per-route counts above them are the summary.
MAX_LIES_SHOWN = 10
#: The three `unique=` spaces are zero-or-nonzero together, so a proof in ANY of them proves the
#: page empty in all three. These are the trace keys Round 60 exposes for the proven channel.
GUARANTEED_KEYS = ("card_guaranteed", "printing_guaranteed", "artwork_guaranteed")


def realized_total(sample: object) -> int | None:
    """The executor's own unpaginated total, taken from whichever plan the router actually ran."""
    for p in sample.plans:
        if p.get("picked") and p.get("result_total") is not None:
            return int(p["result_total"])
    for p in sample.plans:
        if p.get("result_total") is not None:
            return int(p["result_total"])
    return None


def trace_guaranteed(acq: dict) -> int | None:
    """The tightest PROVEN bound the `And` trace exposes, minimised over the three spaces.

    `None` means no channel was populated -- "no mechanism proved one", never "proved zero".
    """
    trace = acq.get("and_trace")
    if not isinstance(trace, dict):
        return None  # not a top-level `And` -- the trace, and so this channel, is absent entirely
    root = trace.get("tree")
    if not isinstance(root, dict):
        return None
    vals = [root[k] for k in GUARANTEED_KEYS if isinstance(root.get(k), int)]
    return min(vals) if vals else None


def collect(engine: object, sampler: QuerySampler, rng: random.Random, budget: Budget) -> dict:
    """One pass over the sample, bucketing every query by route, realized emptiness and claim."""
    tally = {
        "cover": collections.Counter(),  # (route, is_empty) -> queries seen
        "claims": collections.Counter(),  # (route, is_empty) -> acquire said zero
        "by_mode": collections.Counter(),  # (unique, is_empty) -> acquire said zero
        "lies": [],  # said zero, executor returned rows -- must stay empty
        "missed": [],  # really empty, acquire did not say so
        "trace_rows": 0,
        "trace_zero": 0,
        "n": 0,
    }
    for sample in iter_samples(engine, sampler, rng, budget, vary_prefer=True):
        acq = sample.acquire
        real = realized_total(sample)
        if real is None or acq.get("matches") is None:
            continue
        tally["n"] += 1
        route, empty = acq["count_source"], real == 0
        # The FLAG that ships, not `matches == 0`. Those coincide today, but grading the derived
        # value would be exactly the "reconstruct a structural fact downstream" mistake the flag
        # exists to avoid -- and it is the flag, not the count, that decides whether a query skips
        # dispatch entirely. Falls back on a build that predates the flag so an old baseline still runs.
        claimed_zero = bool(acq["provably_empty"]) if "provably_empty" in acq else int(acq["matches"]) == 0
        tally["cover"][(route, empty)] += 1
        if claimed_zero:
            tally["claims"][(route, empty)] += 1
            tally["by_mode"][(sample.kw["unique"], empty)] += 1
            if not empty:
                tally["lies"].append({"q": sample.q, "route": route, "unique": sample.kw["unique"], "real": real})
        if empty:
            guaranteed = trace_guaranteed(acq)
            if guaranteed is not None:
                tally["trace_rows"] += 1
                tally["trace_zero"] += guaranteed == 0
            if not claimed_zero:
                tally["missed"].append({"route": route, "claim": int(acq["matches"]), "q": sample.q})
    return tally


def report_coverage(tally: dict, empties: int) -> None:
    """Of the queries that return nothing, how often is the acquire's zero already in hand?"""
    cover, claims = tally["cover"], tally["claims"]
    print(f"{'=' * 86}\nCOVERAGE -- of the queries that return nothing, does the acquire already say zero?\n{'=' * 86}")
    print(f"  {'route':<24} {'empty queries':>14} {'acquire says 0':>15} {'coverage':>10}")
    for route in sorted({r for r, _ in cover}):
        tot, got = cover[(route, True)], claims[(route, True)]
        if tot:
            print(f"  {route:<24} {tot:>14,} {got:>15,} {100 * got / tot:>9.1f}%")
    got_e = sum(v for (_, e), v in claims.items() if e)
    print(f"  {'ALL':<24} {empties:>14,} {got_e:>15,} {100 * got_e / max(empties, 1):>9.1f}%")
    print("\n  by unique= space:")
    for mode in sorted({m for m, _ in tally["by_mode"]}):
        print(f"    {mode:<12} {tally['by_mode'][(mode, True)]:>8,} proven-zero claims")


def report_soundness(tally: dict) -> None:
    """Any row here is a wrong-answer bug in the proposed fastpath, not a slow path."""
    lies, n = tally["lies"], tally["n"]
    print(f"\n{'=' * 86}\nSOUNDNESS -- does a zero claim ever LIE? (acquire says 0, executor returns rows)\n{'=' * 86}")
    if not lies:
        print(f"  0 lies in {n:,} queries. Every zero claim held.")
        return
    print(f"  *** {len(lies)} LIES -- the fastpath would return an empty page for a query with results ***")
    for route, count in collections.Counter(x["route"] for x in lies).most_common():
        print(f"    {route:<24} {count:>6}")
    for x in lies[:MAX_LIES_SHOWN]:
        print(f"      {x['route']:<22} unique={x['unique']:<9} real={x['real']:<8} {x['q'][:60]}")


def report_misses(tally: dict) -> None:
    """What the acquire claimed on the empty queries it failed to call zero."""
    if not tally["missed"]:
        return
    print(f"\n{'=' * 86}\nTHE MISSES -- empty queries the acquire did NOT call zero: what did it claim?\n{'=' * 86}")
    per: dict[str, list[int]] = collections.defaultdict(list)
    for m in tally["missed"]:
        per[m["route"]].append(m["claim"])
    print(f"  {'route':<24} {'n':>7} {'p50 claim':>10} {'p90 claim':>10}")
    for route, vals in sorted(per.items(), key=lambda kv: -len(kv[1])):
        v = sorted(vals)
        print(f"  {route:<24} {len(v):>7,} {statistics.median(v):>10,.0f} {v[int(0.9 * (len(v) - 1))]:>10,}")


def main() -> None:
    """Split the population by realized emptiness and report coverage and lie rate per route."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n-queries", type=int, default=DEFAULT_N_QUERIES)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, required=True)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path)
    sampler = QuerySampler(args.corpus, args.mode)
    budget = Budget(sample=args.n_queries, warmups=WARMUPS, trials=TRIALS)

    tally = collect(engine, sampler, random.Random(args.seed), budget)
    empties = sum(v for (_, e), v in tally["cover"].items() if e)
    n = tally["n"]
    print(f"\n{n:,} queries with both an acquire claim and a realized total, mode={args.mode}")
    print(f"  {empties:,} ({100 * empties / max(n, 1):.1f}%) really return nothing\n")

    report_coverage(tally, empties)
    report_soundness(tally)
    report_misses(tally)
    print(f"\n  `and_trace` PROVEN channel (top-level And only): {tally['trace_zero']:,} of "
          f"{tally['trace_rows']:,} empty rows carry a guaranteed bound of 0")


if __name__ == "__main__":
    main()
