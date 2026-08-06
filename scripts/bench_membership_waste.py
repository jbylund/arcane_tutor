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
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# The two plans that walk printings under a candidate and evaluate the residual per printing --
# `push_card_matches` (GatheredScan) and `card_match_count` (StreamedSelect), the two call sites #856
# would replace with a bit test.
MATERIALIZING = ("StreamedSelect", "GatheredScan")

# The acquire branch whose dispatch-time narrowing produces an exact printing-space bitmap and drops it.
COMPOSE_ACQUIRE = "printing_compose"

# Per-printing cost of the membership check that would replace the residual, MEASURED by
# card_engine/src/bench_membership_bittest.rs at the production point (234 cards x 13.6 printings,
# 1.56 matches each). Two routes, plus a pessimistic control:
#   0.17 - two-pointer merge over the sorted candidate list (no bitmap, GatheredScan only)
#   0.88 - scatter into a printing bitmap then probe (also costs ~0.5 us/query to build)
#   2.00 - control: 2x worse than the bitmap route, to show the floor
BIT_TEST_NS = (0.17, 0.88, 2.0)

# Match-density bands, chosen from bench_membership_bittest.rs's measured cost curve rather than as
# round numbers: the bit test is ~0.5 ns where the branch is predictable (below ~2% or above ~80%) and
# peaks at 2.3 ns in the middle, so these are the bands that actually differ in cost.
DENSITY_PREDICTABLE_LOW = 2.0
DENSITY_MIDDLE_LOW = 20.0
DENSITY_MIDDLE_HIGH = 80.0


class Totals:
    """Running sums for one view (picked-only, or all materializing trials)."""

    def __init__(self) -> None:
        """Start every accumulator at zero."""
        self.loop_ns = 0.0
        self.printings = 0
        self.rows = 0
        self.routed_ns = 0.0
        self.matches = 0
        self.cards = 0
        self.rates: list[float] = []
        self.rows_detail: list[tuple[float, int, str]] = []
        self.tail: list[tuple[float, float, int, str]] = []

    def add(self, loop_ns: float, printings: int, routed_ns: float = 0.0, matches: int = 0, cards: int = 0) -> None:
        """Fold one measured plan trial in."""
        self.matches += matches
        self.cards += cards
        self.loop_ns += loop_ns
        self.printings += printings
        self.rows += 1
        self.routed_ns += routed_ns
        self.rates.append(loop_ns / printings)


def collect(engine: object, sampler: object, rng: random.Random, budget: object) -> tuple:
    """Sample queries and accumulate the two views, plus the population breakdown."""
    from scripts import costbench  # noqa: PLC0415 - deferred so this imports without the built extension

    picked, every = Totals(), Totals()
    # Every sampled query's routed time, so the population can be placed in the OVERALL latency
    # distribution. A speedup on queries that are already fast is not a user-visible win, and only the
    # comparison against all traffic can say which this is.
    all_routed_ns: list[float] = []
    pop_n = 0
    by_repr: collections.Counter[str] = collections.Counter()
    picked_plans: collections.Counter[str] = collections.Counter()

    for s in costbench.iter_samples(engine, sampler, rng, budget):
        all_routed_ns.append(float(min(s.res["acquire"]["routed_ns"])))
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
                picked.add(loop, exam, float(min(s.res["acquire"]["routed_ns"])), int(p["matches_pushed"]), int(p["cards_visited"]))
                picked.rows_detail.append((100.0 * int(p["matches_pushed"]) / exam, exam, s.q))
                picked.tail.append((float(min(s.res["acquire"]["routed_ns"])), loop, exam, s.q))
                picked_plans[p["plan"]] += 1
    return picked, every, pop_n, by_repr, picked_plans, all_routed_ns


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
    if t.matches:
        # Match density decides how predictable the bit-test branch is, and the bit test is 0.5 ns at
        # 1% density against 2.3 ns at 50% (bench_membership_bittest.rs). Without this the micro-
        # benchmark cannot be read against this population.
        print(f"   matches / examined   {100 * t.matches / t.printings:.1f}%  (bit-test branch density)")
    report_density(t)
    if t.cards:
        # Candidate share of the corpus sets how scattered the bitmap reads are -- the other axis
        # bench_membership_bittest.rs sweeps (as `stride`, = 1 / this share).
        per_q = t.cards / max(t.rows, 1)
        print(f"   cards visited/query  {per_q:,.0f} of 31,508 -> stride ~{31508 / max(per_q, 1):.0f}")
    for bt in BIT_TEST_NS:
        print(f"   at {bt:>4.1f} ns/bit-test -> {t.printings * bt / 1e6:>7.1f} ms  ({rate / bt:.1f}x less)")


def report_density(t: Totals) -> None:
    """Print the match-density DISTRIBUTION and which predicate families make up the population.

    A pooled mean is the wrong summary here: the bit test costs ~0.5 ns where the branch is predictable
    (below ~2% or above ~80% density) and 2.3 ns in the middle, so two queries at 1% and 99% average to
    the worst case while both are cheap.
    """
    if t.rows_detail:
        ds = sorted(d for d, _, _ in t.rows_detail)
        pct = [ds[int(q * (len(ds) - 1))] for q in (0.10, 0.25, 0.50, 0.75, 0.90)]
        print("   density p10/p25/p50/p75/p90  " + " / ".join(f"{v:.0f}%" for v in pct))
        buckets = {"<2% (cheap)": 0, "2-20%": 0, "20-80% (worst)": 0, ">80% (cheap)": 0}
        for d in ds:
            if d < DENSITY_PREDICTABLE_LOW:
                key = "<2% (cheap)"
            elif d < DENSITY_MIDDLE_LOW:
                key = "2-20%"
            elif d < DENSITY_MIDDLE_HIGH:
                key = "20-80% (worst)"
            else:
                key = ">80% (cheap)"
            buckets[key] += 1
        print("   in cost bands: " + ", ".join(f"{k}={v}" for k, v in buckets.items()))
        print("   heaviest queries (by printings examined):")
        for d, exam, q in sorted(t.rows_detail, key=lambda r: -r[1])[:4]:
            print(f"      {exam:>8,} printings  {d:>5.1f}% dense   {q[:52]}")
        # WHICH QUERIES this touches is the question a traffic-weight caveat has to answer, so group by
        # the predicate families present rather than listing individual query strings.
        fam: dict[str, list] = {}
        for d, exam, q in t.rows_detail:
            keys = tuple(sorted(set(re.findall(r"([a-z_]+)[:<>=]", q)))) or ("<bare>",)
            fam.setdefault(" + ".join(keys), []).append((exam, d))
        print("   predicate families, by share of printings examined:")
        total = sum(exam for exam, _ in ((e, d) for rows in fam.values() for e, d in rows))
        for name, rows in sorted(fam.items(), key=lambda kv: -sum(e for e, _ in kv[1]))[:10]:
            ex = sum(e for e, _ in rows)
            md = sorted(d for _, d in rows)[len(rows) // 2]
            print(f"      {100 * ex / total:>5.1f}%  n={len(rows):>4}  median {md:>5.1f}% dense   {name[:46]}")


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
    # Uniform is the default because it resolves the RATE best (it reaches rare shapes at all), but the
    # population share only means something under realistic weights -- `watermark` carries 0.5 there
    # against `set`'s 5, and uniform over-samples both.
    ap.add_argument("--mode", choices=("uniform", "realistic"), default="uniform")
    args = ap.parse_args()

    from client.query_sampler import QuerySampler  # noqa: PLC0415 - deferred with costbench, same reason
    from scripts import costbench  # noqa: PLC0415

    engine = costbench.load_engine(args.corpus, args.shm)
    picked, every, pop_n, by_repr, picked_plans, all_routed_ns = collect(
        engine,
        QuerySampler(mode=args.mode),
        random.Random(args.seed),
        costbench.Budget(sample=args.sample),
    )

    print(f"\n{'=' * 78}\nPopulation: unique=printing, acquire={COMPOSE_ACQUIRE}\n{'=' * 78}")
    print(f"queries in population: {pop_n:,}  (of {args.sample:,} sampled, {args.mode} mode)")
    print("narrowed_repr:", ", ".join(f"{k}={v:,}" for k, v in by_repr.most_common()))
    report_view("PICKED (what production pays)", picked)
    report_view("ALL materializing trials (rate only, NOT a saving)", every)
    report_worth(picked)
    if picked.rates:
        mid = sorted(picked.rates)[len(picked.rates) // 2]
        print(f"\n   per-query ns/printing, median {mid:.2f}  (pooled is volume-weighted)")
    if picked_plans:
        print("   picked plan:", ", ".join(f"{k}={v:,}" for k, v in picked_plans.most_common()))
    report_tail(picked, all_routed_ns)


def pctl(xs: list[float], q: float) -> float:
    """The q-quantile of an unsorted list, nearest-rank."""
    s = sorted(xs)
    return s[min(int(q * len(s)), len(s) - 1)]


def merge_ns_share() -> float:
    """The recommended route's measured per-printing cost."""
    return BIT_TEST_NS[0]


def report_tail(t: Totals, all_routed_ns: list[float]) -> None:
    """Place the population in the overall latency distribution, and show its slowest queries.

    The aggregate speedup says nothing about whether any user-visible slow query gets faster. If the
    population sits entirely inside the fast part of the distribution, the honest answer is that it does
    not, however large the ratio.
    """
    if not t.tail or not all_routed_ns:
        return
    print("\n-- is anything SLOW here? --")
    pop = [r for r, _, _, _ in t.tail]
    print(f"   {'':<12}{'p50':>10}{'p90':>10}{'p99':>10}{'max':>10}")
    for label, xs in (("all sampled", all_routed_ns), ("this population", pop)):
        print(f"   {label:<12}" + "".join(f"{pctl(xs, q) / 1000:>9.1f}u" for q in (0.5, 0.9, 0.99, 1.0)))
    # The aggregate that matters: this population's share of ALL routed time, and what the change saves
    # of the whole. A large ratio on a small share is a small change.
    total_all, total_pop = sum(all_routed_ns), sum(pop)
    saved = t.loop_ns - t.printings * merge_ns_share()
    print(f"   population share of all routed time: {100 * total_pop / total_all:.2f}%")
    print(f"   saving as a share of ALL routed time: {100 * saved / total_all:.2f}%")
    merge_ns = merge_ns_share()
    print(f"   slowest in population, and what {merge_ns} ns/printing would make them:")
    for routed, loop, exam, q in sorted(t.tail, key=lambda r: -r[0])[:8]:
        after = routed - loop + exam * merge_ns
        print(f"      {routed / 1000:>7.1f}us -> {after / 1000:>6.1f}us  ({routed / max(after, 1):.1f}x)  {q[:44]}")


if __name__ == "__main__":
    main()
