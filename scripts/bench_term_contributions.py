"""Which cost-model TERMS carry the predicted time? Spread only matters where the share is large.

The rest of this toolkit grades FEATURES: `bench_feature_accuracy.py` says which feature disagrees
with the counter that realizes it, and `fit_cost_model.py` says what the rates should be. Neither
says whether a given term matters. That gap produced two rounds of work on a term worth 0.3% of its
plan's predicted cost, so this closes it.

By the end of Round 72 four features sat at a median near 1.00 with a 15-35x spread
(`stream_perm_steps`, `stream_scan_units`, `printings_walked`, `compose_scan_printings`) and no
existing feature predicting the residual. Whether any of them is worth a build depends entirely on
share: a 35x spread on a term worth 2% of predicted ns is noise; a 27x spread on a term worth 43% is
the model.

Exact rather than approximate, because `fit_cost_model.design_row` returns `{term: value}` and
`CURRENT[plan][term]` holds the shipped coefficient -- so a term's contribution in ns is the product,
and they sum to the engine's own `predicted_ns`. That identity is checked, not assumed: the mirror
agreement `fit_cost_model` enforces is what makes this a decomposition of the shipped model rather
than of a Python lookalike.

Three views, because they answer different questions:

  AGGREGATE   sum(contribution) / sum(total) -- where the nanoseconds are, weighted by how expensive
              each query is. The one that ranks work.
  MEDIAN      the per-row median share -- where they are on a TYPICAL query, which a handful of huge
              queries cannot dominate. Read both: a term at 36% aggregate and 0% median is a tail.
  RISK        aggregate x the feature's measured p90/p10, for the terms that have a graded feature.
              Crude, but it is the trade-off being made.

    PYTHONPATH=<wheel> .venv/bin/python scripts/bench_term_contributions.py --n-queries 4000
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

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import MODES, QuerySampler, Shape  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402
from scripts.fit_cost_model import CURRENT, design_row  # noqa: E402

#: Sort columns to draw from. Includes the two with no card permutation (`usd`, `rarity`), since they
#: select different plans entirely -- see `orderby_walk_available`.
ORDERBYS = ("name", "cmc", "rarity", "usd", "edhrec", "power", "toughness", "cubecobra")
#: Varied because `compose_scan_printings` depends on `prefer` at ACQUIRE time (Round 66), so pinning
#: it would exercise only one of the gather's arms. See `costbench.iter_samples`' own note.
PREFERS = ("default", "newest", "oldest", "usd_high")
OFFSETS = (0, 0, 60, 300)
LIMIT = 60
#: Queries drawn per predicate count (1, 2, 3), so 1x/2x/3x this many.
DEFAULT_QUERIES_PER_LEAF_COUNT = 700
#: Cap on sampler retries when it keeps returning duplicates.
MAX_SAMPLE_TRIES = 18_000
#: Below this a plan's rows are too few to summarise.
MIN_ROWS = 30
#: Measured p90/p10 per (plan, term), from the Round 69-72 grading runs. Keyed by BOTH because
#: `SCAN_PER_ROW` is a different feature in each plan -- `stream_scan_units` for StreamedSelect,
#: `scan_units` for GatheredScan -- and keying by term alone silently gives one the other's spread.
MEASURED_SPREAD = {
    ("StreamedSelect", "PERM_STEP"): 15.5,
    ("StreamedSelect", "SCAN_PER_ROW"): 27.1,
    ("GatheredScan", "SCAN_PER_ROW"): 11.1,
    ("PrintingCompose", "WALK_STEP"): 21.8,
    ("PrintingCompose", "GATHER_BITTEST_PER_PRINTING"): 34.2,
}


def draw_queries(sampler: QuerySampler, per_leaf: int) -> list[str]:
    """Distinct queries at 1, 2 and 3 predicates, `per_leaf` x the leaf count of each.

    Seeded per leaf count rather than off the caller's rng, so `--seed` varies the page/order/prefer
    draw while the query population stays fixed. Two runs are then comparable term by term.
    """
    queries: list[str] = []
    seen: set[str] = set()
    for n_leaves in (1, 2, 3):
        qrng = random.Random(f"contrib:{n_leaves}")
        tries = 0
        while len(seen) < per_leaf * n_leaves and tries < MAX_SAMPLE_TRIES:
            tries += 1
            q = sampler.query(qrng, shape=Shape(predicates=n_leaves))
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
    return queries


def collect(engine: object, sampler: QuerySampler, rng: random.Random, per_leaf: int) -> tuple[dict, dict]:
    """Per-term contributions in ns, for every costed plan-row and for the picked rows alone."""
    queries = draw_queries(sampler, per_leaf)
    allrows: dict[str, list[tuple[dict[str, float], float]]] = collections.defaultdict(list)
    picked: dict[str, list[tuple[dict[str, float], float]]] = collections.defaultdict(list)
    for q in queries:
        for unique in ("printing", "card", "artwork"):
            offset = rng.choice(OFFSETS)
            kwargs = {
                "filters": parse_scryfall_query(q),
                "unique": unique,
                "orderby": rng.choice(ORDERBYS),
                "direction": "asc",
                "limit": LIMIT,
                "offset": offset,
                # To `explain`, not just to an execution: the acquire reads it.
                "prefer": rng.choice(PREFERS),
            }
            try:
                explained = engine.explain(**kwargs)
            except Exception:  # noqa: BLE001, S112 - a rejected query is a skipped sample
                continue
            acquire = explained["acquire"] or {}
            if not acquire:
                continue
            for plan_row in explained["plans"]:
                predicted, plan = plan_row.get("predicted_ns"), plan_row["plan"]
                if predicted is None or predicted <= 0:
                    continue
                built = design_row(plan, acquire, LIMIT, offset)
                if built is None:
                    continue  # an arm with no fittable design (compose's Decline costs infinity)
                terms, excess = built
                contrib = {name: CURRENT[plan][name] * value for name, value in terms.items()}
                if excess > 0:
                    # The residual charge above the floor, which no coefficient scales. Named so it
                    # shows up as a term rather than silently breaking the sum-to-predicted identity.
                    contrib["RESIDUAL_EXCESS"] = excess
                total = sum(contrib.values())
                if total <= 0:
                    continue
                allrows[plan].append((contrib, total))
                if plan_row.get("picked"):
                    picked[plan].append((contrib, total))
    return allrows, picked


def report(data: dict[str, list[tuple[dict[str, float], float]]], label: str) -> None:
    """Print each plan's terms ordered by aggregate share of predicted time, largest first."""
    print(f"\n{'=' * 96}\n{label}\n{'=' * 96}")
    for plan, rows in sorted(data.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < MIN_ROWS:
            continue
        grand = sum(total for _, total in rows)
        print(f"\n{plan}  (n={len(rows):,} rows, {grand / 1e6:.1f} ms of predicted time in total)")
        print(f"  {'term':<34} {'aggregate':>10} {'median':>9} {'p90 share':>10} {'spread':>8} {'risk':>8}")
        stats = []
        for name in sorted({t for contrib, _ in rows for t in contrib}):
            agg = sum(contrib.get(name, 0.0) for contrib, _ in rows) / grand
            shares = sorted(contrib.get(name, 0.0) / total for contrib, total in rows)
            p90 = shares[min(len(shares) - 1, int(0.90 * len(shares)))]
            stats.append((agg, name, statistics.median(shares), p90))
        for agg, name, med, p90 in sorted(stats, reverse=True):
            spread = MEASURED_SPREAD.get((plan, name))
            spread_s = f"{spread:>8.1f}" if spread else f"{'--':>8}"
            risk_s = f"{agg * spread:>8.2f}" if spread else f"{'--':>8}"
            print(f"  {name:<34} {agg:>9.1%} {med:>8.1%} {p90:>9.1%} {spread_s} {risk_s}")


def main() -> None:
    """Sample, decompose each plan's predicted cost into its terms, and rank them by share."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n-queries", type=int, default=DEFAULT_QUERIES_PER_LEAF_COUNT, help="queries per predicate count")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".terms.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    allrows, picked = collect(engine, sampler, random.Random(args.seed), args.n_queries)
    report(allrows, "ALL costed plan-rows -- what the argmin compares")
    report(picked, "PICKED rows only -- what actually becomes latency")
    print("\nrisk = aggregate share x the feature's measured p90/p10. '--' means no graded feature")
    print("backs the term, which is itself worth noting where the share is large.")


if __name__ == "__main__":
    main()
