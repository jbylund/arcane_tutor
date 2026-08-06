"""Size the waste #856 targets: per-printing residual re-evaluation under a compose acquire.

`narrow_candidates_exact` computes exact printing-space membership and discards it, so the materializing
plans re-derive per printing what the narrowing already proved. What that costs is
`ns_loop / printings_examined` on printing-mode queries acquired through `printing_compose`.

Both inputs are realized counters `explain_analyze` has published since #833, so this is an
OBSERVATIONAL measurement rather than an A/B: no second build, and no drift to control for. `ns_loop`
comes from the fastest recorded round (matching every consumer's `min(trials_ns)`), so the rate is a
floor rather than an average.

Two views, answering different questions -- conflating them is how the original 8x figure was produced:

- **PICKED** -- only queries where the router actually chose a materializing plan. This is what
  production pays and the only view a latency claim may cite.
- **ALL TRIALS** -- every materializing trial that ran, picked or not. Better-resolved RATE, but it
  includes forced runs of plans the router rejected and double-counts queries where both ran, so its
  total is not a saving.

Not observable from outside Rust: `n.tight`. #856's gate is `n.tight && printing_space`, and neither half
is visible here -- `narrowed_repr` is `None` by construction for a compose acquire (`Prep::Range` and
`Prep::Plane` materialize no candidate list), because the narrowing happens at DISPATCH, after the
router's acquire. So the population below is an upper bound on what the change can touch; the
per-printing rate is the solid part.

Delete this harness when #856 ships -- it exists to size one defect, and
`docs/issues/local-benchmark-toolkit-audit.md` is a standing argument against keeping one-off benches.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# The two plans that walk printings under a candidate and evaluate the residual per printing --
# `push_card_matches` (GatheredScan) and `card_match_count` (StreamedSelect), the two call sites #856
# would replace with a bit test.
MATERIALIZING = ("StreamedSelect", "GatheredScan")

# The acquire branch whose dispatch-time narrowing produces an exact printing-space bitmap and drops it.
COMPOSE_ACQUIRE = "printing_compose"

# Cost of an O(1) bitmap membership test, for the counterfactual. Reported across a range rather than at
# one value because the saving is (measured_rate - bit_test_ns): the original 8x assumed 1 ns, and a
# reader should see how much the conclusion rests on that.
BIT_TEST_NS = (0.5, 1.0, 2.0)


class Totals:
    """Running sums for one view (picked-only, or all materializing trials)."""

    def __init__(self) -> None:
        """Start every accumulator at zero."""
        self.loop_ns = 0.0
        self.printings = 0
        self.rows = 0
        self.routed_ns = 0.0
        self.rates: list[float] = []

    def add(self, loop_ns: float, printings: int, routed_ns: float = 0.0) -> None:
        """Fold one measured plan trial in."""
        self.loop_ns += loop_ns
        self.printings += printings
        self.rows += 1
        self.routed_ns += routed_ns
        self.rates.append(loop_ns / printings)


def collect(engine: object, sampler: object, rng: random.Random, budget: object) -> tuple:
    """Sample queries and accumulate the two views, plus the population breakdown."""
    from scripts import costbench  # noqa: PLC0415 - deferred so this imports without the built extension

    picked, every = Totals(), Totals()
    pop_n = 0
    by_repr: collections.Counter[str] = collections.Counter()
    picked_plans: collections.Counter[str] = collections.Counter()

    for s in costbench.iter_samples(engine, sampler, rng, budget):
        if s.kw["unique"] != "printing" or s.acquire["count_source"] != COMPOSE_ACQUIRE:
            continue
        pop_n += 1
        by_repr[s.acquire.get("narrowed_repr", "?")] += 1
        for p in s.plans:
            if p["plan"] not in MATERIALIZING or not p["trials_ns"]:
                continue
            loop, exam = float(p["ns_loop"]), int(p["printings_examined"])
            if exam <= 0 or loop <= 0:
                continue
            every.add(loop, exam)
            if p["picked"]:
                # routed_ns is a per-round list; min matches how consumers reduce trials_ns, and pairs
                # with ns_loop, which is itself the fastest round's phase split.
                picked.add(loop, exam, float(min(s.res["acquire"]["routed_ns"])))
                picked_plans[p["plan"]] += 1
    return picked, every, pop_n, by_repr, picked_plans


def report_view(label: str, t: Totals) -> None:
    """Print one view's volume, rate, and the bit-test counterfactual."""
    print(f"\n-- {label} --")
    if not t.printings:
        print("   no rows")
        return
    rate = t.loop_ns / t.printings
    print(f"   rows                 {t.rows:,}")
    print(f"   match-loop time      {t.loop_ns / 1e6:,.1f} ms")
    print(f"   printings examined   {t.printings / 1e6:,.1f} M")
    print(f"   per printing         {rate:.2f} ns")
    for bt in BIT_TEST_NS:
        print(f"   at {bt:>4.1f} ns/bit-test -> {t.printings * bt / 1e6:>7.1f} ms  ({rate / bt:.1f}x less)")


def report_worth(t: Totals) -> None:
    """Print what the change is worth end to end on the population that actually pays it."""
    if not t.routed_ns:
        return
    print("\n-- what it is worth on the PICKED path (end to end) --")
    print(f"   routed (total) time  {t.routed_ns / 1e6:,.1f} ms over {t.rows:,} queries")
    print(f"   of which match loop  {t.loop_ns / 1e6:,.1f} ms  ({100 * t.loop_ns / t.routed_ns:.1f}%)")
    for bt in BIT_TEST_NS:
        saved = t.loop_ns - t.printings * bt
        speedup = t.routed_ns / max(t.routed_ns - saved, 1e-9)
        print(
            f"   at {bt:>4.1f} ns/bit-test -> saves {saved / 1e6:>6.1f} ms"
            f" = {100 * saved / t.routed_ns:>4.1f}% of routed, {speedup:.2f}x on the population",
        )


def main() -> None:
    """Load a store from the corpus, sample, and report both views."""
    ap = argparse.ArgumentParser(description="Size #856's per-printing residual re-evaluation waste.")
    ap.add_argument("--corpus", type=pathlib.Path, required=True)
    ap.add_argument("--shm", type=pathlib.Path, required=True)
    ap.add_argument("--sample", type=int, default=30000)
    ap.add_argument("--seed", type=int, default=20260806)
    args = ap.parse_args()

    from client.query_sampler import QuerySampler  # noqa: PLC0415 - deferred with costbench, same reason
    from scripts import costbench  # noqa: PLC0415

    engine = costbench.load_engine(args.corpus, args.shm)
    picked, every, pop_n, by_repr, picked_plans = collect(
        engine,
        QuerySampler(mode="uniform"),
        random.Random(args.seed),
        costbench.Budget(sample=args.sample),
    )

    print(f"\n{'=' * 78}\nPopulation: unique=printing, acquire={COMPOSE_ACQUIRE}\n{'=' * 78}")
    print(f"queries in population: {pop_n:,}  (of {args.sample:,} sampled, uniform mode)")
    print("narrowed_repr:", ", ".join(f"{k}={v:,}" for k, v in by_repr.most_common()))
    report_view("PICKED (what production pays)", picked)
    report_view("ALL materializing trials (rate only, NOT a saving)", every)
    report_worth(picked)
    if picked.rates:
        mid = sorted(picked.rates)[len(picked.rates) // 2]
        print(f"\n   per-query ns/printing, median {mid:.2f}  (pooled is volume-weighted)")
    if picked_plans:
        print("   picked plan:", ", ".join(f"{k}={v:,}" for k, v in picked_plans.most_common()))


if __name__ == "__main__":
    main()
