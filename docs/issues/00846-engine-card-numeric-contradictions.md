# Card-Level Numeric Contradictions Are Not Detected

Status: proposed, not started. Filed as
[#846](https://github.com/jbylund/sylvan_librarian/issues/846). Split out of
[the two-sided range fusion](done/local-engine-two-sided-range-fusion.md) that shipped in #837, which
closed the printing-space half of this and left the card-space half untouched.

## The asymmetry

`usd>=1 usd<=0.5` is detected for free. `fuse_and_range_children` folds the two comparisons into one
half-open interval, gets `hi < lo`, clamps to `[lo, lo)`, and every consumer computes
`k = partition_point(hi) - partition_point(lo)` as `0`. That clamp is load-bearing rather than
decorative — without it the subtraction underflows and panics.

`power=3 power=5` is the same contradiction and reaches none of that code:

- `resolve_numeric_range_leaf` returns `None` for `Power`, so there is no index and no interval to fold.
- `printing_dependent` classifies `Power` / `Cmc` / `Toughness` / `Loyalty` as **card-level**, evaluated
  through `numeric_candidates` rather than through a `PrintingValueIndex`.

So the fields with a printing-space index get contradiction detection as a side effect of fusion, and the
card-level numerics get nothing. There is no measurement here yet — the query is rare, and the case for
doing it is that it is a small bind-time pass with an exact answer, not that it is costing measured time.

## Why it is a bind-time pass, not a narrowing arm

#837's mechanism is interval arithmetic over a stored index. These fields have no such index, so there is
nothing to intersect; what is available is the expression itself. A bind-time pass over an `And`'s
children, grouping by field and folding comparisons into an interval, would reach the same conclusion
without needing the store at all — and would also catch it under `Or` and `Not`, where the narrowing arm
never runs.

## Two semantics decisions first

These are the reason this is not a mechanical port of #837.

**`Ne`.** `power!=3 power=5` is satisfiable and must not fold to empty. An interval representation has no
natural slot for "everything except one point", so either `Ne` is excluded from the fold or the
representation grows a hole set.

**Null.** A card with no power satisfies neither `power=3` nor `power=5`. For a *contradiction* that
distinction does not change the answer — the result is empty either way — but the same pass is the natural
place to fold non-contradictory bounds, and there `power>=0` and "has a power" are different predicates.
Settle which one the fold means before it emits anything other than empty.

## Related

- [done/local-engine-two-sided-range-fusion.md](done/local-engine-two-sided-range-fusion.md) — the
  printing-space half, its measurement, and the unsatisfiable-interval clamp.
- [#847](00847-engine-empty-conjunction-short-circuit.md) — the other "this filter is provably empty"
  case, from disjoint plane values rather than from numeric bounds. Both want the same thing: a place
  early enough to answer empty without routing.
