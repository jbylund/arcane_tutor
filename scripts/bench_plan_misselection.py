"""How often does the router pick a plan slower than the best one available, and by how much?

`explain` ranks every applicable plan by `cost::plan_cost` and `run_query_routed` takes the
argmin. `explain_analyze` then runs each plan for real. Comparing `argmin(predicted)` against
`argmin(measured)` prices the cost model's ranking errors directly — not its absolute accuracy,
which is a separate and much more forgiving question.

    .venv/bin/python scripts/bench_plan_misselection.py --source wild-operators --sample 200

Only multi-plan queries count: with one applicable plan there is nothing to mis-select. Regret
is `measured(picked) - measured(best)`, so a correct pick contributes 0 and the mean is over
all multi-plan queries, not only the misses.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import re
import statistics
import sys
from typing import TYPE_CHECKING

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_runner import random_query  # noqa: E402
from scripts.bench_bitplanes import load_engine  # noqa: E402

if TYPE_CHECKING:
    import card_engine

# Same operator test build_wild_corpus.py uses to split its census: the corpus is 75% bare
# name lookups (bot deep links), which resolve to one card and are never multi-plan.
OP_RE = re.compile(r"[a-z]+[:<>=]", re.I)
UNIQUE_FROM_SCRYFALL = {
    "card": "card",
    "cards": "card",
    "art": "artwork",
    "artwork": "artwork",
    "prints": "printing",
    "printing": "printing",
}
ORDERBY_VALUES = frozenset(
    {"edhrec", "cubecobra", "cmc", "power", "toughness", "rarity", "name", "released", "set", "color", "usd", "artist", "review"}
)
DEFAULT_ORDERBY = "edhrec"
RANDOM_UNIQUE_WEIGHTS = {"card": 75, "printing": 20, "artwork": 5}
# Enough rounds that a bimodal plan shows both modes (00648's measurement-traps section) while
# keeping a 200-query sweep to a couple of minutes.
NUM_WARMUPS = 3
NUM_TRIALS = 15
# Regret below this is inside run-to-run noise at these sizes; counted but reported separately
# so a "miss" that costs nothing does not inflate the headline rate.
NOISE_FLOOR_US = 1.0
# Fewer plans than this and there is no choice to get wrong.
MIN_PLANS_TO_CHOOSE = 2
# statistics.quantiles needs more than this many samples to say anything.
MIN_FOR_QUARTILES = 4
# Only misses at least this costly are classified live-or-rescued; below it the routed,
# picked and best timings all sit inside each other's noise and the comparison says nothing.
LIVE_DEFECT_FLOOR_US = 5.0


def load_queries(source: str, sample: int, seed: int) -> list[tuple[str, str, str]]:
    """(query, unique, orderby) triples from the requested source, sampled deterministically."""
    rng = random.Random(seed)
    if source == "random":
        uniques = list(RANDOM_UNIQUE_WEIGHTS)
        weights = [RANDOM_UNIQUE_WEIGHTS[u] for u in uniques]
        return [(random_query(), rng.choices(uniques, weights=weights)[0], DEFAULT_ORDERBY) for _ in range(sample)]

    rows = []
    for line in (REPO_ROOT / "benchmarks/wild-queries/wild-corpus.jsonl").open():
        row = json.loads(line)
        unique = UNIQUE_FROM_SCRYFALL.get(row.get("unique", "card"))
        if unique is None or not OP_RE.search(row["q"]):
            continue
        order = row.get("order", DEFAULT_ORDERBY)
        rows.append((row["q"], unique, order if order in ORDERBY_VALUES else DEFAULT_ORDERBY))
    rng.shuffle(rows)
    return rows[:sample]


def calibration(engine: card_engine.QueryEngine, queries: list[tuple[str, str, str]], trials: int = NUM_TRIALS) -> None:
    """Per-plan measured/predicted ratio across the corpus — which cost arms are wrong, and how.

    Mis-COSTING is the leading indicator; mis-selection is only where it happens to bite. A plan
    costed 30x wrong is picked correctly right up until it competes closely with something, so this
    scan finds problems before they cost a query anything.

    Ratio > 1 means the plan is cheaper in the model than in reality (under-costed, so over-picked);
    < 1 means over-costed and under-picked.
    """
    # Two ratios, because `measured` includes the acquire step and `predicted` does not — that
    # unpriced term (docs/issues/local-engine-candidate-materialize.md) would otherwise be read as
    # the plan arm being wrong. `net` subtracts the measured acquire, isolating the arm itself.
    ratios: collections.defaultdict[str, list[float]] = collections.defaultdict(list)
    nets: collections.defaultdict[str, list[float]] = collections.defaultdict(list)
    by_source: collections.defaultdict[tuple[str, str], list[float]] = collections.defaultdict(list)
    worst: collections.defaultdict[str, tuple[float, str]] = collections.defaultdict(lambda: (1.0, ""))
    for q, unique, orderby in queries:
        try:
            res = engine.explain_analyze(
                filters=parse_scryfall_query(q),
                unique=unique,
                orderby=orderby,
                direction="asc",
                limit=100,
                offset=0,
                num_warmups=NUM_WARMUPS,
                num_trials=trials,
            )
        except Exception:  # noqa: BLE001, S112 - a query bind rejects is a sample skip, not an error
            continue
        for p in res["plans"]:
            if not p["trials_ns"] or p["predicted_ns"] <= 0:
                continue
            # A plan can measure 0 ns when it is faster than the clock's resolution; clamp so the
            # log-scale ordering below stays defined without dropping the sample.
            meas = max(min(p["trials_ns"]), 1)
            r = meas / p["predicted_ns"]
            ratios[p["plan"]].append(r)
            # A plan that materializes pays acquire inside its trial; net it out. Clamped at a
            # nanosecond so a plan whose whole cost was acquire cannot divide by zero.
            net = max(meas - min(res["acquire"]["acquire_ns"]), 1.0) if p["materialize_ns"] > 0 else float(meas)
            nets[p["plan"]].append(net / p["predicted_ns"])
            by_source[p["plan"], res["acquire"]["count_source"]].append(net / p["predicted_ns"])
            # Track the furthest from 1 in log terms, either direction.
            if abs(math.log(r)) > abs(math.log(worst[p["plan"]][0])):
                worst[p["plan"]] = (r, f"{q} [{unique}]")

    print(f"\n{'plan':<20}{'n':>5}{'median':>9}{'net':>8}{'p25':>8}{'p75':>8}{'worst':>9}  worst query")
    for plan, rs in sorted(ratios.items(), key=lambda kv: abs(math.log(statistics.median(kv[1]))), reverse=True):
        qs = statistics.quantiles(rs, n=4) if len(rs) >= MIN_FOR_QUARTILES else [float("nan")] * 3
        w, wq = worst[plan]
        print(
            f"{plan:<20}{len(rs):>5}{statistics.median(rs):>9.2f}{statistics.median(nets[plan]):>8.2f}"
            f"{qs[0]:>8.2f}{qs[2]:>8.2f}{w:>9.2f}  {wq[:38]}"
        )
    print("\nratio = measured / predicted; net subtracts the measured acquire from materializing plans.")
    print(">1 under-costed (over-picked), <1 over-costed (under-picked).")

    # Which acquire branch built the features matters: a plan costed off another plan's acquire can
    # be charged for work it would never do, because the feature fields it needs were left at their
    # defaults. A plan whose ratio is fine on its own acquire and wrong on others is that bug.
    print(f"\n{'plan':<20}{'acquire branch':<22}{'n':>5}{'net median':>12}{'p10':>8}{'p90':>8}")
    for (plan, src), rs in sorted(by_source.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        if len(rs) < MIN_FOR_QUARTILES:
            print(f"{plan:<20}{src:<22}{len(rs):>5}{statistics.median(rs):>12.2f}{'':>8}{'':>8}")
            continue
        ds = statistics.quantiles(rs, n=10)
        print(f"{plan:<20}{src:<22}{len(rs):>5}{statistics.median(rs):>12.2f}{ds[0]:>8.2f}{ds[8]:>8.2f}")


def main() -> None:
    """Sweep the sample, print every mis-selection, and summarize the rate and regret."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calibration", action="store_true", help="per-plan cost-model accuracy instead of the miss table")
    parser.add_argument("--source", choices=("wild-operators", "random"), default="wild-operators")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--trials", type=int, default=NUM_TRIALS, help="timed rounds per plan; lower for large sweeps")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".misselect.store"))
    queries = load_queries(args.source, args.sample, args.seed)
    if args.calibration:
        calibration(engine, queries, args.trials)
        return

    pairs: collections.Counter[str] = collections.Counter()
    regret: list[float] = []
    misses: list[tuple[float, str, str, str, float, str]] = []
    live_count: list[str] = []
    multi = skipped = declined = 0

    for q, unique, orderby in queries:
        try:
            res = engine.explain_analyze(
                filters=parse_scryfall_query(q),
                unique=unique,
                orderby=orderby,
                direction="asc",
                limit=100,
                offset=0,
                num_warmups=NUM_WARMUPS,
                num_trials=NUM_TRIALS,
            )
        except Exception:  # noqa: BLE001 - a query bind rejects is a sample skip, not an error
            skipped += 1
            continue
        ran = [p for p in res["plans"] if p["trials_ns"]]
        if len(ran) < MIN_PLANS_TO_CHOOSE:
            continue
        # Regret is measured against `routed_ns` — what the engine actually pays — not against the
        # picked plan's own trial. Those differ in the case that matters most: when the picked plan
        # declines at runtime, dispatch re-chooses among MATERIALIZING plans only, so a
        # non-materializing fast path that would have won is never reconsidered. Scoring the picked
        # plan's trial skips those queries entirely, which is how the worst misses stayed hidden.
        picked = [p for p in res["plans"] if p["picked"]]
        pick = picked[0] if picked else None
        pick_declined = pick is not None and not pick["trials_ns"]
        declined += pick_declined
        multi += 1
        best = min(ran, key=lambda p: min(p["trials_ns"]))
        routed = min(res["acquire"]["routed_ns"]) / 1000
        lost = routed - min(best["trials_ns"]) / 1000
        regret.append(lost)
        same_plan = pick is not None and pick["plan"] == best["plan"] and not pick_declined
        if lost >= NOISE_FLOOR_US and not same_plan:
            pick_name = f"{pick['plan']}{'(declined)' if pick_declined else ''}" if pick else "?"
            pairs[f"{pick_name} -> {best['plan']}"] += 1
            if lost >= LIVE_DEFECT_FLOOR_US:
                live_count.append(q)
            misses.append((lost, res["acquire"]["count_source"], pick_name, best["plan"], routed, q))

    print_misses(misses)
    counts = {"sampled": len(queries), "multi": multi, "skipped": skipped, "declined": declined}
    print_summary(args.source, counts, misses, regret, pairs, live=len(live_count))


def print_misses(misses: list[tuple[float, str, str, str, float, str]]) -> None:
    """The per-miss table, worst regret first. `live?` is blank below the classification floor."""
    misses.sort(reverse=True)
    print(f"\n{'lost µs':>9}{'routed':>9}{'src':>19}{'picked':>26}{'best':>20}  query")
    for lost, src, pick_name, best_name, routed, q in misses:
        print(f"{lost:>9.1f}{routed:>9.1f}{src:>19}{pick_name:>26}{best_name:>20}  {q[:34]}")


def print_summary(  # noqa: PLR0913 - a print function's arguments ARE its output; bundling them
    # into a struct would add a type whose only purpose is to satisfy this rule.
    source: str,
    counts: dict[str, int],
    misses: list[tuple[float, str, str, str, float, str]],
    regret: list[float],
    pairs: collections.Counter[str],
    *,
    live: int,
) -> None:
    """Rates and totals. Regret is routed-minus-best, so it is what the engine actually pays."""
    real = sum(1 for lost, *_ in misses if lost >= NOISE_FLOOR_US)
    print(
        f"\n{source}: {counts['multi']:,} multi-plan queries of {counts['sampled']:,} sampled "
        f"({counts['skipped']:,} skipped, {counts['declined']:,} picked-plan declined)"
    )
    print(
        f"mis-selected: {len(misses):,} ({len(misses) / max(counts['multi'], 1):.0%}), of which {real:,} cost >{NOISE_FLOOR_US:g} µs"
    )
    if regret:
        print(f"regret over all multi-plan queries: mean {statistics.mean(regret):.2f} µs, max {max(regret):.1f} µs")
    print(
        f"regret >{LIVE_DEFECT_FLOOR_US:g} µs on {live:,} queries; {counts['declined']:,} had their picked plan decline at runtime"
    )
    for pair, n in pairs.most_common():
        print(f"  {pair}: {n}")


if __name__ == "__main__":
    main()
