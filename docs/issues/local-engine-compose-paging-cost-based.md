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

## Why that costs something

The walk is fast when the filter's matches are dense near the start of the sort order and catastrophic
when they are not. Measured on `r:mythic` ordered by `usd`, printing mode:

| | |
| --- | --: |
| entries the walk scanned | **31,698** |
| matches it needed | 60 (a page) |
| matches it found in those entries | 75 |
| the composed set's total size | 8,924 |

Mythics are expensive and an ascending price walk starts at pennies, so almost every one of those 31,698
bit-tests is a miss. `gather_composed_page` would page the same query in **O(8,924)** — it materialises
the composed set and pages it with the bounded `GatherSelect` — so the branch the shape test forces is
~3.5x more work than the one it forbids.

Pricing, using the rate measured for plane-bucket steps in
[local-engine-rarity-walk-cost.md](./local-engine-rarity-walk-cost.md) (1.07 ns/entry, not the shipped
0.58):

- walk, realized: 31,698 entries ≈ **34 µs**
- walk, as charged: `printings_walked` = 948 × 0.58 ≈ **0.55 µs** — 60x under
- gather, roughly: the Gather arm over ~8,924 printings / ~2,568 cards ≈ **34 µs**

So correctly priced the two are close to a tie on this query, and either is acceptable. The damage is
entirely from the walk being charged 60x under while being the only branch the shape test allows.

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
- [The value-major range index](./local-engine-range-index-value-major.md) fixes the *overshoot* and is
  worth doing on its own merits, but clumping is *across* buckets and survives it untouched.

## What to do

1. Replace the unconditional `OrderbyWalk` with a comparison: price both branches through `plan_cost`
   and take the cheaper. The arms exist; this is a branch selection, not a new model.
2. The walk's charge must be fixed in the same change or the comparison is decided by the 60x error
   rather than by cost. That means the rate split from the rarity-walk doc, and the usd feature from
   [the walk-features doc](./local-engine-compose-walk-features.md).
3. `compose_paging` is asserted against the branch actually taken by
   `compose_paging_prediction_matches_the_branch_taken` — a cost-based choice must stay predictable, so
   the prediction has to run the same comparison the executor will.

Gate on the compose-paging slice `bench_regret_matrix` reports rather than the total: `OrderbyWalk` is 4%
of rows, so a real improvement there is invisible in the aggregate.

## Status

Not measured. The costs above are the measured 31,698 entries and 8,924 total, times rates measured
elsewhere — the arithmetic is sound but the conclusion that the branches are near-tied has not been
verified by running both. Doing that is step zero: force each branch on the same query and time them.
