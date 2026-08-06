# Interior-Interval Distinct Counts, for `year:Y` and Fused Two-Sided Ranges

Status: open, nothing implemented. Filed as
[#853](https://github.com/jbylund/sylvan_librarian/issues/853).

The **interior-interval** half of the range-cardinality work. The one-sided half shipped as
`RangeCardCounts`; [that record](done/local-engine-range-cardinality-estimate.md) holds candidate 2 as built,
the nine rejected estimators, the four measurement traps, and the superseded scoping argument. This doc holds
the live options and the constraint they ship under.

## The structural limit, which is proven rather than assumed

`RangeCardCounts` stores, per distinct value: `below[i]` (distinct cards among printings with value <
`values[i]`), `at_or_above[i]`, and `at[i]`. Those answer an interval that runs to an **edge** of the index.

They cannot answer an interior interval, and it was measured rather than assumed:

- `suf[i] != total - pre[i]` — a card with printings on both sides of the cut is in both.
- `val[i] != pre[i+1] - pre[i]` — that counts cards whose *first* printing is at this value, not cards
  present at it (10 against a true 54 at `usd:2.99`).

**Distinct counts do not subtract.** An interior interval is not a lookup away; it is a different data
structure. `ArchivedRangeCardCounts::lookup` (`card_engine/src/lib.rs`) encodes exactly that boundary:

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

`k.min(n_cards)` is the proxy measured at **median 1.49×, p10 1.14, p90 1.82, p99 4.33** — over-estimating
nearly everywhere, because printings outnumber cards.

### 2. Fused two-sided ranges — the part that changed

`bare_range_bounds` matches `NumericCmp` / `DateCmp` / `YearCmp` and `Not` of those — **not `And`**. So
`usd>=0.42 usd<=0.43` is declined by `exact_result_total` in *every* mode, including printing. It never
reaches `distinct_cards`.

`compose_printing_estimate`'s `And` arm meanwhile fuses the children and takes
`AndSource::FusedRange { k } => ComposeEstimate::leaf(k, 0, k)` — **exact in printing space** — and the card
and artwork totals then come from projecting that through balls-into-bins.

### Where each shape stands

| shape | printing | card/artwork | path |
| --- | --- | --- | --- |
| `usd>=200`, `cn<200`, `date>X` (one-sided) | exact `e - s` | **exact** | `exact_result_total` → `distinct_cards`, edge arms |
| `usd:5`, `date:2023-01-01` (`Eq`) | exact | **exact** | same, `j == i + 1` arm |
| `year:Y` | exact | **1.49× median proxy** | reaches `distinct_cards`, gets `None` |
| `usd>=a usd<=b` (fused two-sided) | **declined, though `k` is free** | estimated | never asks |

## Why candidate 3 is no longer parked

The parent doc parked it under an explicit condition — *"unless two-sided conjunctions are ever routed to the
range index"* — on the strength of its scoping argument that *"a two-sided conjunction like `cn>=441 cn<=447`
never reaches this acquire… It was treated as the motivating bounded case for several turns and is not one."*

**[#837](done/local-engine-two-sided-range-fusion.md) fused same-index `And` children into one range-index
interval**, in `narrow_rec` and in the compose builders. The premise is false and the parking condition has
fired. The claim about `bare_range_bounds` not matching `And` is still literally true — which is why
`exact_result_total` now *declines* two-sided ranges rather than estimating them — but "out of scope" no
longer follows.

## Nothing here ships alone

Carried from the parent doc unchanged, because it governs every option below.

The proxy over-costs ~1.48×; the plan arms under-cost ~1.5× at this operating point. The two errors point in
opposite directions and **partially cancel**, which is why routing mostly survives them. Correcting the
estimate without re-fitting the arms pushes the arm error from 1.6× to ~2.4×.

The arm half cannot be fixed by re-fitting rates globally: the same two plans are over-costed (0.57) off
`candidates` acquires and under-costed (2.56) off this one, and `STREAM_*` / `GATHER_*` are shared constants.
That half is [#852](00852-engine-compose-acquire-p3-p4-ranking.md), whose oracle result puts `eval_domain` at
~75% of recoverable routing loss — so this estimate is not cosmetic, and the two want sequencing.

## The live options

### 0. Give `exact_result_total` a fused-range arm for printing space — free

`k` is two `partition_point` calls away and `compose_printing_estimate` already computes exactly it. Today a
two-sided range has no exact result total in the one space where it costs nothing. `result_total` feeds the
paging decisions, including the `STREAM_MIN_MATCHES` sparse floor where landing on the wrong side is what
[#848](00848-engine-decline-sparse-exact-wasted-build.md) is about.

Reuse `fuse_and_range_children`; do not re-derive the interval. This closes nothing in card or artwork space —
it is listed first because it is free, not because it is the fix.

### 1. The ~34 per-year counts on `date` — closes `year:Y`, nothing else

Named in the parent doc's Plan and never shipped. One array, no dependency, no tuning parameter. After it the
`k.min(n_cards)` proxy has no reachable caller and should be **deleted** rather than left as a dead fallback.

### 2. Build the card bitmap in acquire — exact, no storage

Carried from the parent doc; it was always "complementary and independent" and is still unshipped.

`CardRangePopcount` already calls `build_card_range_bits` at dispatch, re-deriving the bounds to do so, and it
wins **138 of 142** sampled range queries. `StreamedSelect` / `GatheredScan` would take `bitmap_card_ids` over
their own `range_narrowed` → `into_cards`. Only `PrintingCompose` has no use for a card bitmap, and it wins
**zero**.

So promoting the build into the acquire branch and carrying it in `Prep` — the pattern `Prep::Plane` already
uses for the plane bitmap — is a reordering of work already done. Its popcount is the exact
`matches` / `eval_domain`, and it deletes a duplicate bounds derivation.

| | |
| --- | --- |
| accuracy | **exact** |
| memory | **none** — no archive data |
| query time | 0 in the 97% case (the winner builds it anyway); 47 µs median, 106 µs p90 when a plan wins that does not want it |
| risk | moves an O(k) build across the acquire/dispatch boundary; the both-fast-paths-decline case (`usd>50`) needs a test |

That 47 µs is why the storage option below still matters — it is the cost when the artifact goes unused.
Measured from `acquire.range_k`: median slice 38,245 printings at `CARD_RANGE_BUILD_PER_PRINTING_NS` = 1.22.

**This option is exact for interior intervals too**, since a bitmap popcount does not care about interval
shape — which makes it the cheapest general answer if the unused-artifact cost is acceptable.

### 3. prev-array + wavelet tree — exact for arbitrary ranges, 1.06 MB

Carried from the parent doc, where it was kept "for completeness; **probably not needed**". That judgement
rested on the scoping argument above and should be re-read as live.

The general problem is **range distinct count** (colored range counting). Let `prev[i]` be the index of the
previous printing of the same card, or −1. Then

    distinct cards in [a, b)  ==  #{ i in [a, b) : prev[i] < a }

because each card in the window has exactly one occurrence that is its first there, and only that one points
back outside it. A wavelet tree over `prev` answers that dominance count in O(log n), and the tree encodes
`prev`, so the array need not be stored. Verified exact on 2,583 windows across all five dimensions —
bounded, one-sided and random. Space is 1.06 MB, 134–252 KB per dimension.

No maintained Rust crate exposes the operation. [`qwt`](https://docs.rs/qwt/) has rank/select/access only.
[`sucds`](https://github.com/kampersanda/sucds) and [`vers`](https://github.com/Cydhra/vers) — both
Apache-2.0, both actively maintained — expose `quantile(range, k)`, the inverse, which would have to be
binary-searched at roughly 17× the work. [`wavelet-matrix`](https://github.com/sekineh/wavelet-matrix-rs) has
exactly `count_lt(pos_range, value)` and is MIT (declared in `Cargo.toml`; there is no LICENSE file, which is
why GitHub's API reports none) but was last touched in 2022. Vendoring the ~200 lines that path needs —
`prefix_rank_op`, the struct, and a rank-capable bitvector the engine can supply itself — is viable under an
MIT attribution header if it is ever wanted.

### Fallback if the storage is unaffordable

A trapezoid histogram — bucket widths doubling in from each edge, capped, uniform between — over
prefix/suffix arrays reaches 1.19× worst case at 3.4 KB, or 1.06× at 42 KB. Dominated by candidate 2 on every
axis except size, and for `date` not even that, since 1,048 cuts exceeds the 915 possible answers.

## Order

1. **Option 0**, because it is free and independent of the rest.
2. **Option 1**, because it closes a named shape with one array and lets the proxy be deleted.
3. **Then decide between options 2 and 3 on measurement**, not on elegance. Option 2 is exact with no archive
   cost but pays 47 µs when its artifact goes unused; option 3 is exact with no query cost but 1.06 MB and a
   vendored dependency. The deciding number is how often a plan wins that does not want the bitmap.
4. **Re-ask the arm question afterwards.** With exact features, part of the arms' ~1.5× under-costing may
   disappear — it may be the model charging `card_est`-shaped terms for true-card-shaped work. Re-fit only
   afterwards, and only against prep-netted measurements. That is [#852](00852-engine-compose-acquire-p3-p4-ranking.md)'s
   territory, and its oracle run says features come before rates.

## Acceptance

`scripts/bench_range_estimate_scan.py` is the test. It sweeps thresholds across each dimension in both
directions and reports `acquire.matches / true total` **per point** — scans rather than a pooled median,
because every pooled figure in this investigation hid structure that only showed up per cell. That is the
first of [the four measurement traps](done/local-engine-range-cardinality-estimate.md#measurement-traps-worth-keeping),
all of which still apply.

**Target:** every `unique=card` cell within 1% of true, and the interior shapes are the cells that are not
there yet. The one-sided baseline is in the parent doc as a historical record; those cells now answer exactly.

**`eur` and `tix` are now IN scope.** The parent doc's Acceptance section excludes them as having "no range
index and never reach this acquire" — [#838](https://github.com/jbylund/sylvan_librarian/pull/838) gave both a
`PrintingValueIndex` and both now carry a `RangeCardCounts` (`price_eur_cards`, `price_tix_cards`). The scan
should cover them, and `tix` is the interesting one: it is only 56.5% present, the highest null rate of any
range index.

**Control:** all eight cells at `unique=printing` read 1.000 across 26 scan points, because that branch sets
`matches = k` and in printing mode that *is* the result cardinality. Those rows must stay at 1.000 — and
option 0 should extend that guarantee to fused two-sided ranges, which currently have no exact total at all.

**Routing is deliberately not an acceptance criterion for the estimate alone**, per "nothing here ships
alone" above.

## Reproducing

```bash
.venv/bin/python scripts/bench_range_estimate_scan.py               # the acceptance test, per cell
.venv/bin/python scripts/bench_card_range_estimate.py --seconds 60   # engine-side, live store
.venv/bin/python scripts/study_range_slice_cardinality.py            # offline: closed forms
.venv/bin/python scripts/study_range_slice_layouts.py                # offline: table shapes
```

All four exist and are tracked. The offline studies read `benchmarks/bitplanes/corpus.jsonl` directly
(239 MB, untracked) and cache the extraction. `study_range_slice_cardinality.py` verified running 2026-08-06.

## Related

- [done/local-engine-range-cardinality-estimate.md](done/local-engine-range-cardinality-estimate.md) — the
  one-sided half as shipped, the nine rejected estimators (all measured, so the next attempt does not repeat
  them), and the measurement traps.
- [done/local-engine-two-sided-range-fusion.md](done/local-engine-two-sided-range-fusion.md) — #837, which
  created this shape and whose write-up contains a correction on exactly this point.
- [#852](00852-engine-compose-acquire-p3-p4-ranking.md) — the arm half of "nothing ships alone".
- [#848](00848-engine-decline-sparse-exact-wasted-build.md) — why an exact `result_total` near
  `STREAM_MIN_MATCHES` matters.
- [local-engine-plan-misselection.md](done/local-engine-plan-misselection.md) — where the proxy was found.
