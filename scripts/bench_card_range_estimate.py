"""Why is every plan costed off the `CardRangePopcount` acquire under-costed 2.0-2.6x?

That branch feeds `matches`/`eval_domain`/`scan_units` from `card_est = k.min(n_cards)`, where `k` is
the number of in-range *printings* standing in for a card count — a proxy its own comment flags. Two
things get measured here, because the proxy alone cannot be the cause:

1. **Estimate accuracy** — `card_est` against the query's real total. It over-estimates, which
   *over*-costs, the opposite sign to the observed error. So correcting it alone makes the
   under-costing worse; establishing that ordering is the point of measuring it.
2. **The materialization split** — `acquire.prep_ns` times `prepare_candidates` directly. This
   acquire materializes nothing, so a materializing plan's `trials_ns` pays a full
   `prepare_candidates` that `acquire_ns` never saw. Netting the *measured* prep separates "the model
   omits candidate production" from "the plan arm's own rates are wrong". Modelled proxies were tried
   and rejected (`cost::materialize_cost` prices a concat+sort; these branches extract from a bitmap).

Generates range queries exclusively, at `unique=card`, because that is the only shape this acquire
fires for (`card_range_popcount_applicable` requires `Mode::Card`) and the general `random_query()`
emits it roughly once in 150,000.

    .venv/bin/python scripts/bench_card_range_estimate.py --seconds 60
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import math
import pathlib
import random
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from scripts.bench_bitplanes import load_engine  # noqa: E402

TARGET_SOURCE = "card_range_popcount"
MATERIALIZING = ("StreamedSelect", "GatheredScan")
LIMIT = 100
NUM_WARMUPS = 3
NUM_TRIALS = 9
# Rows of the sorted table to show at each end before eliding the middle.
SHOW_HEAD = 8
SHOW_TAIL = 4
# statistics.quantiles needs at least this many samples for deciles.
MIN_FOR_DECILES = 10
# cost.rs's CARD_RANGE_BUILD_PER_PRINTING_NS: the fused scatter+project pass an exact distinct-card
# count would need, measured at 98333ns/80527 printings.
BUILD_PER_PRINTING_NS = 1.22

# Every field `bare_range_bounds` resolves to a range index: the three price columns and collector
# number have their own, `year:`/`date:` both map onto `released_at`. Values span each field's real
# distribution so the sweep covers selectivities from a handful of cards to nearly the whole corpus.
RANGE_FIELDS: dict[str, list[str]] = {
    "usd": ["0.1", "0.25", "1", "2", "5", "10", "20", "50", "100", "200", "500"],
    "eur": ["0.1", "0.5", "1", "3", "8", "20", "60", "150"],
    "tix": ["0.05", "0.1", "0.5", "1", "3", "10", "30"],
    "cn": ["5", "20", "50", "100", "200", "300", "500"],
    "year": ["1994", "1999", "2004", "2009", "2014", "2018", "2021", "2023", "2025"],
    "date": ["1995-01-01", "2005-06-01", "2015-03-01", "2020-09-01", "2024-01-01"],
}
RANGE_OPS = [">", ">=", "<", "<="]
DATE_FIELDS = ("year", "date")


def random_range_query(rng: random.Random) -> str:
    """One bare range predicate — the only filter shape this acquire branch fires for."""
    field = rng.choice(list(RANGE_FIELDS))
    # `year:2023` is a bounded range on released_at (bare_range_bounds handles YearCmp/DateCmp), so
    # it belongs in the sweep; `:` on a price column is equality and mostly yields empty results.
    ops = [*RANGE_OPS, ":"] if field in DATE_FIELDS else RANGE_OPS
    return f"{field}{rng.choice(ops)}{rng.choice(RANGE_FIELDS[field])}"


def main() -> None:
    """Sweep range queries, checking both the estimate and the measured materialization split."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".misselect.store"))
    rng = random.Random(args.seed)

    samples = Samples()
    seen: set[str] = set()
    generated = other_source = 0
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        q = random_range_query(rng)
        generated += 1
        if q in seen:
            continue
        seen.add(q)
        try:
            filters = parse_scryfall_query(q)
            kw = {"filters": filters, "unique": "card", "orderby": "edhrec", "direction": "asc", "limit": LIMIT, "offset": 0}
            quick = engine.explain(**kw)
            if quick["acquire"]["count_source"] != TARGET_SOURCE:
                other_source += 1
                continue
            total = engine.query(prefer="default", **{**kw, "limit": 1})[0]
            res = engine.explain_analyze(prefer="default", num_warmups=NUM_WARMUPS, num_trials=NUM_TRIALS, **kw)
        except Exception:  # noqa: BLE001, S112 - a query bind rejects is a sample skip
            continue
        samples.record(q, quick, res, total)

    if not samples.est_rows:
        print(f"no {TARGET_SOURCE} samples in the budget ({generated:,} generated, {other_source:,} other acquire)")
        return

    report(samples.est_rows, samples.arm_rows, args.seconds, generated, other_source)
    score_estimators(samples.est_rows, samples.n_cards, engine.size(), [r[0] for r in samples.k_rows])
    by_slice_size(samples.k_rows, samples.picks)


def score_estimators(est_rows: list[tuple[float, int, int, str]], n_cards: int, n_printings: int, k_seen: list[int]) -> None:
    """Score replacements for `card_est` against the real total, before changing the engine.

    `card_est = k.min(n_cards)` uses an in-range PRINTING count as a card count. The obvious cheap
    correction scales by the corpus-wide card:printing ratio, which costs one multiply and keeps the
    branch's O(1) promise (counting distinct cards in the slice is the O(k) build it defers to
    dispatch on purpose). Scored on |log ratio| so over- and under-estimates are penalised alike.
    """
    if not n_cards or not n_printings:
        return
    ratio = n_cards / n_printings
    # `est == k` whenever the clamp did not bite, which is the only regime a rescale can change.
    rows = [(est, total) for _, est, total, _ in est_rows if total > 0]
    if not rows:
        return

    def score(name: str, f) -> tuple[float, float, str]:  # noqa: ANN001 - local scoring helper
        errs = [abs(math.log(max(f(est), 1) / total)) for est, total in rows]
        ratios = sorted(max(f(est), 1) / total for est, total in rows)
        return statistics.median(errs), statistics.median(ratios), name

    # Balls into bins: if `k` printings were an exchangeable draw from `n_printings` spread over
    # `n_cards` cards, the expected number of distinct cards is the classic occupancy result. Two
    # parameterisations, since neither is obviously the right one for a value-sorted index slice.
    prints_per_card = n_printings / n_cards

    def occupancy_exp(est: int) -> int:
        return min(round(n_cards * (1.0 - math.exp(-est / n_cards))), n_cards)

    def occupancy_pow(est: int) -> int:
        return min(round(n_cards * (1.0 - (1.0 - min(est / n_printings, 1.0)) ** prints_per_card)), n_cards)

    scored = [
        score("k.min(n_cards) (current)", lambda est: min(est, n_cards)),
        score("k * n_cards/n_printings", lambda est: min(round(est * ratio), n_cards)),
        score("k * 0.5", lambda est: min(round(est * 0.5), n_cards)),
        score("k * 0.75", lambda est: min(round(est * 0.75), n_cards)),
        score("occupancy 1-exp(-k/n_cards)", occupancy_exp),
        score("occupancy, per-card prints", occupancy_pow),
    ]
    print(f"\nreplacements for card_est (n={len(rows)}, card:printing = {ratio:.3f})")
    print(f"{'estimator':<28}{'median |log err|':>18}{'median est/real':>17}")
    for err, med, name in sorted(scored):
        print(f"{name:<28}{err:>18.3f}{med:>17.2f}")
    print("  lower |log err| is better; median est/real near 1.00 means unbiased.")

    # Would an EXACT count be affordable instead? It needs one O(k) pass scattering each in-range
    # printing to its card bit (the `printing_to_card` direct lookup) plus a popcount — which is
    # exactly `build_card_range_bits`, measured at CARD_RANGE_BUILD_PER_PRINTING_NS in cost.rs. The
    # branch defers that to dispatch today, but CardRangePopcount pays it anyway when it wins, so
    # the marginal cost is only incurred when a materializing plan wins instead.
    ks = sorted(k for k in k_seen if k > 0)
    if not ks:
        return
    words = n_cards / 64.0
    print(f"\ncost of an exact count instead (k printings x {BUILD_PER_PRINTING_NS} ns + {words:.0f} words)")
    print(f"{'k percentile':<16}{'k':>10}{'exact-count µs':>17}")
    for label, kv in (("median", ks[len(ks) // 2]), ("p90", ks[int(len(ks) * 0.9)]), ("max", ks[-1])):
        print(f"{label:<16}{kv:>10,}{(kv * BUILD_PER_PRINTING_NS + words) / 1000:>17.1f}")
    print("  compare against the whole query: these range queries route at ~4-100 µs today.")


@dataclasses.dataclass
class Samples:
    """Everything the report needs, accumulated one query at a time."""

    est_rows: list[tuple[float, int, int, str]] = dataclasses.field(default_factory=list)
    k_rows: list[tuple[int, float, int, str]] = dataclasses.field(default_factory=list)
    arm_rows: list[tuple[str, float, float, str]] = dataclasses.field(default_factory=list)
    picks: collections.Counter[str] = dataclasses.field(default_factory=collections.Counter)
    n_cards: int = 0

    def record(self, q: str, quick: dict, res: dict, total: int) -> None:
        """Fold one sampled query in. `matches` is clamped at n_cards here, so k comes from range_k."""
        acq = quick["acquire"]
        est = acq["matches"]
        self.n_cards = acq["n_cards"]
        self.est_rows.append((est / max(total, 1), est, total, q))
        self.k_rows.append((acq["range_k"], est / max(total, 1), total, q))
        self.picks[next((p["plan"] for p in quick["plans"] if p["picked"]), "?")] += 1
        prep = min(res["acquire"]["prep_ns"])
        for p in res["plans"]:
            if p["plan"] not in MATERIALIZING or not p["trials_ns"] or p["predicted_ns"] <= 0:
                continue
            meas = max(min(p["trials_ns"]), 1)
            self.arm_rows.append((p["plan"], meas / p["predicted_ns"], max(meas - prep, 1) / p["predicted_ns"], q))


def by_slice_size(k_rows: list[tuple[int, float, int, str]], picks: collections.Counter[str]) -> None:
    """Is the estimate's error selectivity-dependent, and is it worst where an exact count is cheap?

    If the two are inversely related there is no need to always build the bitmap: take the exact count
    below some `k` where it is cheap, and trust the estimate above it where it is already accurate.
    """
    print(f"\n{'k (printings)':<16}{'n':>5}{'median est/real':>17}{'build µs':>10}")
    buckets = (
        (0, 1_000, "< 1k"),
        (1_000, 5_000, "1k-5k"),
        (5_000, 20_000, "5k-20k"),
        (20_000, 60_000, "20k-60k"),
        (60_000, 1 << 30, "60k+"),
    )
    for lo, hi, label in buckets:
        grp = [r for r in k_rows if lo <= r[0] < hi]
        if not grp:
            continue
        med_k = statistics.median(r[0] for r in grp)
        print(f"{label:<16}{len(grp):>5}{statistics.median(r[1] for r in grp):>17.2f}{med_k * BUILD_PER_PRINTING_NS / 1000:>10.1f}")
    print("  est/real near 1.00 means the proxy is already fine there; build µs is what an exact")
    print("  count would cost at that slice size.")

    total = sum(picks.values())
    print(f"\nrouter's pick on these queries (n={total:,}) — a plan that does not want a card bitmap")
    print("pays for one built unconditionally in acquire:")
    for plan, n in picks.most_common():
        print(f"  {plan:<22}{n:>5}{n / max(total, 1):>7.0%}")


def report(
    est_rows: list[tuple[float, int, int, str]],
    arm_rows: list[tuple[str, float, float, str]],
    seconds: float,
    generated: int,
    other_source: int,
) -> None:
    """Estimate accuracy, then the arm error with measured candidate production netted out."""
    print(f"\n{len(est_rows):,} {TARGET_SOURCE} queries in {seconds:.0f}s ({generated:,} generated, {other_source:,} other)")

    ratios = [r[0] for r in est_rows]
    print(f"\ncard_est / real total: median {statistics.median(ratios):.2f}  min {min(ratios):.2f}  max {max(ratios):.2f}")
    print("  >1 means the acquire OVER-estimates, which over-costs — the opposite sign to the arm error below.")

    print(f"\n{'plan':<18}{'n':>5}{'raw median':>12}{'prep-netted':>13}{'p10':>8}{'p90':>8}")
    for plan in MATERIALIZING:
        grp = [r for r in arm_rows if r[0] == plan]
        if not grp:
            continue
        nets = sorted(r[2] for r in grp)
        ds = statistics.quantiles(nets, n=10) if len(nets) >= MIN_FOR_DECILES else [float("nan")] * 9
        print(
            f"{plan:<18}{len(grp):>5}{statistics.median(r[1] for r in grp):>12.2f}"
            f"{statistics.median(nets):>13.2f}{ds[0]:>8.2f}{ds[8]:>8.2f}"
        )
    print("  raw >1 = under-costed. If prep-netted lands near 1 the gap was unpriced candidate")
    print("  production; if it stays high the plan arm's own rates are wrong at this operating point.")

    est_rows.sort()
    print(f"\n{'est/real':>9}{'estimate':>10}{'real':>8}  query")
    for ratio, est, total, q in est_rows[:SHOW_HEAD]:
        print(f"{ratio:>9.2f}{est:>10,}{total:>8,}  {q[:40]}")
    if len(est_rows) > SHOW_HEAD:
        print("  ...")
        for ratio, est, total, q in est_rows[-SHOW_TAIL:]:
            print(f"{ratio:>9.2f}{est:>10,}{total:>8,}  {q[:40]}")


if __name__ == "__main__":
    main()
