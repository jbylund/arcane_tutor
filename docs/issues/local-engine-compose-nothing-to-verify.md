# Bare Compose-Exact Collection Leaves Get Charged a Verify Tier They Don't Need, Mis-Routing to `PrintingCompose`

`otag:triggered-ability` (`unique=card orderby=edhrec limit=175`, 47% dense) picks `PrintingCompose`
when `StreamedSelect` is measurably ~4x cheaper. Traced with `explain_analyze` on both `main` and
[#1003](https://github.com/jbylund/sylvan_librarian/pull/1003) — present on both, so it predates the
hybrid-storage work and isn't specific to it.

## What's wrong

| plan | predicted | measured |
|---|---:|---:|
| `PrintingCompose` (picked) | 100.7us | 176.5us |
| `StreamedSelect` | **1026.0us** | **45.7us** |

`StreamedSelect` is over-predicted ~22x. Every structural counter the acquire step reports
(`printings_walked`, `popcount_words`, `compose_scan_printings`) is identical between builds, so this
isn't a storage-representation effect — it's the router charging `StreamedSelect`/`GatheredScan` a
residual-verification cost for a predicate that has none.

## Root cause

`plane_leaves_nothing_to_verify` (`card_engine/src/lib.rs:8509`) decides whether the materializing
plans have nothing left to check per candidate card, in which case they should be charged **zero**
verify-tier cost:

```rust
fn plane_leaves_nothing_to_verify(filter: &FilterExpr, mode: Mode, plane: Option<&PlaneExpr>, indexes: &Archived<CardIndexes>) -> bool {
    matches!(filter, FilterExpr::True)
        && plane.is_none_or(|expr| { ... })
}
```

It only recognizes the **legality-plane** case: the plane machinery rewrites a fully card-invariant
legality filter down to `FilterExpr::True`, and this function catches that. It has no equivalent for
the **compose** machinery. When the whole filter is a single compose-exact leaf like
`otag:triggered-ability`, `filter` is still the `CollectionCmp` node — never rewritten to `True` — so
`nothing_to_verify` comes back `false`, and `acquire_plan_features`'s `PrintingCompose` branch
(`lib.rs:10395-10396`) falls through to `verify_cost_tier(composed)`, charging a real per-card
verification cost.

That's the wrong answer for this predicate class specifically: `subtypes`/`keywords`/`oracle_tags` are
"pure card properties" (a card either has the tag or it doesn't — every printing agrees, per the
comment at `lib.rs:6858-6860`). So `card_match_count` on the materializing plans would ALSO resolve
trivially at card level for every candidate, exactly the property the legality carve-out already
exploits for card-invariant formats — the router just doesn't know it applies here too.

Concretely, two features stay wrongly inflated because `nothing_to_verify` is `false`:

- `scan_units` (`lib.rs:10414`): `if nothing_to_verify { printing_matches } else { scan_units }` — stays
  at the expensive `scan_all(domain_cards)` estimate (every printing of every candidate) instead of the
  cheap `printing_matches` value.
- `feats.stream_scan_units` (`lib.rs:10428`): `if tier == 0 { 0 } else { ... }` — stays non-zero because
  `tier` never got zeroed.

## Why this is worth fixing separately from #1003

It's a pre-existing router bug, not something #1003 introduced or needs to fix — the numbers above are
identical on `main`. But #1003's own benchmarking is what surfaced it (a bare `otag:` query was the
test case), and the fix belongs with the cost model rather than the storage-migration PR.

## Scope of a fix

Not a trivial extension of the existing gate — needs to be scoped correctly:

- Only for **card-space** collection fields (`subtypes`, `keywords`, `oracle_tags`), which have the
  "every printing of a matching card matches" invariant. `art_tags`/`is_tags`/`frame_data` are
  printing-space — a printing can carry a tag its sibling printings don't — so they do NOT get this
  carve-out.
- Only for a **bare leaf**, matching the legality carve-out's own caveat: "one printing-varying
  partner... makes `card_pass` return `PrintingDep` for every card, and P3 then walks the whole span
  like P4" (`lib.rs:10436-10439`). A card-space `CollectionCmp` ANDed with a printing-varying predicate
  does not have the same guarantee.
- Should likely generalize `plane_leaves_nothing_to_verify`'s signature (or add a sibling function) to
  ask the same question compose's branch needs, mirroring how the legality carve-out was itself
  extracted so "the ROUTER can ask the same question the EXECUTOR does" (the function's own docstring).

## Validation needed

This changes routing for every compose-exact bare leaf on a card-invariant collection field, not just
`otag:` — wider blast radius than a storage change. Before shipping:

- `plan_cost_model_matches_gold` (src/tests.rs) — the existing gold-standard argmin test.
- Re-run `fit_cost_model.py`'s calibration / `bench_feature_accuracy.py` to confirm `scan_units` /
  `stream_scan_units` grade correctly against realized `printings_examined` for this newly-recognized
  population, the same way `bench_feature_accuracy.py` already grades the `tier == 0` legality case.
- A regret-style sweep (uniform traffic, paired) checking `PrintingCompose -> StreamedSelect` moves in
  the right direction without flipping any `GatheredScan` cases the wrong way — same instrument
  `local-engine-compose-build-rates.md` used to catch a compensating-rate regression from a
  superficially-correct constant fix.

## Status

Not started. Root cause identified and localized; fix not attempted.
