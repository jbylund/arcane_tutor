# `eur` and `tix` have no range index, and pay 170–550x for it

`usd>150` routes in **1.3 µs**. `eur>150`, the same shape at the same selectivity, takes **710.8 µs**.
`tix>10` takes **782.2 µs**. The difference is not the cost model — it is that only `price_usd` has a
`PrintingRangeIndex`, so `eur` and `tix` predicates never narrow and fall through to a scan.

Found while scoping the acceptance test for
[local-engine-range-cardinality-estimate.md](local-engine-range-cardinality-estimate.md): those two
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

Three fields have a `PrintingRangeIndex`:

```rust
released_at:      PrintingRangeIndex,   // printing space
price_usd:        PrintingRangeIndex,   // printing space (integer cents)
collector_number: PrintingRangeIndex,   // printing space (extracted int)
```

And `resolve_numeric_range_leaf` maps only two numeric fields onto them — `PriceUsd` and
`CollectorNumberInt`. `PriceEur` and `PriceTix` hit the `_ => None` arm, so `bare_range_bounds`
returns `None`, no range acquire applies, and the query lands on the general candidates path where
`matches`/`eval_domain` are the unnarrowed card count.

## Fix

Add `price_eur` and `price_tix` as `PrintingRangeIndex`es and map them in
`resolve_numeric_range_leaf`, alongside `price_usd`. Both columns already exist on the printing row
(`price_eur`, `price_tix` in the corpus), and `price_usd` already demonstrates the integer-cents
handling those two need — `snap_to_nearest_cent(v * PRICE_CENTS_PER_DOLLAR)`.

**Cost:** roughly 1.1 MB of archive. The index is (value, printing id) pairs over printings that
carry the column — `eur` has 81,523 and `tix` 54,896, so ~652 KB and ~439 KB at 8 bytes each. That
is real against a 75 MB store but small against the 546x it buys.

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
