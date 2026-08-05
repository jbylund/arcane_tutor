# `eur` and `tix` have no range index, and pay 170–550x for it

**Shipped.** Both now carry a `PrintingValueIndex` and a `RangeCardCounts` table. The acceptance
criteria at the bottom of this doc pass as written — see [Outcome](#outcome).

`usd>150` routes in **1.3 µs**. `eur>150`, the same shape at the same selectivity, takes **710.8 µs**.
`tix>10` takes **782.2 µs**. The difference is not the cost model — it is that only `price_usd` has a
`PrintingRangeIndex`, so `eur` and `tix` predicates never narrow and fall through to a scan.

Found while scoping the acceptance test for
[local-engine-range-cardinality-estimate.md](../local-engine-range-cardinality-estimate.md): those two
dimensions showed estimate errors up to 106x, which turned out to be a symptom of routing elsewhere
entirely rather than a bad estimate.

## Measured

All `unique=card`, `orderby=edhrec`, `limit=100`, via `explain_analyze`'s `routed_ns`.

| query | acquire | est | true | routed | usd equivalent | ratio |
| ----- | ------- | --: | ---: | -----: | -------------: | ----: |
| `eur>150` | candidates | 31,508 | 338 | **710.8 µs** | `usd>150` 1.3 µs | **546x** |
| `tix>10` | candidates | 31,508 | 298 | **782.2 µs** | `usd>150` 1.3 µs | 602x |
| `eur>20` | candidates | 31,508 | 1,123 | 655.6 µs | `usd>20` 3.8 µs | 172x |
| `tix>1` | candidates | 31,508 | 2,961 | 720.6 µs | `usd>20` 3.8 µs | 190x |
| `eur<1` | candidates | 31,508 | 26,391 | 438.2 µs | `usd<1` 82.5 µs | 5.3x |
| `tix<0.5` | candidates | 31,508 | 26,651 | 452.0 µs | `usd<1` 82.5 µs | 5.5x |

The gap is worst where the range is selective — exactly where an index earns most — and never below
5x even for a range covering most of the corpus.

`eur>150` is also mis-selected on top of that (`StreamedSelect` where `GatheredScan` is 29.5 µs
faster), which is the unnarrowed `matches = 31,508` feeding the plan choice. That is a second-order
effect; the 546x is the finding.

## Mechanism

Three fields have a `PrintingValueIndex` (`PrintingRangeIndex` when this was written; the type went
value-major in [done/local-engine-value-major-sort-indexes.md](./local-engine-value-major-sort-indexes.md)):

```rust
released_at:      PrintingValueIndex,   // printing space
price_usd:        PrintingValueIndex,   // printing space (integer cents)
collector_number: PrintingValueIndex,   // printing space (extracted int)
```

And `resolve_numeric_range_leaf` maps only two numeric fields onto them — `PriceUsd` and
`CollectorNumberInt`. `PriceEur` and `PriceTix` hit the `_ => None` arm, so `bare_range_bounds`
returns `None`, no range acquire applies, and the query lands on the general candidates path where
`matches`/`eval_domain` are the unnarrowed card count.

## Fix

Add `price_eur` and `price_tix` as `PrintingValueIndex`es and map them in
`resolve_numeric_range_leaf`, alongside `price_usd`. Both columns already exist on the printing row
(`price_eur`, `price_tix` in the corpus), and `price_usd` already demonstrates the integer-cents
handling those two need — `snap_to_nearest_cent(v * PRICE_CENTS_PER_DOLLAR)`.

**Cost as estimated here:** roughly 1.1 MB of archive — (value, printing id) pairs over printings that
carry the column, `eur` 81,523 and `tix` 54,896 at 8 bytes each.

**Cost as measured:** **+653 KB**, 41% under that, and it includes the two `RangeCardCounts` tables
this estimate did not count. The layout went value-major in the meantime
([done/local-engine-value-major-sort-indexes.md](./local-engine-value-major-sort-indexes.md)), so
the value is stored once per DISTINCT value rather than once per printing.

**The omission was deliberate** — `eur` and `tix` were lower priority than `usd` and left out of the
range indexes on purpose. So this is a prioritisation question, not a bug: the measurements above are
what that trade actually costs, now that it can be priced.

## Acceptance

`scripts/bench_range_estimate_scan.py` currently excludes `eur` and `tix` for exactly this reason.
When they gain an index, add them back to `SCANS` and they should behave like `usd`:
`count_source: card_range_popcount` at `unique=card`, `printing_range_scan` at `unique=printing`, and
an estimate within 1% of true once the boundary table from the sibling doc lands.

The runtime check is simpler: `eur>150` and `tix>10` should drop from ~700 µs to low single-digit µs,
matching their `usd` counterparts.

## Outcome

The runtime check, measured the way the table above was (the picked plan's own fastest forced trial,
`unique=card orderby=edhrec limit=100`):

| query | before | after | | its `usd` twin | before | after |
| --- | --: | --: | --- | --- | --: | --: |
| `eur>150` | 710.8 µs | **1.6 µs** (449x) | | `usd>150` | 1.3 µs | 1.3 µs |
| `tix>10` | 782.2 µs | **1.3 µs** (605x) | | `usd>150` | 1.3 µs | 1.4 µs |
| `eur>20` | 655.6 µs | **3.2 µs** (204x) | | `usd>20` | 3.8 µs | 3.5 µs |
| `tix>1` | 720.6 µs | **5.2 µs** (138x) | | `usd>20` | 3.8 µs | 3.5 µs |
| `eur<1` | 438.2 µs | **59.1 µs** (7x) | | `usd<1` | 82.5 µs | 54.0 µs |
| `tix<0.5` | 452.0 µs | **52.2 µs** (9x) | | `usd<1` | 82.5 µs | 54.0 µs |

Each now matches its `usd` twin, which is the acceptance condition. All six take
`count_source: card_range_popcount`, and the estimate is not merely "within 1%" — it is **exact** on
every row (338/338, 298/298, 1,123/1,123, 2,961/2,961, 26,391/26,391, 26,651/26,651), because
`RangeCardCounts` answers a one-sided range exactly.

`usd<1` improving 82.5 → 54.0 µs is not from this change; it is the value-major layout making
`build_card_range_bits` walk a contiguous `u32` array instead of striding an 8-byte pair.

### What this is worth on real traffic

2,000 queries sampled by `QuerySampler` in `realistic` mode, paired per-query wall time through
`engine.query`:

| subset | n | before | after | ratio |
| --- | --: | --: | --: | --: |
| eur/tix touched | 56 | 33.2 ms | **12.8 ms** | 0.386 |
| everything else (control) | 1,944 | 174.7 ms | 173.2 ms | 0.991 |
| whole mix | 2,000 | 207.9 ms | **186.0 ms** | 0.895 |

So ~10% of all wall time in that mix, from 2.8% of the queries. The control ratio matters as much as
the win: nothing else moved.

**What does not speed up:** a price predicate ANDed with an oracle-text predicate. `o:creature
tix>=0.03` stays at 2.4 ms and `o:target eur<=21.84` at 1.3 ms — the text side dominates and no price
index touches it. The gains are on price-dominant queries, and the biggest of them (8-13x) are
two-sided ranges, which additionally get #828's two-sided fusion: one intersected interval, one scatter.

### The test gap this closed

The fuzz fixture left `price_eur`/`price_tix` at `None` on every printing and the generator never
emitted those predicates, so an index here would have shipped completely unexercised. `FuzzLeaf::Price`
now names which field it means and draws all three equally, and the fixture gives each column an
independent value and null rate — derived values would agree often enough to hide a wrong-index read.
Wiring `eur` to the `usd` index fails `fuzz_row_identity_matches_reference` and
`force_plan_differential_agreement`.

`scripts/bench_range_estimate_scan.py` still excludes `eur`/`tix` from `SCANS`; adding them back is the
one piece of this doc's acceptance section not yet done.
