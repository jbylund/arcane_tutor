# Let compose choose OrderbyWalk vs Gather on cost, not on shape

`compose_paging_with_total` decides which paging branch `printing_compose_fastpath` will take, and for
one case it decides by **shape** where the two candidates have wildly different costs:

```rust
if perm exists                              { ... Perm }
else if printing && orderby_walk_available   { OrderbyWalk }   // <- unconditional
else if gather_declines                      { Decline }
else                                         { Gather }
```

Printing mode on a `usd`/`rarity` orderby always takes the walk. `Gather` is reachable only when the
walk *declines* at runtime (the null-value tail, or a page past the value structure). So compose has no
internal argmin, even though `plan_cost` already has a fully-formed arm for both branches.

This is now the **only** remaining error on the `OrderbyWalk` slice. The value-major layout
([done/local-engine-value-major-sort-indexes.md](./done/local-engine-value-major-sort-indexes.md))
deleted the walk's overshoot and the `orderby_walk_scan` floor with it, and `printings_walked` now
grades flat across a 10x corpus axis for every non-clumped query. `r:mythic` ordered by usd is the
exception and the reason, and it is the case below.

## Why that costs something

The walk is fast when the filter's matches are dense near the start of the sort order and catastrophic
when they are not. Measured on `r:mythic` ordered by `usd`, printing mode:

| | |
| --- | --: |
| entries the walk scanned | **30,646** |
| matches it needed | 60 (a page) |
| matches it found in those entries | 75 |
| the composed set's total size | 8,924 |

Mythics are expensive and an ascending price walk starts at pennies, so almost every one of those 30,646
bit-tests is a miss. `gather_composed_page` would page the same query in **O(8,924)** — it materialises
the composed set and pages it with the bounded `GatherSelect` — so the branch the shape test forces is
~3.4x more work than the one it forbids.

Measured after the layout change, printing mode, `limit=60 offset=0`, the compose participant's own
trial minimum:

- walk, realized: **11.0 µs** for 30,646 entries — 0.36 ns/entry
- walk, as charged: **2.5 µs** — 4.4x under
- and it gets worse with the corpus: 122,780 entries at 5x, where the charge cannot move at all

The charge used to be 60x under; deleting `orderby_walk_scan` did not cause that improvement, it just
stopped a wrong floor from accidentally covering part of a different error. The residual 4.4x is
clumping, and unlike everything else on this slice it is NOT flat across the corpus axis — the feature
grades 0.03 at 1x and 0.01 at 5x.

## Why this is the cheap fix, and cheaper than the alternatives

The clumping problem — that `printings_walked` divides by the GLOBAL match rate and cannot see where in
the sort order the matches sit — is real and is documented as unfixable by a density ratio
(`WALK_LENGTH_BIAS`'s own doc). Earlier notes on this branch treated that as the ceiling on the compose
arm.

It is not, and this is the reframing: **a router does not need to know how bad the walk is, only that it
is bad.** That is a far weaker requirement than an accurate clumping model. `printings_walked` already
grows as `matches` shrinks (948 for mythic against ~100 for `border:black`), so the signal that a walk
will be long is present even when the magnitude is wrong — and `Gather` is O(matches), so it is exactly
the branch that gets *safer* as the walk gets worse. The two arms fail in opposite directions, which is
what makes an argmin between them worth having.

It is also cheaper than either structural alternative:

- A price-ordered index per rarity value would fix this query, but needs one index per (predicate value ×
  sort column) — small individually, unbounded in combination.
- [The value-major layout](./done/local-engine-value-major-sort-indexes.md) fixed the *overshoot* and
  was worth doing on its own merits — but clumping is *across* runs and survived it untouched, as that
  work predicted it would.

## What to do

1. Replace the unconditional `OrderbyWalk` with a comparison: price both branches through `plan_cost`
   and take the cheaper. The arms exist; this is a branch selection, not a new model.
2. **This precondition is now satisfied.** It read: "the walk's charge must be fixed in the same change
   or the comparison is decided by the 60x error rather than by cost." The layout change did that — the
   walk is charged 2.5 µs against 11.0 realized on the worst case and grades flat everywhere else, so
   the comparison is now decided by cost on every non-clumped query and by a 4.4x under-charge on the
   clumped one. That is the error an argmin is allowed to work with, because `Gather` gets safer exactly
   as the walk gets worse.
3. `compose_paging` is asserted against the branch actually taken by
   `compose_paging_prediction_matches_the_branch_taken` — a cost-based choice must stay predictable, so
   the prediction has to run the same comparison the executor will.

Gate on the compose-paging slice `bench_regret_matrix` reports rather than the total: `OrderbyWalk` is 4%
of rows, so a real improvement there is invisible in the aggregate.

## Status

The walk side is now measured directly (11.0 µs realized, 30,646 entries) rather than inferred from a
rate. The `Gather` side is still arithmetic: the conclusion that the branches are near-tied on this
query has not been verified by running both. Doing that is step zero — force each branch on the same
query and time them.
