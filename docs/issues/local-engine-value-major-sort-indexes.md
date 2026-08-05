# One value-major layout for every orderby walk: key -> tiebreak-ordered printings

Two structures back the two `orderby` walks, and both have the same defect: within a key, printings are
in **pid** order rather than sort-key order, so `collect_orderby_page` must take each bucket whole and
sort it.

    PrintingRangeIndex   Vec<(u32 value, u32 pid)>  sorted by value   (usd / cn / date)
    RarityPrintingPlanes one-hot plane per interior rarity + postings (rarity)

Replace both with one shape:

    keys:  Vec<(u32 key, u32 offset)>     // ascending, one entry per DISTINCT key
    pids:  Vec<u32>                        // key-major; within a key, TIEBREAK order

This started as two docs. It is one change — the rarity walk and the range walk are the same walk over
the same layout, and `walk_rarity_orderby_page` / `walk_range_orderby_page` collapse into one function.
The rarity-specific cost-model defects that survive it stay in
[local-engine-rarity-walk-cost.md](./local-engine-rarity-walk-cost.md).

## The measured cost of the current layout

Printing mode, `limit=60 offset=0`, production corpus:

| orderby | query | dir | **pushed** for 60 rows | examined | measured |
| --- | --- | --- | --: | --: | --: |
| rarity | `border:black` | **asc** | **24,653** | 97,216 | **115.5 µs** |
| rarity | `border:black` | desc | 337 | 391 | 2.5 µs |
| rarity | `usd>0.01` | **asc** | **25,418** | 97,216 | **153.6 µs** |
| usd | `border:black` | asc | 109 | 112 | — |
| usd | `border:black` (5x corpus) | asc | **545** | **560** | — |

Rarity ascending starts at `common` and collects **24,653 matches to serve 60 rows** — a 411x overshoot,
and 46x slower than the same query descending, which fills from the sparse bonus/special postings. The usd
walk's overshoot is smaller per query but **scales with the corpus**, which is worse in kind: it made the
`orderby_walk_scan` feature grade exactly `1/N` across a 10x axis.

## What it buys## What it buys

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

**3. Two cost-model features stop existing.** `orderby_walk_scan` exists only to express bucket
granularity: `n_printings` for rarity because a plane bucket ANDs the corpus, and (in the held
[run-boundary patch](./patches/local-engine-compose-walk-usd-run-boundary.patch)) a run boundary for usd.
Early stop removes the granularity, so the field goes and `printings_walked` alone prices both walks —
and becomes accurate, since examined then really is `page_span / local density`. Deleting a feature beats
fixing one.

**4. Roughly size-neutral, saving on ranges and paying on rarity.** The range indexes shrink 45% because
the value stops being repeated per pid: `price_usd` goes ~652 KB → ~359 KB (8 bytes per distinct value +
4 per printing, 4,133 values over 81,542 priced printings), and `collector_number`/`released_at` likewise.
Rarity is purely additive, since the planes stay for the filter path: 6 keys plus every printing = ~389 KB.
Net across all four is about a wash — worth stating plainly, because "it is smaller" was true of the
range-only version of this doc and is not true of the merged one.

## What it does NOT buy

**Clumping survives untouched.** The overshoot is *within* a bucket; clumping is *across* buckets.
`r:mythic` ordered by `usd` scans 31,698 entries to find 75 matches because mythics are expensive and an
ascending walk starts at pennies — every one of those entries is a genuine bit-test miss against `pbits`,
and no within-bucket ordering removes them. Expect this to fix `border:black`, `f:modern`, `r:common` and
`usd>0.01` cleanly and leave `r:mythic` where it is; that one is
[a paging-branch choice](./local-engine-compose-paging-cost-based.md).

**The planes stay.** This replaces the rarity walk's use of them, not the FILTER path's:
`rarity_cmp_leaf_bits` wants a whole-bucket bitmap and a plane is the right shape for that — 1,519 word
ANDs against a rarity's whole printing count. The plane/postings crossover is correct *for filtering* and
this change does not touch it. `special`/`bonus` need no plane either way; their bits twiddle in from
postings.

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
- **Build + serialization** — one extra sort per index, by the tiebreak rather than by pid. Plus a NEW
  rarity index, which the build does not have today.
- **`walk_rarity_orderby_page` disappears into `walk_range_orderby_page`.** They become one walk over one
  layout, which is the structural payoff and also the largest single simplification here.
- **`orderby_walk_available`** currently answers "usd or rarity", the two columns with a printing-space
  structure. Unchanged in meaning, but its doc explains the two structures separately and will not.

**`ARCHIVE_FORMAT_VERSION` must be bumped to the actual current date**, not incremented: this is a
layout change and a stale archive must fail the header check rather than be read as garbage.

## Order to do it in

1. Change the range-index type and build, bump `ARCHIVE_FORMAT_VERSION`, and get the suite green with
   every consumer mechanically ported. No behaviour change intended at this step —
   `force_plan_differential_agreement` asserts full row order across every plan and is the gate. One
   format bump covers the rarity index too, so add it in this step even though nothing reads it yet.
2. Then change `walk_range_orderby_page` / `collect_orderby_page` to stop mid-bucket, which is where the
   win is. Row identity again, because this alters which rows a page contains at a tie boundary if the
   tiebreak sort is wrong.
3. Then delete `range_walk_run_boundary` and the `SortCol::PriceUsd` arm of `orderby_walk_scan` if the
   patch has landed by then, and re-grade `OW/usd` against `printings_examined` — it should read ~1.0 at
   every corpus size for the non-clumped queries.
4. Re-run the corpus-size sweep. The whole point is that the feature now scales; three sizes confirm it.

Gate each step on the compose-paging slice `bench_regret_matrix` reports, not the total — `OrderbyWalk`
is 4% of rows, so a real improvement there is invisible in an aggregate.
