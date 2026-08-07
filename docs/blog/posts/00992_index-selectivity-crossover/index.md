---
title: "We Indexed Card Prices and the Broadest Queries Got 4x Slower"
date: 2027-02-02
publishDate: 2027-02-02
tags: ["rust", "performance", "indexing", "benchmarking"]
summary: "A new range index made selective price queries 7x faster and broad ones 4.5x slower. We measured the exact index-vs-scan crossover — about a third of the store — and taught the engine to refuse its own index past 25% selectivity, using two constants instead of a cost model."
---

`(t:goblin or usd>50)` took 1.3 ms in our Rust search engine, and both halves of that query are extremely selective.
501 goblins, 553 cards over fifty dollars, out of 97,199 printings — this should have been a tenth of a millisecond.
Chasing it uncovered two separate problems, and fixing the second one required measuring exactly where an index stops being worth using.
The answer for our store: about a third of the data.
Past that, gathering candidate ids from the index costs more than the full scan it is supposed to avoid, so the engine now refuses its own index at 25% selectivity.

## A Decomposition and a Red Herring

The suspicious thing about 1.3 ms was that neither predicate could account for it.
Timing the pieces separately (20 warmup iterations, then a 3-second timed window, `unique=card`, `limit=100`, single worker in the dev Docker deployment on an M-series MacBook):

| query | matches | ms |
|---|---|---|
| `t:goblin` | 501 | 0.049 |
| `usd>50` | 553 | 0.753 |
| `(t:goblin or usd>50)` | 1,048 | 1.285 |
| `(usd>50 or usd>100)` | 553 | 1.215 |
| `usd>5` | 4,122 | 0.747 |
| `usd<50` | 31,220 | 0.565 |

The Or machinery was innocent: `(t:goblin or t:elf)` ran in 0.082 ms.
The tell is in the `usd` rows — 0.75 ms whether the query matches 553 printings or 4,122, and *cheaper* when it matches 31,220.
A flat cost regardless of selectivity is the signature of a full scan, and two `usd` terms costing exactly twice one term confirmed it.
But `usd` had an index — we had shipped a sorted `(f32_sort_bits(price), printing_id)` array [the evening before](https://github.com/jbylund/sylvan_librarian/pull/605), and its benchmark table showed this exact query getting 7x faster.

It turned out the benchmark container had been built 30 minutes before that PR merged.
The engine we had been surveying all afternoon predated its own price index, and every `usd` number we had collected was measuring code from before the index existed.
This is an embarrassing kind of bug to lose an hour to, but it has a moral: our timing protocol records query strings, match counts, and iteration counts, and none of those identify *which build* produced the numbers.
The fix on the fresh container was everything the PR had promised: `usd>50` dropped to 0.110 ms and the goblin query to 0.147 ms.

## The Rebuild Exposed the Opposite Problem

The same fresh container made the broadest price queries dramatically worse:

| query | matches | before the index | with the index |
|---|---|---|---|
| `usd>50` | 553 | 0.753 ms | 0.110 ms |
| `usd<50` | 31,220 | 0.565 ms | 2.570 ms |
| `(t:goblin or usd<50)` | 31,221 | 0.963 ms | 3.069 ms |

`usd<50` matches 83% of priced printings, and using the index for it was 4.5x slower than the full scan the engine used to do.
The Or variant became the slowest query we had ever measured in this engine — worse than the unindexed flavor-text scans that previously owned the slow tail.

The mechanism is in what "using the index" means here.
The index answers a range query with a binary search, which is effectively free; the cost is everything after.
The matched slice is ordered by price, so its printing ids come out in price order and must be copied out and re-sorted into id order before they can be intersected or evaluated — for `usd<50`, a gather and `sort_unstable` of 31,220 ids.
Then evaluation walks those candidates by random access, hopping around the store, where the full scan walks every printing sequentially.
Per element, we measured the candidate path at roughly twice the cost of the sequential path.
Narrowing only pays when it shrinks the workload by more than that factor, and a range covering most of the store shrinks nothing.

Database people will recognize this as the oldest planner decision there is: PostgreSQL switches from index scan to sequential scan at low selectivity for the same reason.
But our engine has no planner, no cost model, and no statistics — `narrow_candidates` either returns candidate ids or it doesn't.
So the question became: at exactly what selectivity should it stop returning them?

## Measuring the Crossover With a Deliberately Broken Query

To find the crossover we needed the same query timed both ways on the same build.
The engine gave us a back door: an Or node with any unindexable child disables narrowing for the whole node, and `tix` (paper-ticket prices) never got an index.
So `(usd<T or tix>99999)` matches the identical card set as `usd<T` — the `tix` arm matches nothing and costs one numeric compare per printing — but is forced down the scan path.
Sweeping T from 2 cents to 50 dollars:

| `usd<T` | matched printings | % of store | index path | scan path |
|---|---|---|---|---|
| 0.05 | 1,122 | 1.2% | 0.13 ms | 1.32 ms |
| 0.10 | 6,102 | 6.3% | 0.34 ms | 1.31 ms |
| 0.25 | 28,518 | 29.3% | 1.08 ms | 1.40 ms |
| 0.50 | 50,617 | 52.1% | 1.76 ms | 1.30 ms |
| 50 | 80,510 | 82.8% | 2.46 ms | 1.22 ms |

The scan path is flat — it always visits all 97,199 printings.
The index path is linear at about 30 ns per gathered candidate.
The lines cross between 29% and 52% of the store; interpolating both directions of the sweep puts it near 33k candidates, about a third.
One honesty note on the method: the forced-scan comparator carries the Or node's own overhead, so it overstates a bare scan by roughly a third.
Against a fair bare-scan baseline (`tix<1`, genuinely unindexed, 0.92 ms for a similar match count) the crossover moves down to roughly 25% of the store.

## Two Constants Instead of a Cost Model

The guard is three lines where the index gathers its slice, plus [two named constants](https://github.com/jbylund/sylvan_librarian/blob/5741103/card_engine/src/lib.rs#L980-L998):

```rust
const MAX_NARROW_FRACTION: f64 = 0.25;
const NARROW_FLOOR: usize = 1_000;

fn range_too_broad_to_narrow(matched: usize, index_len: usize) -> bool {
    matched > NARROW_FLOOR && matched as f64 > index_len as f64 * MAX_NARROW_FRACTION
}
```

The range functions already compute their slice bounds with two `partition_point` calls before materializing anything, so the check is free: if the slice is too broad, return `None`, which already meant "no index applies, fall through to the scan."
Narrowing in this engine is advisory — evaluation re-verifies every candidate — so the guard cannot change results, only speed.

25% is deliberately on the low side of the measured 25–34% crossover because the failure modes are asymmetric.
Bailing out slightly early costs at most a few tenths of a millisecond of forgone narrowing; bailing out late costs up to 2 ms of id-gathering on every broad query.
The floor handles the other end: below a thousand ids, gathering is microseconds and always wins, and without it any match in a small store — unit tests, partial imports — would trip the fraction.

With the guard, `usd<50` runs 0.58 ms and `(t:goblin or usd<50)` runs 1.00 ms, back at parity with the pre-index scan; selective queries are untouched ([PR #609](https://github.com/jbylund/sylvan_librarian/pull/609)).

## The Expected Downside Measured as a Win

There is an obvious objection: candidate sets don't just avoid scans, they feed *intersections*.
In `usd<50 ft:fire`, the broad price index used to shield the expensive predicate — flavor text has no index, so pre-guard it only evaluated the 31k price candidates instead of all 97k printings.
The guard removes that shield, and we expected to pay for it somewhere.
Measured on both builds:

| query | matches | unguarded | guarded |
|---|---|---|---|
| `ft:fire` (baseline) | 461 | 1.72 ms | 1.69 ms |
| `usd<50 ft:fire` | 453 | 4.18 ms | 2.11 ms |
| `usd<0.1 ft:fire` | 84 | 0.53 ms | 0.53 ms |
| `usd<50 o:draw` | 4,128 | 2.28 ms | 0.38 ms |

The shielded query was 2x *slower* than the unshielded one, for the same reason broad standalone ranges were: the ~1 ms gather-and-sort of 31k ids plus random-access evaluation at twice the per-element cost exceeded the value of skipping two-thirds of a sequential pass.
With the guard, `usd<50 ft:fire` runs at parity with bare `ft:fire` — the And costs nothing beyond its most expensive child.
And `usd<50 o:draw` is a 6x win nobody asked for: the broad price gather had been polluting an intersection that the oracle-text trigram index was already winning on its own.
The guard doesn't just skip useless scans-avoidance; it stops feeding 31,000-element sets into intersections where the other side has 4,000.

## The Draft That Guarded Too Much

The first version applied the guard to all five range indexes, on the theory that the same shape means the same pathology.
The benchmark disagreed: `cmc>3` regressed from 0.32 ms to 0.49 ms.
The cmc, power, and toughness indexes live in *card* space — 31,508 unique cards, roughly a third the size of printing space — and the same sweep on those told the opposite story:

| query | matched cards | index path | scan path |
|---|---|---|---|
| `cmc>3` | 13,007 | 0.31 ms | 1.27 ms |
| `cmc>1` | 27,263 | 0.66 ms | 0.92 ms |
| `cmc>=0` | 31,508 (entire index) | 0.72 ms | 0.74 ms |
| `power>0` | 16,551 | 0.40 ms | 1.21 ms |

Card-space narrowing never loses — at 100% index coverage it is a wash, and everywhere else it wins.
The difference is that a card-space candidate set is bounded by the 3x smaller card count and shrinks the evaluation domain relative to the per-printing scan, while a printing-space candidate set is drawn from the same 97k ids the scan would visit anyway.
So the shipped guard covers only the printing-space indexes (price and release date), and the card-space exemption is documented in the code with the numbers above.
"Same data structure" turned out not to mean "same cost curve" — the id space it indexes into matters more than the index's shape.

The crossover we measured is one machine, one dataset, one ratio of random-access to sequential cost; on a store 10x larger or hardware with different cache behavior the constant would deserve re-measuring.
That is also the point.
A planner earns its statistics infrastructure by answering this question for every table and every query; we had one index family and one question, and an afternoon of measurement replaced the cost model with a named constant.
The most useful query in the whole exercise was the deliberately broken one — sometimes the best benchmark harness is a query engine that can be tricked into doing things the slow way.
