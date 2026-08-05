# One value-major layout for every orderby walk: key -> tiebreak-ordered printings

**Shipped** for usd, collector number, released_at and rarity. `PrintingRangeIndex = Vec<(value, pid)>`
became a value-major `PrintingValueIndex`, and rarity — which had no such index — got a fourth of the
same type.

    keys:   Vec<u32>       // each DISTINCT key once, ascending
    starts: Vec<u32>       // keys.len() + 1; key i owns pids[starts[i]..starts[i+1]]
    pids:   Vec<u32>       // key-major; within a key, page_cmp TIEBREAK order

Two structures backed the two `orderby` walks and both had the same defect: within a key, printings
were in **pid** order rather than sort-key order, so `collect_orderby_page` had to take each bucket
whole and quickselect it. `walk_range_orderby_page`, `walk_rarity_orderby_page` and
`collect_orderby_page` are now one `walk_value_orderby_page`, and it emits rows directly.

## Why the tiebreak works, which is the whole idea

`page_cmp` orders on `(primary, edhrec_rank, cid, pid)`. Every printing in a key run shares the
primary, so a run stored in the rest of that order IS page order — and the order is
**direction-independent**, because `sort_key_bits` negates only the primary and `page_cmp` drops key 3
(`prefer_score`) deliberately. Descending reads `keys` backwards and each run forwards. No mirror
index, no reversal at query time.

`cid` then falls out for free: printings are stored card-major, so ascending `pid` already IS ascending
`(cid, pid)`. The build's sort key is two fields, `(edhrec_rank, pid)`, with a missing rank last.

## Measured

Production corpus, printing mode, `limit=60 offset=0`. Whole-request time; the walk's own share is in
the second table.

| orderby | query | before | after |
| --- | --- | --: | --: |
| rarity | `border:black` | 142.7 µs | **25.1 µs** |
| rarity | `usd>0.01` | 181.1 µs | **47.5 µs** |
| rarity | `f:modern` | 168.4 µs | **63.2 µs** |
| rarity | `r:mythic` | 72.5 µs | 52.8 µs |
| usd | `r:mythic` | 55.5 µs | 40.2 µs |
| usd | `border:black` | 30.2 µs | 28.5 µs |

The walk itself, on `border:black` ordered by rarity ascending — the case the design doc opened on:

| | before | after |
| --- | --: | --: |
| matches pushed to serve 60 rows | 24,653 | **60** |
| index entries examined | 97,216 | **60** |
| the compose participant's own time | 115.5 µs | **0.4 µs** |

What remains of the 25 µs is the compose build and the request round trip, not the walk.

**The feature now scales, which was the point.** `printings_walked` graded against realized
`printings_examined`, six corpus sizes built by replication (fractional sizes sampled by oracle card,
never by printing):

| orderby | query | 0.5x | 1x | 2x | 3x | 4x | 5x |
| --- | --- | --: | --: | --: | --: | --: | --: |
| rarity | `border:black` | 1.65 | 1.65 | 1.65 | 1.65 | 1.65 | 1.65 |
| rarity | `usd>0.01` | 1.56 | 1.66 | 1.63 | 1.61 | 1.58 | 1.56 |
| rarity | `f:modern` | 1.98 | 1.98 | 1.92 | 1.86 | 1.81 | 1.76 |
| usd | `border:black` | 1.60 | 1.60 | 1.60 | 1.57 | 1.55 | 1.52 |
| usd | `f:modern` | 2.02 | 1.95 | 2.05 | 2.05 | 2.05 | 2.05 |
| usd | `r:mythic` | 0.05 | 0.03 | 0.02 | 0.01 | 0.01 | 0.01 |

Flat, where it used to be exactly `1/N` — realized `examined` is 60-70 entries at **every** size,
because the walk stops when the page fills. The residual is a constant ~1.6x over-charge and it
decomposes cleanly: `WALK_LENGTH_BIAS = 1.45` was fitted against the overshoot this change deletes, and
1.65 / 1.45 = 1.14. Not worth splitting — the term is ~130 ns against a build section of ~2 µs.

`r:mythic` ordered by usd is the clumped control and drifts as expected; see below.

## What it bought beyond the walk

**A cost-model feature stopped existing.** `orderby_walk_scan` floored the rarity walk at `n_printings`
because a rarity bucket was a one-hot PLANE — ANDing one covered the corpus however few matches
survived. There is no bucket granularity left to express, and the floor measured **146x over**
(58.3 µs charged against 0.4 µs realized on `border:black`/rarity). Deleted. Paired regret A/B, 120 s
uniform traffic at seed 7, on the compose-paging slice:

| branch | n | miss% | mean | p90 | → | n | miss% | mean | p90 |
| --- | --: | --: | --: | --: | --- | --: | --: | --: | --: |
| **OrderbyWalk** | 572 | 11% | **2.24 µs** | 1.87 | | 570 | 8% | **1.06 µs** | 0.17 |
| Gather | 20,594 | 5% | 1.38 | 0.00 | | 20,564 | 5% | 1.36 | 0.00 |
| Perm | 2,394 | 5% | 1.56 | 0.00 | | 2,388 | 5% | 1.49 | 0.00 |
| Decline | 3,063 | 6% | 1.43 | 0.00 | | 3,053 | 6% | 1.47 | 0.00 |

**The range FILTER paths got faster too**, and they were the risk rather than the reward — every range
consumer was rewritten. None regressed, and `usd<50` under `unique=card` went 128.6 → 103.0 µs, because
`build_card_range_bits` walks a contiguous `u32` array instead of striding an 8-byte pair. Half the
bytes touched for the same set.

**The archive is 649 KB SMALLER**, not the wash this doc predicted before the work: 72,138,360 →
71,489,272 bytes. The three range indexes stop repeating the value once per printing, which more than
pays for the ~389 KB rarity index. The prediction was wrong because it counted the range saving at 45%
of the value column only, not of the whole pair.

That 649 KB then got spent, deliberately: `price_eur`/`price_tix` gained indexes of this same type
([done/local-engine-eur-tix-range-index.md](./local-engine-eur-tix-range-index.md)) for +653 KB, which
came in 41% under that doc's own estimate precisely because the layout is value-major. Net for the whole
branch is **+20 KB over main having added three indexes**, which is the number to quote — not either
half on its own.

`ARCHIVE_FORMAT_VERSION` → 20260805; one bump covered all three layout changes in this window.

## What it did NOT buy

**Clumping survives untouched**, exactly as scoped. The overshoot was *within* a run; clumping is
*across* runs. `r:mythic` ordered by usd still examines 30,646 entries for 75 matches, because mythics
are expensive and an ascending price walk starts at pennies — every one of those entries is a genuine
bit-test miss and no within-run ordering removes them. It is charged 2.5 µs against 11.0 realized, and
it is [a paging-branch choice](../local-engine-compose-paging-cost-based.md), not a rate.

**The rarity planes stay.** This replaced the walk's use of them, not the FILTER path's:
`rarity_cmp_leaf_bits` wants a whole-bucket bitmap and a plane is the right shape for that (1,519 word
ANDs against a rarity's whole printing count). The plane/postings crossover is correct *for filtering*.
So rarity is dual-storage on purpose, and the doc that analysed the walk's rarity-specific cost errors
([local-engine-rarity-walk-cost.md](./local-engine-rarity-walk-cost.md)) is resolved by this change
rather than by either of the fixes it proposed — there are no plane steps left to rate-split, and the
124x `special`/`bonus` over-charge went with the deleted field.

## Row identity was the gate, and it is not vacuous

`force_plan_differential_agreement` asserts full row order against `GatheredScan` across every plan.
Inverting the build's tiebreak to plain pid order fails it, plus
`fuzz_row_identity_matches_reference`, `orderby_walk_matches_gather_composed` and
`printing_range_aligned_page_matches_naive_incl_tie_buckets` — checked by making that edit
deliberately. 149 debug / 148 release.

## Next, in the order the evidence puts them

1. **The compose BUILD section — measured, and it is a dead end on its own.** The suspicion recorded
   here first ("~4x over, not measured") was right about the rate and wrong about it being actionable:
   `COMPOSE_POPCOUNT_PER_WORD_NS` is 4.3x over, and correcting it alone loses regret AND wall time
   because the other compose rates were fitted with the error present.
   [local-engine-compose-build-rates.md](../local-engine-compose-build-rates.md) carries the
   measurement, the revert, and a second finding: `Perm` is missing a per-CARD cost feature.
2. **[Cost-based `OrderbyWalk` vs `Gather`](../local-engine-compose-paging-cost-based.md)** — the
   clumping case, and the only remaining item this doc's work does not touch.
3. **`Perm`.** Leave it. 1.19 at production scale; its drift is cache superlinearity that saturates,
   not a missing term.
