# Compose's two walk branches, graded separately

Branched off `engine-loop-phase-measurement` at `c11ad25`, which shipped the Gather arm's missing build
term. That work left `Perm` and `OrderbyWalk` unaddressed and named them one problem. Measured, they
are three, and only one of them is what it looked like.

Context and the corpus-axis method:
[local-engine-sparse-compose-gather.md](./local-engine-sparse-compose-gather.md).

## The walk feature was never graded, and a counter for it already existed

`printings_walked` (`page_span / match_rate * WALK_LENGTH_BIAS`) is what both branches multiply by
`COMPOSE_WALK_STEP_NS`. `ComposePageWork` documents `cards_visited` as "permutation entries consumed for
the forward walk" and `printings_examined` as the printings bit-tested, so the counter was there the
whole time. Graded over 190 Perm cells:

| feature / counter | p10 | p50 | p90 |
| --- | --: | --: | --: |
| `printings_walked` / `cards_visited` | 2.65 | 4.53 | 39.49 |
| **`printings_walked` / `printings_examined`** | 0.17 | **1.17** | 1.75 |

It tracks `printings_examined`, which the name already said. So the feature is not badly biased — the
walk steps cards and examines each card's whole span, and ~4.5 printings per card is the corpus mean.

## Split by branch and by sort column, across a 10x corpus axis

`charged / printings_examined`, median, where `charged` is what the arm multiplies
(`max(printings_walked, orderby_walk_scan)`):

| corpus | Perm | OW/usd | OW/rarity |
| --- | --: | --: | --: |
| 0.5x | 1.25 | 0.42 | **0.67** |
| 1.0x | 1.12 | 0.88 | **0.67** |
| 2.0x | 1.22 | 0.44 | **0.67** |
| 3.0x | 1.26 | 0.30 | **0.67** |
| 4.0x | 1.36 | 0.23 | **0.67** |
| 5.0x | 1.37 | 0.18 | **0.67** |

Three different diagnoses, and the branch name hides two of them: `OrderbyWalk` charges
`orderby_walk_scan` (= `n_printings`) for a rarity orderby and `printings_walked` for usd, so it is two
features under one label and has to be graded as two.

### Perm: the feature is fine; the arm's drift is a cache effect that saturates

The feature is nearly flat (1.12-1.37 over a 10x range) while the ARM drifts 1.75x
(pred/meas 1.259 -> 0.721). So the feature is not the cause. Decomposing: predicted grows exactly 10x
across the axis while measured grows **14.6x** (21 us -> 306 us). The extra is superlinear cost in the
executor, not a missing term — the working set outgrows a cache level — and it is why adding the build
term shifted the curve without flattening it (drift 1.75x -> 1.76x).

**So Perm does not need fixing for this corpus.** It reads 1.185 at production scale, and the drift is
only visible at 3-5x. Recording it because the next person to see 0.72 at 5x will otherwise go looking
for a missing feature that is not there. If the corpus ever grows that far, the answer is a
cache-aware rate, not a new term.

### OW/rarity: the charge has the wrong SHAPE, and the right one is discrete

Exactly 0.67 at every corpus size is not a coincidence and not a constant to fit. `walk_rarity_orderby_page`
walks rarity buckets in sort order, and the interior rarities are **planes** — each plane bucket ANDs
`wpp` words of the whole corpus however few matches survive, so the walk costs
`(buckets consumed) x n_printings`. `orderby_walk_scan` charges exactly one.

Swept over pages and both directions (160 cells) the quantity is **discrete**:

    corpus widths consumed:  0 -> 31 cells,  1 -> 83,  2 -> 10,  3 -> 22,  4 -> 14

p50 is 1.00, so today's charge is right at the median and wrong in the tail — 27% of cells consume 3-4
widths and are undercharged 3-4x, and 19% consume none and are overcharged. (An earlier read of "1.5x,
fit a constant" came from sampling only `limit=60, offset=0`; sweeping the page is what exposed the
distribution. A median is not a shape.)

**A model was the obvious fix. It was tried and it does not work.** The walk consumes buckets in sort
order until it has `page_offset + limit` rows, so the fraction of buckets touched should track the
fraction of the result set skipped: `ceil(n_planes * page_span / total)`, needing no popcount and only
quantities the acquire already holds. Scored against realized widths over 168 cells:

| | mean abs err | p50 | p90 | exact |
| --- | --: | --: | --: | --: |
| **shipped (flat 1)** | **0.80 widths** | 1.00 | **2.00** | 83/168 |
| `ceil(4 * span/total)` | 0.86 | **0.00** | 3.00 | 88/168 |

Better at the median, worse in the tail, worse on the mean. The even-spread assumption is what fails:
rarity is heavily skewed, so a page at offset 0 **ascending** lands entirely inside the common bucket
and needs one width, while **descending** starts at mythic — tiny — and needs several before the page
fills. Direction, not page depth, is doing most of the work, and `page_span / total` cannot see it.

So the flat charge of 1 is a defensible estimator (exact on 83/168, mean error 0.80 widths) and this is
**not the easy win it looked like**. Predicting it properly needs the per-rarity match counts, which
cost a popcount per plane — the walk's own price — so the honest options are to leave it, or to make
the executor report a realized bucket count and revisit with a direction-aware model.

### OW/usd: diagnosed — the feature is corpus-invariant and the work is not

0.88 at 1x degrading monotonically to 0.18 at 5x, and it is exactly a `1/N` law:

| corpus | measured | `0.88 / N` | ratio |
| --- | --: | --: | --: |
| 1x | 0.88 | 0.88 | 1.00 |
| 2x | 0.44 | 0.44 | 1.00 |
| 3x | 0.30 | 0.29 | 1.02 |
| 4x | 0.23 | 0.22 | 1.05 |
| 5x | 0.18 | 0.18 | 1.02 |

**The feature cannot move with the corpus and the work must.** `printings_walked` is
`page_span / match_rate * WALK_LENGTH_BIAS`; under replication `matches` and `n_printings` scale
together so `match_rate` is unchanged, and `page_span` is a page, so the feature is *constant* across
the whole axis. Meanwhile `collect_orderby_page` collects **whole value buckets** that the page window
overlaps — and replication puts N times as many printings on each distinct price — so the realized
count scales linearly. Constant over linear is `1/N`, which is what the table shows.

**A one-bucket floor was the obvious fix. Implemented, measured, and it does not bind — reverted.**
`max(printings_walked, printings per distinct value)`, the same shape `orderby_walk_scan` already gives
the rarity walk, with the bucket size from the price index and its `RangeCardCounts.values` length. The
feature grade did not move at all (0.88 / 0.44 / 0.18, unchanged), because the floor is far too small:

| corpus | `printings_walked` | floor | charged | realized `examined` |
| --- | --: | --: | --: | --: |
| 1x | 99 | 19 | 99 | **112** |
| 5x | 99 | 98 | 99 | **560** |

`examined` scales exactly 5x while `charged` cannot move, and at 5x one bucket is 98.6 printings, so
`ceil(99 / 98.6) = 1` bucket predicts ~99 and not 560. **The walk consumes the same ~5.7 buckets at both
sizes** — the bucket COUNT is corpus-invariant and the bucket SIZE scales, which is the 1/N law, but it
means the count is not `printings_walked / bucket_size` and the `max` shape cannot express it.

### Resolved: two buckets, and the run length where the WALK STARTS

`walk_range_orderby_page` forms one bucket per distinct price and reports `raw = be - bs`, the value run
length. `collect_orderby_page` then loops `while cum < want` with `cum` in matches, so it can overshoot
by one whole bucket — and `matches_pushed` says it does, massively: **109 pushed for a 60-row page at 1x,
545 at 5x.**

Reconstructing from that, the walk consumes **two** buckets at both sizes. The first (~8 printings at 1x,
40 at 5x) yields fewer than the 60 needed; the second overshoots hugely (~97 at 1x, ~485 at 5x). The
bucket COUNT stays 2 while the bucket SIZES scale, which is exactly why `scanned` scales and
`printings_walked` cannot.

**And it is why both floors failed.** The corpus-wide average run is 19.7 entries, but the walk starts at
the cheap end where runs are far denser than average — the statistic has to be the run length *where the
walk starts*, not a corpus-wide mean.

The model that follows needs no constant: walk `printings_walked` entries from the start of the value
order, then extend to the end of the run you land in. One `partition_point`. Scored against realized
`scanned`:

| query | 1x | 5x |
| --- | --: | --: |
| `border:black` | **1.00** | **1.00** |
| `usd>0.01` | **1.00** | **1.00** |
| `f:modern` | 4.21 | **1.00** |
| `r:common` | 4.21 | **1.00** |
| `r:mythic` | 0.04 | 0.02 |

**It fixes the SCALING exactly and inherits the CLUMPING error.** The broad filters all consume the same
two cheap buckets because their local match density near the cheap end is ~100%, while `printings_walked`
divides by the GLOBAL rate — so `f:modern` and `r:common` over-predict at 1x. `r:mythic` is the inverse
and far worse: mythics are expensive, absent from the cheap end, so the walk grinds through 32% of the
index (31,698 entries) to find 75 matches.

That is not a new defect and not one this model introduces. `WALK_LENGTH_BIAS`'s own doc already declares
it: "the spread stays wide because how matches clump along a sort order is not something a density ratio
can see, and no constant will fix that." The run-boundary model is a strict improvement in SHAPE — the
current feature cannot scale with the corpus at all, and this one does — but **OW/usd will not become
low-error until clumping is addressed**, and that is a bigger problem than this branch.

What clumping would need: the match density in the *first* part of the sort order, not the whole. For a
price orderby that is answerable — the composed bitmap intersected with the first N index entries — but
it costs a scan of those entries, which is the walk's own price. The cheaper route is a per-filter
correlation hint, and nothing in `PlanFeatures` carries one today.

One case worth carrying into that: `r:mythic` at `orderby=usd` charges 947 against a realized 31,698 at
1x and 129,075 at 5x — a 33x undercharge, far worse than the broad queries, and a sparse-match regime
(8,924 matches) where many buckets are needed to fill a page. Whatever explains the bucket count has to
explain that cell too.

## Where this stands

Nothing shipped on this branch yet. Order to take it in:

1. **The run-boundary model is implemented and held on an unresolved total.** Tracked at
   [patches/local-engine-compose-walk-usd-run-boundary.patch](./patches/local-engine-compose-walk-usd-run-boundary.patch)
   — `range_walk_run_boundary` plus a `SortCol::PriceUsd` arm in `orderby_walk_scan`. Applies to
   `card_engine/src/lib.rs` at `cf44da2` with `git apply`, and passes 149 debug / 148 release. Held
   rather than applied: it is one measurement short, and leaving it in the tree would mean shipping a
   5% total regression on "probably noise". What it does:

   | | before | after |
   | --- | --: | --: |
   | OW/usd feature, 1x / 2x / 5x | 0.88 / 0.44 / 0.18 | **0.92 / 0.53 / 0.59** |
   | drift across the range | 4.9x | **1.56x** |
   | OrderbyWalk miss% | 12% | **11%** |
   | OrderbyWalk mean | 2.55 µs | **2.40** |
   | OrderbyWalk p90 | 2.75 | **2.25** |
   | **total regret** | **1.32 µs** | **1.39** |

   The targeted branch improves on every metric and the 1/N collapse is gone. The total moved +0.07 µs,
   which OrderbyWalk cannot explain — it is 4% of rows and its own delta is -0.11 ms against a +3.4 ms
   total. So either the total is noise or there is a second-order path I did not find, and a repeat on a
   different seed settled nothing because the seed changes the query mix (1.51 with the change on seed 7,
   with no seed-7 baseline to compare against).

   **Resolving that is the next step, and it is cheap:** run both arms on three paired seeds. If the
   total is noise, ship — the mechanism is validated and the targeted branch is better. If it is real,
   the cause is worth finding before the fix lands, because nothing in the change should reach a
   non-`OrderbyWalk` row.

2. **OW/rarity: one rate serving two operations, plus a 124x feature over-charge.** Tested and
   confirmed. `walk_rarity_orderby_page` produces two physically different buckets and reports both in
   the same unit — a PLANE bucket (common/uncommon/rare/mythic) ANDs `words_per_plane` words of the whole
   corpus and reports `wpp * 64` printings covered; a POSTINGS bucket (special/bonus) walks its own id
   list and reports its length. Both are charged `COMPOSE_WALK_STEP_NS`. Split by which kind a query
   consumes:

   | group | n | charged/examined | realized ns per reported printing |
   | --- | --: | --: | --: |
   | plane-only | 56 | 0.50 | **1.069** |
   | postings-only | 8 | **124.43** | **3.792** |
   | mixed | 32 | 0.33 | 1.413 |

   The two operations differ **3.5x** in realized rate and the shipped 0.58 is under both. That is the
   rate half, and a constant is the right instrument because the error is flat across the corpus axis.

   The bigger half is the feature: `orderby_walk_scan = n_printings` charges a whole corpus pass even
   for `r:special`/`r:bonus`, whose walk only touches a short postings list — **124x over**. Fixable
   without a popcount, because it is a filter-SHAPE question: a rarity equality on a postings int
   (`special`=4/`bonus`=5) can never consume a plane bucket, and the acquire can see that from the
   filter. That the aggregate read a dead-flat 0.67 was these two errors, in opposite directions,
   mixing — which is why splitting the population was the necessary step and a median was not.

3. **Clumping is NOT the ceiling — see
   [local-engine-compose-paging-cost-based.md](./local-engine-compose-paging-cost-based.md).** An earlier
   revision of this doc said it was. The reframing: `compose_paging_with_total` picks `OrderbyWalk` by
   SHAPE for printing mode on usd/rarity, so compose has no argmin between it and `Gather` even though
   `plan_cost` has both arms. A router does not need to know how bad a clumped walk is, only that it is
   bad — and `Gather` is O(matches), so it gets safer exactly as the walk gets worse. That is a far
   weaker requirement than the density-along-the-sort-order feature the paragraph below asks for.

   The full rarity-walk analysis, including why postings for mythic would be ~6x slower rather than
   faster, is in [local-engine-rarity-walk-cost.md](./local-engine-rarity-walk-cost.md).

4. **Clumping remains the ceiling on PRICING the walk**, and it is one problem rather than two: `Perm`'s
   `printings_walked` divides by the same global match rate and carries the same 10x spread. Neither
   branch gets low-error without it. This is the item to open next if the goal is a well-behaved arm
   rather than a well-behaved constant.
2. **OW/rarity.** Attempted and declined — see above. Revisit only with a direction-aware model, and
   only if the tail (27% of cells undercharged 3-4x) shows up in regret. It has not yet.
3. **Perm.** Leave it. 1.19 at production scale, and the drift is cache, not model.

Each needs its own regret gate. This branch has twice shown that a partial accuracy fix on the compose
arm loses regret — the `compose_scan_printings` span patch (1.33 -> 1.41 us) and the build term applied
build-wide (Perm 1.193 -> 1.544 at production scale).
