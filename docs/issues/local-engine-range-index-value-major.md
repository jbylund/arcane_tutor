# Store range indexes value-major, with each value's printings pre-sorted by tiebreak

`PrintingRangeIndex` is `Vec<(u32 value, u32 pid)>` sorted by value ([lib.rs:2271](../../card_engine/src/lib.rs#L2271)).
Within one value the pids are in **pid** order, not sort-key order. That single fact is what forces the
`usd` orderby walk to consume whole value runs, and it is the root of the largest remaining feature
error on the compose arm.

Proposed: a value array plus a pid array, each value's slice pre-sorted by the full tiebreak.

    values:  Vec<(u32 value, u32 offset)>     // ascending, one entry per DISTINCT value
    pids:    Vec<u32>                          // value-major; within a value, tiebreak order

Diagnosis this comes out of:
[local-engine-compose-walk-features.md](./local-engine-compose-walk-features.md).

## What it buys

**1. The overshoot disappears, and with it a whole feature.** `collect_orderby_page` loops
`while cum < want` with `cum` in matches, and `walk_range_orderby_page` hands it whole runs, so the walk
always finishes a bucket it has begun. Measured on a 60-row page it pushes **109** matches at the
production corpus and **545** at 5x. Pre-sorted, it stops the instant the page fills.

That removes the reason `orderby_walk_scan` exists for `usd` at all. The run-boundary model
([patch](./patches/local-engine-compose-walk-usd-run-boundary.patch)) is an accurate description of an
overshoot that should not happen — this deletes the overshoot and the model with it, which is the better
outcome. `printings_walked` alone becomes the whole story for this branch.

**2. One structure reads in both directions.** `sort_key_bits` negates only the PRIMARY under `desc`;
keys 2+ (`edhrec_rank`, `prefer_score`) are not negated. So within a value the tiebreak order is the
same in both directions — read the value array backwards and each value's pid slice forwards. No mirror
index, no reversal at query time.

**3. It is smaller.** Today: 8 bytes per printing, because the value is repeated per pid — ~652 KB for
`price_usd` (81,542 priced printings). Proposed: 8 bytes per distinct value + 4 per printing =
33 KB + 326 KB = **~359 KB**, a 45% reduction, and the same argument applies to `collector_number` and
`released_at`.

## What it does NOT buy, and this is the part to be clear about

**Clumping survives untouched.** The overshoot is *within* a bucket; clumping is *across* buckets.
`r:mythic` ordered by `usd` scans 31,698 entries to find 75 matches because mythics are expensive and an
ascending walk starts at pennies — every one of those entries is a genuine bit-test miss against `pbits`,
and no within-bucket ordering removes them. Expect this change to fix `border:black`, `f:modern`,
`r:common` and `usd>0.01` cleanly and leave `r:mythic` exactly where it is.

**It does nothing for the rarity walk**, which has no range index — its buckets are planes and postings.
That branch's two defects are separate and recorded in the walk-features doc.

## Blast radius

`PrintingRangeIndex` appears 22 times in `lib.rs`, with ~26 sites doing direct `.0`/`.1` tuple access.
The ones that need real thought rather than mechanical rewriting:

- **`range_narrowed` / `range_candidates`** — collect `idx[s..e].map(p.1)` then `sort_unstable()`. With a
  value-major layout the slice is `pids[off_s..off_e]`, still contiguous, and the sort is still needed
  (tiebreak order is not pid order). Cheaper if anything: no stride over a tuple.
- **`partition_point` on values** — every bounds lookup becomes a search over `values` (4,133 entries for
  usd) instead of over 81,542 pairs. Strictly faster, and `bare_range_bounds`' contract is unchanged.
- **`aligned_page`** — uses the index directly as the value-sorted permutation for a `usd` sort. This is
  the one that gets *better* rather than just different: today it relies on the run being in pid order
  and re-sorts; pre-sorted slices give it the page directly.
- **`walk_range_orderby_page`** — the point of the change. Bucket formation stops scanning for run
  boundaries (`while b > lo && idx[b-1].0 == v`) and becomes an offset subtraction.
- **`range_leaf_bits` / `CardRangePopcount`'s build** — scatter `pids[off_s..off_e]`, unchanged in shape.
- **Build + serialization** — one extra sort per index, by the tiebreak rather than by pid.

**`ARCHIVE_FORMAT_VERSION` must be bumped to the actual current date**, not incremented: this is a
layout change and a stale archive must fail the header check rather than be read as garbage.

## Order to do it in

1. Change the type and the build, bump `ARCHIVE_FORMAT_VERSION`, and get the suite green with every
   consumer mechanically ported. No behaviour change intended at this step — `force_plan_differential_agreement`
   asserts full row order across every plan and is the gate.
2. Then change `walk_range_orderby_page` / `collect_orderby_page` to stop mid-bucket, which is where the
   win is. Row identity again, because this alters which rows a page contains at a tie boundary if the
   tiebreak sort is wrong.
3. Then delete `range_walk_run_boundary` and the `SortCol::PriceUsd` arm of `orderby_walk_scan` if the
   patch has landed by then, and re-grade `OW/usd` against `printings_examined` — it should read ~1.0 at
   every corpus size for the non-clumped queries.
4. Re-run the corpus-size sweep. The whole point is that the feature now scales; three sizes confirm it.

Gate each step on the compose-paging slice `bench_regret_matrix` reports, not the total — `OrderbyWalk`
is 4% of rows, so a real improvement there is invisible in an aggregate.
