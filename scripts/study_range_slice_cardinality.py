"""Offline study of the range-slice card-count estimate, straight from the corpus.

Answers three things the engine-side sweep could not, because it can enumerate thousands of
thresholds instead of the 142 the query generator produces, and can simulate sampling without an
engine hook:

1. Does the printing:card ratio in a slice differ by DIMENSION (price / collector number / date)?
2. Can a correction curve on the balls-into-bins estimate fit the k-dependence?
3. How accurate is sampling s printings from the slice and extrapolating distinct cards?

Extraction is cached, since parsing the 239 MB corpus takes a while.
"""

import bisect
import collections
import json
import math
import pathlib
import pickle
import statistics

CORPUS = pathlib.Path("/Users/joseph.bylund/scratch/sylvan_librarian/benchmarks/bitplanes/corpus.jsonl")
# Cache the extraction beside the corpus, not in scripts/ -- it is a ~10 MB derived artifact and
# benchmarks/ is already gitignored, so it never risks being committed.
CACHE = CORPUS.with_suffix(".slice-study.cache.pkl")
# Sample sizes to score the sampling estimator at, as printing counts.
SAMPLE_SIZES = (64, 256, 1024, 4096)
# Slices below this are too small for the ratio to mean anything.
MIN_SLICE = 16
# statistics.quantiles needs at least this many samples for deciles.
MIN_FOR_DECILES = 10
# "Close enough" band for the hit-rate columns.
NEAR_LO, NEAR_HI = 0.8, 1.2
# Estimators are compared against exact counts, so a per-dimension divisor needs the dimension's own
# printings-per-card — computed here rather than assumed corpus-wide, since tix indexes far fewer.


def load() -> dict[str, list[tuple[float, int]]]:
    """Per dimension, the (value, card_index) pairs sorted by value — the range index's own shape."""
    if CACHE.exists():
        with CACHE.open("rb") as fh:
            return pickle.load(fh)  # noqa: S301 - our own cache

    card_of: dict[str, int] = {}
    dims: dict[str, list[tuple[float, int]]] = {d: [] for d in ("usd", "eur", "tix", "cn", "date")}
    with CORPUS.open() as fh:
        for line in fh:
            r = json.loads(line)
            cid = card_of.setdefault(r["oracle_id"], len(card_of))
            for dim, key in (("usd", "price_usd"), ("eur", "price_eur"), ("tix", "price_tix")):
                v = r.get(key)
                if v is not None:
                    dims[dim].append((float(v), cid))
            cn = r.get("collector_number_int")
            if cn is not None:
                dims["cn"].append((float(cn), cid))
            d = r.get("released_at")
            if d:
                y, m, dd = d.split("-")[:3]
                dims["date"].append((int(y) * 10000 + int(m) * 100 + int(dd), cid))
    for v in dims.values():
        v.sort()
    dims["_n_cards"] = len(card_of)  # type: ignore[assignment]
    with CACHE.open("wb") as fh:
        pickle.dump(dims, fh)
    return dims


def main() -> None:
    """Enumerate slices per dimension and score the estimators against exact distinct-card counts."""
    dims = load()
    n_cards = dims.pop("_n_cards")  # type: ignore[arg-type]
    n_printings = sum(len(v) for v in dims.values())
    print(f"{n_cards:,} cards, {n_printings:,} indexed printings across {len(dims)} dimensions")

    per_dim: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)  # (k, exact distinct)
    sampling: dict[int, list[float]] = collections.defaultdict(list)

    for dim, rows in dims.items():
        vals = [v for v, _ in rows]
        cards = [c for _, c in rows]
        n = len(rows)
        # Sweep one-sided slices at many quantiles, both directions — the shapes `bare_range_bounds`
        # produces for `usd>x` / `usd<x` / `year<x`.
        for q in [i / 60 for i in range(1, 60)]:
            cut = vals[int(n * q)]
            for lo, hi in ((bisect.bisect_left(vals, cut), n), (0, bisect.bisect_left(vals, cut))):
                k = hi - lo
                if k < MIN_SLICE:
                    continue
                exact = len(set(cards[lo:hi]))
                per_dim[dim].append((k, exact))
                # Sampling: s evenly spaced printings from the slice, extrapolated by the classic
                # occupancy inversion — d distinct in s draws implies m cards where
                # d = m(1 - (1 - 1/m)^s); solved by bisection on m.
                for s in SAMPLE_SIZES:
                    if s >= k:
                        continue
                    step = k / s
                    seen = {cards[lo + min(int(i * step), k - 1)] for i in range(s)}
                    d = len(seen)
                    est = invert_occupancy(d, s, n_cards)
                    sampling[s].append(est / exact)

    print(f"\n{'dimension':<10}{'n slices':>10}{'median k/cards':>16}{'p10':>8}{'p90':>8}")
    for dim, rows in per_dim.items():
        ratios = sorted(k / ex for k, ex in rows)
        ds = statistics.quantiles(ratios, n=10)
        print(f"{dim:<10}{len(rows):>10}{statistics.median(ratios):>16.2f}{ds[0]:>8.2f}{ds[8]:>8.2f}")
    print("  k/cards is how many in-range printings each distinct card contributes — the factor the")
    print("  current estimator ignores by using k directly.")

    print(f"\n{'sample size':<14}{'n':>8}{'median est/exact':>18}{'p10':>8}{'p90':>8}{'within 20%':>12}")
    for s in SAMPLE_SIZES:
        rs = sorted(sampling[s])
        if len(rs) < MIN_FOR_DECILES:
            continue
        ds = statistics.quantiles(rs, n=10)
        near = sum(1 for r in rs if NEAR_LO <= r <= NEAR_HI) / len(rs)
        print(f"{s:<14}{len(rs):>8}{statistics.median(rs):>18.2f}{ds[0]:>8.2f}{ds[8]:>8.2f}{near:>11.0%}")
    print("  sampling cost is s printing_to_card lookups, independent of k.")


def invert_occupancy(d: int, s: int, n_cards: int) -> float:
    """Cards implied by seeing `d` distinct in `s` draws, under uniform occupancy."""
    if d >= s:
        return float(d) * (1.0 + 0.0)  # every draw distinct: the slice looks card-disjoint
    lo, hi = float(d), float(n_cards)
    for _ in range(60):
        mid = (lo + hi) / 2
        expected = mid * (1.0 - math.exp(-s / mid))
        if expected < d:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


if __name__ == "__main__":
    main()
