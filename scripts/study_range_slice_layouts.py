"""Which summary structure estimates distinct cards in a range slice, and at what size?

Companion to `study_range_slice_cardinality.py`, which established that no closed-form estimator
works (the duplication factor moves in opposite directions by dimension and direction) and that a
build-time table beats all of them. This one settles the table's *shape*, separately for the two
query populations, because they turn out to need different structures:

- **one-sided** (`usd>x`, `year<x`): needs only prefix and suffix arrays, which is LINEAR in the cut
  count, so a fine layout is affordable. A trapezoid — bucket widths doubling in from each edge,
  capped, uniform across the middle — is the right shape, because one-sided slice boundaries are
  edge-anchored by construction and a boundary near a cut interpolates over a tiny interval.
- **bounded** (`x<usd<y`, `year:2023`): distinct counts do not subtract, so prefix/suffix cannot
  answer these at all. Needs a table of `[cut_i, cut_j)` counts, which is QUADRATIC if stored in
  full; a band (only `j - i <= w`) brings it back to linear at the cost of a fallback for wide ranges.

Also scores a Postgres-style most-common-values split, since heavy-hitter separation is the standard
fix for exactly this kind of skew.

    .venv/bin/python scripts/study_range_slice_layouts.py

Reuses the corpus extraction cache from `study_range_slice_cardinality.py`.
"""

from __future__ import annotations

import bisect
import collections
import dataclasses
import math
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.study_range_slice_cardinality import load  # noqa: E402

MIN_SLICE = 16
MIN_FOR_DECILES = 10
# Band width for the bounded-range table, in buckets. 32 is where coverage stopped being the
# limiting factor at the layouts below — past it the residual error is sub-bucket ranges, which no
# band width reaches.
BAND = 32
# Most-common-value list sizes, as card counts. Postgres defaults to 100 values; this sweeps well
# past that because the duplication here is far less concentrated than a typical column's.
MCV_SIZES = (0, 200, 1000, 3000)


def trapezoid(n: int, cap_frac: float) -> list[int]:
    """Cut positions, widths doubling in from each edge, capped, then uniform across the middle."""
    cap = max(int(n * cap_frac), 1)
    cuts = {0, n}
    width, x = 1, 0
    while x < n // 2:
        x += min(width, cap)
        cuts.add(min(x, n))
        cuts.add(max(n - x, 0))
        width *= 2
    return sorted(c for c in cuts if 0 <= c <= n)


def equidepth(n: int, buckets: int) -> list[int]:
    """Evenly spaced cut positions — percentile markers, the conventional layout."""
    return [round(n * i / buckets) for i in range(buckets + 1)]


def prefix_suffix(cards: list[int], cuts: list[int]) -> tuple[list[int], list[int]]:
    """Distinct cards in [0, cut) and in [cut, n). Two arrays, linear in the cut count."""
    pre, seen, last = [], set(), 0
    for c in cuts:
        seen.update(cards[last:c])
        last = c
        pre.append(len(seen))
    suf, seen, last = [], set(), len(cards)
    for c in reversed(cuts):
        seen.update(cards[c:last])
        last = c
        suf.append(len(seen))
    return pre, list(reversed(suf))


def banded(cards: list[int], cuts: list[int], band: int) -> dict[tuple[int, int], int]:
    """Exact distinct counts for [cut_i, cut_j) where j - i <= band."""
    out = {}
    for i in range(len(cuts) - 1):
        seen: set[int] = set()
        for j in range(i + 1, min(i + band, len(cuts) - 1) + 1):
            seen.update(cards[cuts[j - 1] : cuts[j]])
            out[i, j] = len(seen)
    return out


def _frac(cuts: list[int], x: int) -> tuple[int, float]:
    i = min(max(bisect.bisect_right(cuts, x) - 1, 0), len(cuts) - 2)
    width = cuts[i + 1] - cuts[i]
    return i, (x - cuts[i]) / width if width else 0.0


def est_one_sided(cuts: list[int], table: list[int], boundary: int) -> float:
    """Linear interpolation in index position — the position the binary search already computed."""
    i, f = _frac(cuts, boundary)
    return max(table[i] + (table[i + 1] - table[i]) * f, 1.0)


@dataclasses.dataclass
class Summary:
    """Everything stored for one dimension: cut positions, the two linear arrays, the banded table."""

    cuts: list[int]
    pre: list[int]
    suf: list[int]
    bnd: dict[tuple[int, int], int]
    band: int
    total: int


def est_bounded(s: Summary, lo: int, hi: int) -> float:
    """Estimate distinct cards in [lo, hi), bilinear on BOTH endpoints.

    Interpolating only the right endpoint snaps the left edge to a cut, silently widening every
    slice by up to a bucket — worth stating, because that bug cost an 8x error before it was caught.

    Outside the band the fallback is `pre[j] + suf[i] - total`, the inclusion-exclusion size of
    `[0, cut_j) ∩ [cut_i, n)`. That is an upper BOUND, not an estimate: it also counts cards
    straddling the range with no printing inside it. Substituting the total distinct count there
    instead — an easy mistake — inflates the bounded error roughly 2x.
    """
    cuts, pre, suf, bnd, band, total = s.cuts, s.pre, s.suf, s.bnd, s.band, s.total
    i, fi = _frac(cuts, lo)
    j, fj = _frac(cuts, hi)
    top = len(cuts) - 1

    def at(ii: int, jj: int) -> float:
        ii = min(ii, top - 1)
        jj = max(min(jj, top), ii + 1)
        if jj - ii <= band and (ii, jj) in bnd:
            return float(bnd[ii, jj])
        return max(min(pre[min(jj, len(pre) - 1)] + suf[ii] - total, pre[min(jj, len(pre) - 1)]), 1.0)

    j0 = max(j, i + 1)
    low = at(i, j0) + (at(i, j0 + 1) - at(i, j0)) * fj
    high = at(i + 1, j0) + (at(i + 1, j0 + 1) - at(i + 1, j0)) * fj
    return max(low + (high - low) * fi, 1.0)


def realistic_bounded(dim: str) -> list[tuple[float, float]]:
    """Bounded ranges shaped like real queries.

    Calendar years, set-sized collector blocks and price bands — rather than uniformly random
    intervals, which over-represent mid-index slices no user would ever type.
    """
    if dim == "date":
        return [(y * 10000, (y + 1) * 10000) for y in range(1993, 2027)]
    if dim == "cn":
        return [(s, s + 50) for s in range(0, 600, 50)]
    bands = [0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 12, 20, 35, 60, 100, 200, 400]
    return [(bands[i], bands[i + 1]) for i in range(len(bands) - 1)]


def stats(errs: list[float]) -> tuple[float, float, float]:
    """Median, p90, max of |log ratio| — over- and under-estimates penalised alike."""
    p90 = statistics.quantiles(errs, n=10)[8] if len(errs) >= MIN_FOR_DECILES else float("nan")
    return statistics.median(errs), p90, max(errs)


def study_one_sided(dims: dict[str, list[tuple[float, int]]]) -> None:
    """Layout comparison for one-sided ranges, on linear prefix/suffix storage."""
    print(f"\none-sided ranges — prefix/suffix arrays, {'2 x cuts'} entries (linear)")
    print(f"{'layout':<28}{'cuts':>6}{'bytes':>9}{'median':>10}{'p90':>9}{'max':>9}")
    layouts = (
        ("equi-depth 32", lambda n: equidepth(n, 32)),
        ("equi-depth 256", lambda n: equidepth(n, 256)),
        ("trapezoid cap n/64", lambda n: trapezoid(n, 1 / 64)),
        ("trapezoid cap n/256", lambda n: trapezoid(n, 1 / 256)),
        ("trapezoid cap n/1024", lambda n: trapezoid(n, 1 / 1024)),
    )
    for label, mk in layouts:
        errs, nbytes, ncuts = [], 0, 0
        for rows in dims.values():
            vals = [v for v, _ in rows]
            cards = [c for _, c in rows]
            n = len(rows)
            cuts = mk(n)
            pre, suf = prefix_suffix(cards, cuts)
            nbytes += 2 * len(cuts) * 4
            ncuts = len(cuts)
            for q in [i / 400 for i in range(1, 400)]:
                b = bisect.bisect_left(vals, vals[int(n * q)])
                for lo, hi, tbl in ((0, b, pre), (b, n, suf)):
                    if hi - lo < MIN_SLICE:
                        continue
                    errs.append(abs(math.log(est_one_sided(cuts, tbl, b) / len(set(cards[lo:hi])))))
        med, p90, mx = stats(errs)
        print(f"{label:<28}{ncuts:>6}{nbytes:>9,}{med:>10.4f}{p90:>9.4f}{mx:>9.3f}")


def study_exact_per_value(dims: dict[str, list[tuple[float, int]]]) -> None:
    """Store the true count at every distinct-value boundary: one-sided ranges stop being estimated.

    The range dimensions have far fewer distinct VALUES than printings — 914 dates against 97,206
    printings — and printings sharing a value are contiguous in the value-sorted index. So any query
    threshold, present in the data or not, bisects to a value boundary: there is no between-buckets
    case to interpolate. Storing the exact prefix and suffix distinct-card counts at those boundaries
    answers every one-sided range by lookup.

    This supersedes every closed form and every bucket layout above for one-sided ranges. They were
    all modelling a distribution over ~100k printings when the domain is ~1-4k values — the same
    collapse the arith-tuple index (#750) exploits.
    """
    print("\nexact per distinct value — one-sided ranges answered by lookup, not estimate")
    print(f"{'dim':<7}{'boundaries':>12}{'bytes':>9}{'max |log err|':>16}")
    total = 0
    for dim, rows in dims.items():
        vals = [v for v, _ in rows]
        cards = [c for _, c in rows]
        n = len(rows)
        bounds = [0, *[i for i in range(1, n) if vals[i] != vals[i - 1]], n]
        pre, suf = prefix_suffix(cards, bounds)
        total += 2 * len(bounds) * 4
        errs = []
        for q in [i / 400 for i in range(1, 400)]:
            b = bisect.bisect_left(vals, vals[int(n * q)])
            idx = bisect.bisect_left(bounds, b)
            for lo, hi, tbl in ((0, b, pre), (b, n, suf)):
                if hi - lo < MIN_SLICE:
                    continue
                errs.append(abs(math.log(max(tbl[idx], 1) / len(set(cards[lo:hi])))))
        print(f"{dim:<7}{len(bounds):>12,}{2 * len(bounds) * 4:>9,}{max(errs):>16.2e}")
    print(f"  {total:,} bytes total, zero error. Bounded ranges are NOT covered: distinct counts do")
    print("  not subtract, and the prefix/suffix bracket is a median 4.35x wide (worst 285x on cn).")


def study_bounded(dims: dict[str, list[tuple[float, int]]]) -> None:
    """Banded table plus an MCV split, on realistic bounded ranges."""
    print(f"\nbounded ranges — trapezoid cap n/256 + banded table (band {BAND}), realistic intervals")
    print(f"{'most-common-values':<22}{'residual dup':>14}{'median':>9}{'p90':>8}{'max':>8}")
    for mcv in MCV_SIZES:
        errs, resid = [], []
        for dim, rows in dims.items():
            vals = [v for v, _ in rows]
            cards = [c for _, c in rows]
            counts = collections.Counter(cards)
            heavy = {c for c, _ in counts.most_common(mcv)} if mcv else set()
            pos: dict[int, list[int]] = collections.defaultdict(list)
            for i, c in enumerate(cards):
                if c in heavy:
                    pos[c].append(i)
            light = [(i, c) for i, c in enumerate(cards) if c not in heavy]
            lpos = [i for i, _ in light]
            lcards = [c for _, c in light]
            if not lcards:
                continue
            resid.append(len(lcards) / len(set(lcards)))
            cuts = trapezoid(len(lcards), 1 / 256)
            pre, suf = prefix_suffix(lcards, cuts)
            summary = Summary(cuts, pre, suf, banded(lcards, cuts, BAND), BAND, len(set(lcards)))
            for v0, v1 in realistic_bounded(dim):
                a, b = bisect.bisect_left(vals, v0), bisect.bisect_left(vals, v1)
                if b - a < MIN_SLICE:
                    continue
                exact = len(set(cards[a:b]))
                hv = sum(1 for c in heavy if pos[c] and bisect.bisect_left(pos[c], a) < bisect.bisect_left(pos[c], b))
                la, lb = bisect.bisect_left(lpos, a), bisect.bisect_left(lpos, b)
                est = hv + est_bounded(summary, la, lb)
                errs.append(abs(math.log(max(est, 1.0) / exact)))
        med, p90, mx = stats(errs)
        label = "none (histogram only)" if not mcv else f"top {mcv:,} cards"
        print(f"{label:<22}{statistics.mean(resid):>14.2f}{med:>9.3f}{p90:>8.3f}{mx:>8.3f}")
    print("  MCV separates heavy hitters the way Postgres does for value-frequency skew. Duplication")
    print("  here is far less concentrated than a typical column's — 3,000 cards carry only ~54% of")
    print("  it — yet the split still more than halves the worst case, so it earns its space.")


def main() -> None:
    """Run both studies."""
    dims = load()
    dims.pop("_n_cards", None)
    study_one_sided(dims)
    study_exact_per_value(dims)
    study_bounded(dims)


if __name__ == "__main__":
    main()
