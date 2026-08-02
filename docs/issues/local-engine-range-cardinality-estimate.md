# Estimating distinct cards in a range slice

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

The proxy over-costs ~1.48x; the plan arms under-cost ~1.5x at this operating point. The two errors
point in opposite directions and **partially cancel**, which is why routing mostly survives them.
Correcting the estimate without re-fitting the arms pushes the arm error from 1.6x to ~2.4x.

The arm half belongs to the sibling doc, and cannot be fixed by re-fitting rates globally: the same
two plans are over-costed (0.57) off `candidates` acquires and under-costed (2.56) off this one, and
`STREAM_*`/`GATHER_*` are shared constants. This doc owns the estimate half only.

## The surviving candidates

### 1. Build the card bitmap in acquire — exact, no storage

`CardRangePopcount` already calls `build_card_range_bits` at dispatch, re-deriving the bounds to do
so, and it wins **138 of 142** sampled range queries. `StreamedSelect`/`GatheredScan` would take
`bitmap_card_ids` over their own `range_narrowed` → `into_cards`. Only `PrintingCompose` has no use
for a card bitmap, and it wins **zero**.

So promoting the build into the acquire branch and carrying it in `Prep` — the pattern `Prep::Plane`
already uses for the plane bitmap — is a reordering of work already done. Its popcount is the exact
`matches`/`eval_domain`, and it deletes a duplicate bounds derivation.

| | |
| --- | --- |
| accuracy | **exact** |
| memory | **none** — no archive data |
| query time | 0 in the 97% case (the winner builds it anyway); 47 µs median, 106 µs p90 when a plan wins that does not want it |
| risk | moves an O(k) build across the acquire/dispatch boundary; the both-fast-paths-decline case (`usd>50`) needs a test |

That 47 µs is why the storage options below still matter — it is the cost when the artifact goes
unused. Measured from `acquire.range_k`: median slice 38,245 printings at
`CARD_RANGE_BUILD_PER_PRINTING_NS` = 1.22.

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

Kept for completeness; **probably not needed** — see the next section.

The general problem is **range distinct count** (colored range counting). Let `prev[i]` be the index
of the previous printing of the same card, or −1. Then

    distinct cards in [a, b)  ==  #{ i in [a, b) : prev[i] < a }

because each card in the window has exactly one occurrence that is its first there, and only that one
points back outside it. A wavelet tree over `prev` answers that dominance count in O(log n), and the
tree encodes `prev`, so the array need not be stored. Verified exact on 2,583 windows across all five
dimensions — bounded, one-sided and random. Space is 1.06 MB, 134–252 KB per dimension.

No maintained Rust crate exposes the operation. [`qwt`](https://docs.rs/qwt/) has rank/select/access
only. [`sucds`](https://github.com/kampersanda/sucds) and [`vers`](https://github.com/Cydhra/vers) —
both Apache-2.0, both actively maintained — expose `quantile(range, k)`, the inverse, which would have
to be binary-searched at roughly 17x the work. [`wavelet-matrix`](https://github.com/sekineh/wavelet-matrix-rs)
has exactly `count_lt(pos_range, value)` and is MIT (declared in `Cargo.toml`; there is no LICENSE
file, which is why GitHub's API reports none) but was last touched in 2022. Vendoring the ~200 lines
that path needs — `prefix_rank_op`, the struct, and a rank-capable bitvector the engine can supply
itself — is viable under an MIT attribution header if it is ever wanted.

## Which shapes actually reach this acquire

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

## Plan

1. **The boundary table (candidate 2).** ~159 KB, O(1), no dependency, no tuning parameter — three
   columns built in one pass, exact for every shape that reaches this acquire. Add the ~34 per-year
   counts on `date` in the same change; after it the proxy is gone.
2. **Re-ask the arm question.** With exact features, part of the arms' ~1.5x under-costing may
   disappear — it may be the model charging `card_est`-shaped terms for true-card-shaped work. Re-fit
   only afterwards, and only against prep-netted measurements.

Candidate (1), the prep artifact, is complementary and independent: it removes the proxy for the
`CardRangePopcount` branch by reordering work already done, at no storage cost. Candidate (3) is parked
unless two-sided conjunctions are ever routed to the range index.

**Fallback if 107 KB is somehow unaffordable:** a trapezoid histogram — bucket widths doubling in from
each edge, capped, uniform between — over prefix/suffix arrays reaches 1.19x worst case at 3.4 KB, or
1.06x at 42 KB. Dominated by (2) on every axis except size, and for `date` not even that, since 1,048
cuts exceeds the 915 possible answers.

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

**Target:** every `unique=card` cell within 1% of true. Baseline today, 0 of 8 cells passing:

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

**Out of scope:** `eur` and `tix`, which have no range index and never reach this acquire — a separate
and much larger defect, see [local-engine-eur-tix-range-index.md](local-engine-eur-tix-range-index.md).

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
