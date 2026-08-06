# Estimating distinct cards in a range slice (one-sided)

**DONE for one-sided ranges and `Eq` — candidate 2 shipped as `RangeCardCounts`**
(`card_engine/src/lib.rs`), with the artwork triple added in
[#841](https://github.com/jbylund/sylvan_librarian/pull/841) at a measured **156.6 KB** against this
doc's 159 KB estimate. Six dimensions carry one: `released_at`, `price_usd`, `price_eur`, `price_tix`,
`collector_number`, `rarity`. `<` / `<=` / `>` / `>=` / `Eq` are now **exact in all three spaces**;
`usd>=0.42`/card reads 12,408 against a true 12,408.

**Split: the two-sided and interior-interval half is
[#853](../00853-engine-interior-range-distinct-counts.md).** The prefix/suffix arrays answer an interval
that runs to an *edge* of the index; an interior one is not derivable from them, which the "what was tried
and rejected" table below proves (`suf[i] != total - pre[i]` — distinct counts do not subtract). Two shapes
are still inexact: `year:Y`, which reaches `distinct_cards` and gets `None`, falling back to the
`k.min(n_cards)` proxy measured below; and fused two-sided ranges, which `exact_result_total` declines
outright because `bare_range_bounds` does not match `And`.

**One conclusion in this doc is now void.** The "Which shapes actually reach this acquire" section argues
that two-sided conjunctions never reach the range index, and on that basis parks candidate 3 and calls
candidate 2 plus two small arrays complete. [#837](local-engine-two-sided-range-fusion.md) fuses same-index
`And` children into one range-index interval, so the premise is false and candidate 3's own parking condition
— *"unless two-sided conjunctions are ever routed to the range index"* — has fired. Read that section as
history; #853 re-decides it.

Still unshipped from the Plan below: the **~34 per-year counts on `date`**, which is exactly why the
`year:Y` fallback survives.

`CardRangePopcount`'s acquire feeds `matches`/`eval_domain`/`scan_units` from
`card_est = k.min(n_cards)`, where `k` counts in-range **printings** and stands in for a card count —
a proxy the branch's own comment flags. Measured against the true total on realistic ranges:

| estimator | p10 | p50 | p75 | p90 | p99 | max |
| --------- | --: | --: | --: | --: | --: | --: |
| `min(k, n_cards)` (current) | 1.14 | **1.49** | 1.65 | **1.82** | **4.33** | 4.33 |

Not a tail problem — the median is 1.49x and p10 is already 1.14, so it over-estimates nearly
everywhere, because printings outnumber cards.

Split out of [local-engine-plan-misselection.md](local-engine-plan-misselection.md), which found the
proxy while investigating why every plan costed off that acquire is under-costed 2.0–2.6x.

## Nothing here ships alone

Moved to [#853](../00853-engine-interior-range-distinct-counts.md#nothing-here-ships-alone), because it
governs the work that is left rather than the work that shipped. In short: this estimate's error and the plan
arms' error point in opposite directions and partially cancel, so correcting one alone regresses routing.

## The surviving candidates

Candidate 1 (build the card bitmap in acquire) and candidate 3 (prev-array + wavelet tree) were both
unshipped and are now
[the live options in #853](../00853-engine-interior-range-distinct-counts.md#the-live-options), along with the
trapezoid-histogram fallback. Only candidate 2 shipped, and it is kept below as the record of what was built.

### 2. One boundary table, three counts — exact for every shape here, 159 KB

The dimensions have far fewer distinct **values** than printings: 914 dates against 97,206 printings,
4,133 usd. Printings sharing a value are contiguous in the value-sorted index, so *any* threshold —
present in the data or not — bisects to a value boundary. There is no between-buckets case to
interpolate.

Store three counts per boundary, all produced by one build pass and served by the one binary search
the acquire already performs to get `k`:

| column | distinct cards in | serves |
| ------ | ----------------- | ------ |
| `pre[i]` | `[0, boundary_i)` | `<`, `<=` |
| `suf[i]` | `[boundary_i, n)` | `>`, `>=` |
| `val[i]` | `[boundary_i, boundary_{i+1})` | `Eq` |

| dim | boundaries | pre+suf | val | total |
| --- | ---------: | ------: | --: | ----: |
| date | 915 | 7,320 | 3,656 | 10,976 |
| tix | 1,208 | 9,664 | 4,828 | 14,492 |
| cn | 3,205 | 25,640 | 12,816 | 38,456 |
| eur | 3,925 | 31,400 | 15,696 | 47,096 |
| usd | 4,134 | 33,072 | 16,532 | 49,604 |
| **total** | | 107,096 | 53,528 | **160,624** |

Plus ~34 per-year counts on `date` for `year:Y`, which is a bounded range spanning many values.

| | |
| --- | --- |
| accuracy | **exact** for every shape that reaches this acquire |
| memory | ~159 KB of archive data — needs an `ARCHIVE_FORMAT_VERSION` bump |
| query time | O(1) — the binary search already paid to get `k`, plus one array read |
| risk | low; no tuning parameter, and it tracks corpus growth one value at a time |

**`val[i]` cannot be derived from `pre`.** `count(usd<=2.99) − count(usd<2.99)` looks like it should give
the `Eq` answer and does not: it is 10 against a true 54, because 44 of those cards also have a cheaper
printing and were already counted below the boundary. Subtraction yields cards whose *first* appearance
is at that value, not cards present at it. Distinct-card counts do not subtract over a multiset — one
card spans many prices — which is the same reason prefix/suffix cannot answer a bounded range, and the
reason the arith-tuple index escapes it (each card has exactly one tuple).

### 3. prev-array + wavelet tree — exact for arbitrary ranges, 1.06 MB

**Moved to [#853](../00853-engine-interior-range-distinct-counts.md#3-prev-array--wavelet-tree--exact-for-arbitrary-ranges-106-mb).**
It was parked here as "probably not needed" on the strength of the scoping argument immediately below, and
that argument no longer holds.

## Which shapes actually reach this acquire

> **SUPERSEDED by [#837](local-engine-two-sided-range-fusion.md).** The argument below is what parked
> candidate 3 and made candidate 2 look complete, and its premise no longer holds: fusion turns a two-sided
> conjunction into a single range-index interval in `narrow_rec` and the compose builders. The claim about
> `bare_range_bounds` not matching `And` is still literally true — which is why `exact_result_total` now
> *declines* two-sided ranges in every mode rather than estimating them — but the conclusion that two-sided
> shapes are out of scope is void. See [#853](../00853-engine-interior-range-distinct-counts.md).

This narrows the problem sharply, and was established late. `bare_range_bounds` matches only
`NumericCmp`, `DateCmp`, `YearCmp` and `Not` of those — **not `And`**. So a two-sided conjunction like
`cn>=441 cn<=447` never reaches this acquire; it goes through the general candidates route, where the
estimate is not `card_est` at all. It was treated as the motivating bounded case for several turns and
is not one.

Of the ops that do reach it, every one except `Eq` is one-sided: `Lt`/`Le` give `(0, x)`, `Gt`/`Ge`
give `(x, MAX)`. The complete bounded set is:

| shape | range | answered exactly by |
| ----- | ----- | ------------------- |
| `usd:5`, `cn:441` | `[v, v+1)` — a single distinct value | a per-value distinct count, one entry |
| `date:2023-01-01` | `[d, d+1)` — a single distinct value | the same array |
| `year:2023` | `[y·10⁴, (y+1)·10⁴)` — one calendar year | a per-year count on `date`, ~34 entries |

So candidate (2) plus two small arrays is exact for every shape that reaches this acquire, with no
wavelet, no banded table, no MCV and no dependency.

**`Not` introduces no new shapes**, checked: it applies `negate_op` and reuses the same bounds
functions. `-usd>20` becomes `Le` → `(0, 21)`; `-usd<5` becomes `Ge` → `(5, MAX)`. And negating `Eq`
gives `Ne`, which returns `None` and never narrows — so `Not` cannot produce a bounded range at all,
making it strictly easier than the positive case. (`-usd!=5` inverts to `Eq`, a single value, which the
per-value array covers.)

The engine's documented negation hazards are a different category and do not apply here:
`range_narrowed`'s note that price bounds are widened for float rounding, so "a Not would complement
away the boundary printings", is about the **tightness of the candidate set** — which cards come back.
These arrays feed only the cost estimate, so an error there would degrade routing, not results.

## Plan — how it actually went

1. **The boundary table (candidate 2). SHIPPED**, at a measured 156.6 KB, with the artwork triple added in
   [#841](https://github.com/jbylund/sylvan_librarian/pull/841). The *"add the ~34 per-year counts on `date`
   in the same change; after it the proxy is gone"* half **did not ship**, which is why `year:Y` still falls
   back to `k.min(n_cards)`.
2. **Re-ask the arm question.** Not done. Now
   [#852](../00852-engine-compose-acquire-p3-p4-ranking.md), whose oracle run answers the sequencing question
   this item left open: features before rates, and `eval_domain` carries ~75% of the recoverable loss.

The per-year counts, candidate 1, candidate 3 and the trapezoid fallback are all
[live options in #853](../00853-engine-interior-range-distinct-counts.md#the-live-options). Candidate 3's
parking condition — *"unless two-sided conjunctions are ever routed to the range index"* — has since fired.

## What was tried and rejected

All measured; recorded so the next attempt does not repeat them.

| approach | result | why it fails |
| -------- | ------ | ------------ |
| rescale `card_est` by the card:printing ratio | worse than current | scored on a **clamped** input; the ranking was an artifact |
| `k / prints-per-card` per dimension | 0.126 median, worst of three below k=5k | duplication is not constant in `k` |
| occupancy (balls into bins) | 0.161 median | wins `usd<` at every slice size, loses `usd>` at every one |
| equi-depth histogram | 0.005 median, **3.7 max** | the median hid a 150x tail on sub-bucket slices |
| more buckets | fixes the mid-band, not small-k | still 1.409 at 256 buckets and 658 KB |
| point sampling, even or random | 1.32–1.45 | needs collisions; ~2.5 printings per card means a sparse sample never collides |
| cluster sampling in runs | 0.615 | 2x better than point sampling, still 5x worse than a free constant |
| per-card `(first, last)` intervals | 0.390 median | exact for 3 of 4 cases; `year:2015` has 5,919 spanners against 1,642 real matches, and the fraction actually inside swings 11–69% |
| banded table + MCV 3,000 | p90 1.07x, p99 1.46x | works, but ~760 KB and the MCV lookup costs 6,000 probes as written (~12 KB scanned as bitmaps) — dominated by (3) |

One cause runs through the failures: **duplication is non-local and predicate-dependent**. A card's
printings are spread across the value range, so no window or sample recovers them; and whether a
predicate selects whole cards (`usd>x` — expensive cards are uniformly expensive) or one printing each
(`usd<x` — every card has a cheapest printing) flips the duplication factor from ~3 to ~1 at the same
slice size.

## Measurement traps worth keeping

Four wrong answers here came from the same habit, so the note is itself the finding:

- **Pooled medians hide the structure that matters.** The 2x–50x compose spread was two populations;
  the estimator ranking flipped by slice size; the "k-regime" split was really a direction split; the
  histogram's 0.005 median concealed a 150x tail. Quote per-cell breakdowns.
- **Scaling a clamped value.** `card_est` is `k.min(n_cards)`, so scaling it on a broad range scales
  31,508, not `k`. That inverted an entire estimator ranking until `acquire.range_k` exposed real `k`.
- **Interpolate both endpoints.** Doing only the right one snaps the left edge to a cut and widens
  every bounded slice by up to a bucket — an 8x median error.
- **The out-of-band fallback must be the inclusion-exclusion bound** `pre[j] + suf[i] - total`.
  Substituting the total distinct count inflates bounded error ~2x, and did, which made MCV look
  useless on a first pass.

## Acceptance criteria

`scripts/bench_range_estimate_scan.py` is the test. It sweeps thresholds across each dimension in both
directions and reports `acquire.matches / true total` per point — scans rather than a pooled median,
because every pooled figure in this investigation hid structure that only showed up per cell.

**Target:** every `unique=card` cell within 1% of true. The baseline below is a **historical record** — these
eight one-sided cells now answer exactly from `RangeCardCounts`. The cells that are still short are the
interior ones, and the current acceptance bar lives in
[#853](../00853-engine-interior-range-distinct-counts.md#acceptance).

Baseline at the time of writing, 0 of 8 cells passing:

| cell | n | median | worst |
| ---- | -: | -----: | ----: |
| `usd<x` | 6 | 1.047 | 1.707 |
| `usd>x` | 6 | 1.975 | 2.691 |
| `cn<x` | 3 | 1.643 | 1.789 |
| `cn>x` | 3 | 1.437 | 1.976 |
| `year<x` | 4 | 1.993 | 2.527 |
| `year>x` | 4 | 1.371 | 2.020 |
| `date<x` | 3 | 2.159 | 2.183 |
| `date>x` | 3 | 1.357 | 1.865 |

**Control — `PrintingRangeScan` needs no change, confirmed.** All eight cells at `unique=printing`
already read 1.000 across 26 scan points: that branch sets `matches = k`, the in-range printing count,
and in printing mode that *is* the result cardinality. Those rows must stay at 1.000.

~~**Out of scope:** `eur` and `tix`, which have no range index and never reach this acquire — a separate
and much larger defect, see [local-engine-eur-tix-range-index.md](local-engine-eur-tix-range-index.md).~~
**No longer true.** [#838](https://github.com/jbylund/sylvan_librarian/pull/838) gave both a
`PrintingValueIndex`, and both now carry a `RangeCardCounts` (`price_eur_cards`, `price_tix_cards`). They are
in scope for the scan; see [#853](../00853-engine-interior-range-distinct-counts.md#acceptance).

Routing is deliberately **not** an acceptance criterion for this change alone. Correcting the estimate
lowers predicted cost for the materializing plans, which are already over-picked, so routing should
regress until the arm fix lands with it — hence the stacked-PR plan of merging both together.

## Reproducing

    .venv/bin/python scripts/bench_card_range_estimate.py --seconds 60   # engine-side, live store
    .venv/bin/python scripts/study_range_slice_cardinality.py            # offline: closed forms
    .venv/bin/python scripts/study_range_slice_layouts.py                # offline: table shapes

The offline studies read `benchmarks/bitplanes/corpus.jsonl` directly and cache the extraction, which
lets them enumerate thousands of thresholds instead of the ~142 distinct range queries the engine-side
generator can produce. Bounded ranges are scored on realistic intervals — calendar years, set-sized
collector blocks, price bands — rather than uniformly random ones. n=84 there, thin for a tail claim,
and the interval boundaries were chosen by hand.
