# Interior-Interval Distinct Counts, for `year:Y` and Fused Two-Sided Ranges

Status: open, nothing implemented. Filed as
[#853](https://github.com/jbylund/sylvan_librarian/issues/853). The **two-sided / interior** half of
[the range-cardinality work](done/local-engine-range-cardinality-estimate.md), whose one-sided half shipped
as `RangeCardCounts`. That doc holds the nine rejected estimators, the four measurement traps, and the
sizing for every candidate; this one holds the problem it deferred and the reason it is no longer deferrable.

## The structural limit, which is proven rather than assumed

`RangeCardCounts` stores, per distinct value: `below[i]` (distinct cards among printings with value <
`values[i]`), `at_or_above[i]`, and `at[i]`. Those answer an interval that runs to an **edge** of the index.

They cannot answer an interior interval, and the parent doc measured why rather than assuming it:

- `suf[i] != total - pre[i]` — a card with printings on both sides of the cut is in both.
- `val[i] != pre[i+1] - pre[i]` — that counts cards whose *first* printing is at this value, not cards
  present at it (10 against a true 54 at `usd:2.99`).

**Distinct counts do not subtract.** So an interior interval is not a lookup away; it is a different data
structure. `ArchivedRangeCardCounts::lookup` (`card_engine/src/lib.rs`) encodes exactly the boundary:

```rust
match (lo <= first, last_covers_end) {
    (true, true)  => Some(at_or_above[0]),          // whole index
    (true, false) => Some(below[j]),                // `<` / `<=`
    (false, true) => Some(at_or_above[i]),          // `>` / `>=`
    (false, false) if j == i + 1 => Some(at[i]),    // `Eq` — exactly one distinct value
    _ => None,                                       // interior, several values
}
```

## Two shapes land in that `None` arm, by different routes

### 1. `year:Y` — one leaf, interior interval

`year_range_bounds` turns `year:2015` into `[2015_0000, 2016_0000)`, a whole calendar year of release dates.
`bare_range_bounds` accepts it (a single `YearCmp`), so it *does* reach `distinct_cards` — and hits
`(false, false)` with `j > i + 1`, returning `None`. The `CardRangePopcount` acquire then falls back:

```rust
let card_est = range_card_counts_for(indexes, idx)
    .and_then(|counts| counts.distinct_cards(lo, hi))
    .unwrap_or_else(|| k.min(n_cards));
```

`k.min(n_cards)` is the proxy the parent doc measured at **median 1.49×, p10 1.14, p90 1.82, p99 4.33** —
over-estimating nearly everywhere, because printings outnumber cards.

The code comment already names this as the sole remaining fallback. It is correct about `year:Y` being the
only *single-leaf* case; it predates the fusion below.

### 2. Two-sided fused ranges — the part that changed

`bare_range_bounds` matches `NumericCmp` / `DateCmp` / `YearCmp` and `Not` of those — **not `And`**. So
`usd>=0.42 usd<=0.43`, still an `And` of two leaves, is declined by `exact_result_total` in *every* mode,
including printing. It does not reach `distinct_cards` at all.

Meanwhile `compose_printing_estimate`'s `And` arm fuses the children and takes
`AndSource::FusedRange { k } => ComposeEstimate::leaf(k, 0, k)` — **exact in printing space** — then the
card and artwork totals come from projecting that through balls-into-bins.

So the two shapes share a root cause (no interior-interval distinct count) but fail at different layers:
`year:Y` asks the table and is refused; two-sided never asks.

### Where each shape actually stands

| shape | printing | card | artwork | path |
| --- | --- | --- | --- | --- |
| `usd>=200`, `cn<200`, `date>X` (one-sided) | exact `e - s` | **exact** | **exact** | `exact_result_total` → `distinct_cards`, edge arms |
| `usd:5`, `date:2023-01-01` (`Eq`) | exact | **exact** | **exact** | same, `j == i + 1` arm |
| `year:Y` | exact | **1.49× median proxy** | proxy | `distinct_cards` → `None` |
| `usd>=a usd<=b` (fused two-sided) | **declined, though `k` is free** | estimated | estimated | `exact_result_total` → `None`; compose fuses then projects |

## Why this is newly in scope

The parent doc parked candidate 3 — a wavelet tree over a `prev` array, exact for arbitrary ranges — under
an explicit condition:

> parked unless two-sided conjunctions are ever routed to the range index

and its "Which shapes actually reach this acquire" section argued:

> So a two-sided conjunction like `cn>=441 cn<=447` never reaches this acquire; it goes through the general
> candidates route, where the estimate is not `card_est` at all. **It was treated as the motivating bounded
> case for several turns and is not one.**

That scoping is what justified the conclusion *"candidate (2) plus two small arrays is exact for every shape
that reaches this acquire, with no wavelet, no banded table, no MCV and no dependency."*

**[#837](https://github.com/jbylund/sylvan_librarian/pull/837) fused same-index `And` children into one
range-index interval**, in `narrow_rec` and in the compose builders. The premise no longer holds, so the
conclusion it supported has to be re-decided rather than inherited. The parking condition fired.

## A contradiction to resolve before trusting either figure

[#837's write-up](done/local-engine-two-sided-range-fusion.md) says two things that cannot both be right:

- *"`range_card_counts_for` pairs each range index with an exact `RangeCardCounts` table over the same
  interval, which is why `usd>=0.42`/card reads 12,408 against a true 12,408."* — one-sided card is exact.
- *"card and artwork 0.6–0.9× — the same ratios their one-sided forms already carry."*

Both describe different paths: `exact_result_total` answers one-sided card exactly, while compose's internal
projection reads 0.6–0.9× for one-sided and two-sided alike. The misleading clause is **"the same ratios"**,
which implies two-sided loses nothing one-sided does not — when one-sided has an exact path above the
estimator and two-sided has none. Re-measure both before quoting either.

## Three pieces, cheapest first

1. **Give `exact_result_total` a fused-range arm for printing space.** `k` is two `partition_point` calls
   away and `compose_printing_estimate` already computes exactly it. Today a two-sided range has no exact
   result total in the one space where it costs nothing, and `result_total` feeds paging decisions —
   including the `STREAM_MIN_MATCHES` sparse floor, where being on the wrong side is what
   [#848](00848-engine-decline-sparse-exact-wasted-build.md) is about. Reuse `fuse_and_range_children`; do
   not re-derive the interval.
2. **The ~34 per-year counts on `date`.** Named in the parent doc's Plan and never shipped. Closes `year:Y`
   and nothing else: one array, no dependency, no tuning parameter. After it, the `k.min(n_cards)` proxy has
   no reachable caller and should be removed rather than left as a dead fallback.
3. **Decide the general interior-interval question.** Candidate 3 is the only exact option — 1.06 MB
   (134–252 KB per dimension), verified exact on 2,583 windows across all five dimensions, and needing ~200
   vendored lines because no maintained Rust crate exposes `count_lt(pos_range, value)`
   (`qwt` has rank/select only; `sucds` and `vers` expose the inverse `quantile`, ~17× the work).

   The alternative is to accept the projection error on two-sided card/artwork. **Measure the routing
   consequence before choosing**, because the parent doc's governing lesson applies unchanged: the proxy
   over-costs and the plan arms under-cost, the two partially cancel, and *nothing here ships alone*.
   Correcting an estimate without re-fitting the arms pushed arm error from 1.6× to ~2.4× last time.

## Reproducing

```bash
.venv/bin/python scripts/bench_range_estimate_scan.py   # the acceptance test: per-cell, both directions
.venv/bin/python scripts/bench_card_range_estimate.py --seconds 60   # engine-side, live store
.venv/bin/python scripts/study_range_slice_cardinality.py            # offline: closed forms
.venv/bin/python scripts/study_range_slice_layouts.py                # offline: table shapes
```

All four exist and are tracked. The offline studies read `benchmarks/bitplanes/corpus.jsonl` directly (239 MB,
untracked) and cache the extraction. Verified running 2026-08-06.

Report **per cell, not pooled** — every pooled figure in this investigation hid structure that only showed up
per cell, which is the first of the parent doc's four measurement traps.

## Related

- [done/local-engine-range-cardinality-estimate.md](done/local-engine-range-cardinality-estimate.md) — the
  one-sided half that shipped, the nine rejected estimators, and the sizing for all three candidates.
- [done/local-engine-two-sided-range-fusion.md](done/local-engine-two-sided-range-fusion.md) — #837, which
  created this shape.
- [#852](00852-engine-compose-acquire-p3-p4-ranking.md) — the arm half of "nothing ships alone". Its oracle
  result says `eval_domain` carries ~75% of recoverable routing loss, so an estimate this feeds is not
  cosmetic.
- [local-engine-plan-misselection.md](local-engine-plan-misselection.md) — where the proxy was found, and the
  `StreamedSelect`/`GatheredScan`-off-`card_range_popcount` under-costing that shares this operating point.
