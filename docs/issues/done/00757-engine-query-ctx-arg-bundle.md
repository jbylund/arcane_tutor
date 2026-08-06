# Engine: Bundle the Plan-Selection Layer's Repeated Args into Two Borrow Structs

**DONE.** Status: **implemented 2026-07-24** on `engine-query-ctx-arg-bundle`. Tracked as #757. Filed from the
#752 ([00745-engine-explain-analyze.md](00745-engine-explain-analyze.md)) review — the `explain`/
`explain_analyze` primitives pushed the plan-selection layer's argument lists past the point where
threading them individually reads well. Revised the same day before implementing: scope widened (two
structs, not one; 13 all-five-slice functions, not 6) and the priority re-argued — see
[Do it now, not "next time"](#do-it-now-not-next-time). Measured outcome in
[Outcome](#outcome-as-landed).

## The finding

Two distinct arg clusters repeat across the whole layer, and neither varies within a call chain.

**Cluster A — what came off the archive.** Five slices, straight off the mmap'd `Archived<CardData>`
the caller already holds:

```rust
cards:    &[AOracleCard],
printings:&[APrinting],
offsets:  &AOffsets,
strings:  &AStrings,
indexes:  &Archived<CardIndexes>,
```

**Cluster B — what the request asked for.** Six scalars, fixed for the duration of one query:

```rust
mode: Mode, prefer: Prefer, sort_col: SortCol, descending: bool, limit: usize, page_offset: usize
```

Between them they account for **191 of the 265 parameters** on the 22 functions below. The args that
actually differ per call — `filter`, `plane`, `prep`, `plan`, the bitmaps, the timing knobs — are the
meaningful signal, and they are outnumbered better than 2:1 by the boilerplate.

| Function | Args | of which cluster A | cluster B | varying | Location |
| --- | --- | --- | --- | --- | --- |
| `walk_printing_page` | 10 | 4 | 4 | 2 | [lib.rs:4442](../../../card_engine/src/lib.rs#L4442) |
| `printing_range_fastpath` | 10 | 5 | 4 | 1 | [lib.rs:4587](../../../card_engine/src/lib.rs#L4587) |
| `printing_compose_fastpath` | 11 | 4 | 6 | 1 | [lib.rs:5404](../../../card_engine/src/lib.rs#L5404) |
| `prepare_candidates` | 7 | 4 | 1 | 2 | [lib.rs:5789](../../../card_engine/src/lib.rs#L5789) |
| `exec_plane_popcount_order` | 11 | 5 | 5 | 1 | [lib.rs:5904](../../../card_engine/src/lib.rs#L5904) |
| `exec_plane_popcount_order_with_bitmap` | 12 | 5 | 5 | 2 | [lib.rs:5936](../../../card_engine/src/lib.rs#L5936) |
| `exec_card_range_popcount` | 12 | 5 | 5 | 2 | [lib.rs:5964](../../../card_engine/src/lib.rs#L5964) |
| `walk_grouped_page` | 12 | 3 | 6 | 3 | [lib.rs:6035](../../../card_engine/src/lib.rs#L6035) |
| `gather_composed_page` | 11 | 3 | 6 | 2 | [lib.rs:6139](../../../card_engine/src/lib.rs#L6139) |
| `exec_streamed_select` | 14 | 5 | 6 | 3 | [lib.rs:6226](../../../card_engine/src/lib.rs#L6226) |
| `exec_gathered_scan` | 14 | 5 | 6 | 3 | [lib.rs:6258](../../../card_engine/src/lib.rs#L6258) |
| `run_query` | 13 | 5 | 3 | 5 | [lib.rs:6324](../../../card_engine/src/lib.rs#L6324) |
| `exec_from_candidates` | 15 | 5 | 6 | 4 | [lib.rs:6376](../../../card_engine/src/lib.rs#L6376) |
| `mk_plan_feats` | 8 | 0 | 2 | 6 | [lib.rs:6407](../../../card_engine/src/lib.rs#L6407) |
| `candidate_feats` | 8 | 1 | 3 | 4 | [lib.rs:6443](../../../card_engine/src/lib.rs#L6443) |
| `acquire_plan_features` | 12 | 5 | 5 | 2 | [lib.rs:6469](../../../card_engine/src/lib.rs#L6469) |
| `run_query_routed` | 13 | 5 | 6 | 2 | [lib.rs:6595](../../../card_engine/src/lib.rs#L6595) |
| `run_query_with_plan` | 14 | 5 | 3 | 6 | [lib.rs:6696](../../../card_engine/src/lib.rs#L6696) |
| `explain` | 12 | 5 | 5 | 2 | [lib.rs:6792](../../../card_engine/src/lib.rs#L6792) |
| `explain_analyze` | 15 | 5 | 3 | 7 | [lib.rs:6859](../../../card_engine/src/lib.rs#L6859) |
| `run_query_streamed_popcount` | 13 | 4 | 3 | 6 | [lib.rs:6923](../../../card_engine/src/lib.rs#L6923) |
| `run_query_streamed` | 18 | 4 | 6 | 8 | [lib.rs:7052](../../../card_engine/src/lib.rs#L7052) |

Thirteen of these take all five of cluster A. (The original filing listed six functions; it was
scoped to the plan-selection entry points and missed the executors, the fastpaths, and the streamed
walkers, which carry the same two clusters.) The counts drive **25** `#[allow(clippy::too_many_arguments)]`
in `lib.rs`, and two functions are over the threshold with *no* allow at all — `run_query` (13/7) and
the PyO3 `query` (10/7) are live clippy warnings today.

## Proposed change

Two borrow-only structs, built once per query at the PyO3 entry point and threaded as single args:

```rust
struct QueryCtx<'a> {
    cards:     &'a [AOracleCard],
    printings: &'a [APrinting],
    offsets:   &'a AOffsets,
    strings:   &'a AStrings,
    indexes:   &'a Archived<CardIndexes>,
}

struct QueryParams {
    mode: Mode, prefer: Prefer, sort_col: SortCol, descending: bool,
    limit: usize, page_offset: usize,
}
```

`QueryCtx` holds only shared references under a single lifetime, so it's a zero-cost grouping — no
ownership change, no clone, and the existing `'a` return-borrow relationships (e.g.
`run_query_routed`'s `Vec<(&'a AOracleCard, &'a APrinting)>`) carry through the struct's lifetime
unchanged. `QueryParams` is six `Copy` scalars.

The two clusters' 191 parameters become 43 struct args (one or two per function), so conservatively
the table's 265 parameters drop to **117**. The real number is lower, because several functions'
remaining `&str` args fold into the constructors below. `exec_gathered_scan` goes
from 14 args to `(ctx, filter, prep, plane, params)`. Most of the 25 `#[allow]`s go with it.

### Two constructors that each collapse a duplicated block

- **`QueryParams::from_strs(unique, prefer, orderby, direction, limit, offset)`.** The
  `orderby_to_col` / `direction == "desc"` / `prefer_from_str` / `mode_from_unique` block is
  copy-pasted at four sites in `lib.rs` — [6339](../../../card_engine/src/lib.rs#L6339),
  [6712](../../../card_engine/src/lib.rs#L6712), [6876](../../../card_engine/src/lib.rs#L6876),
  [7963](../../../card_engine/src/lib.rs#L7963) — plus several in `tests.rs`. This is also what lets
  `run_query`, `run_query_with_plan`, and `explain_analyze` stop taking four `&str`s each: they take
  `&QueryParams` and the string→enum adapter exists once.
- **`impl<'a> From<&'a Archived<CardData>> for QueryCtx<'a>`.** Every construction site is
  `&data.cards, &data.printings, &data.offsets, &data.strings, …, &data.indexes` — the PyO3 methods
  and, more importantly, **46 `run_query` call sites in `tests.rs`**, which get *shorter*: one
  `let ctx = QueryCtx::from(&archived);` per test fn, reused across that fn's calls.

### Fold in the adjacent duplication

These are too small to be their own PRs and sit in exactly the signatures being touched:

- `PhysicalPlan::applicable(filter, mode, cards, plane, sort_col, descending, indexes)` — 7 args, 6
  call sites in `run_query_routed`/`explain`/`acquire_plan_features` — becomes
  `applicable(&ctx, &params, filter, plane)`.
- [exec_streamed_select](../../../card_engine/src/lib.rs#L6244) and
  [exec_gathered_scan](../../../card_engine/src/lib.rs#L6276) both open with the identical
  `existential_plane_for` + `Box<dyn Iterator<Item = u32>>` candidate-ids preamble. One method on
  `PreparedCandidates`.
- [exec_plane_popcount_order](../../../card_engine/src/lib.rs#L5904) is a ten-line thread-local wrapper
  around `_with_bitmap`, and both spell out the full 11/12-arg list.
- [mk_plan_feats](../../../card_engine/src/lib.rs#L6407) exists purely to avoid an 8-field struct
  literal, and needs its own `#[allow]` to do it. With the two structs in place it takes those plus
  the four fields that actually vary by count source.

## Why this isn't purely mechanical

- **`filter` stays a separate `&mut` arg**, not a `QueryCtx` field: `prepare_candidates` needs
  `&mut FilterExpr`, and `explain_analyze` deliberately clones a fresh filter per `(plan, round)`
  off a pristine snapshot (the #752 fairness discipline). Folding it into an immutable-borrow ctx
  would fight both. Keep it out.
- **The A/B split is the whole design.** `mode`/`sort_col`/`descending` are request state, not store
  state, so they do not belong in `QueryCtx` — but that argues for a second struct, not for leaving
  them loose. Anything that is neither "came off the archive" nor "the request asked for it" stays a
  positional arg; that residue is the column worth reading in the table above.
- **Not every function wants both.** `acquire_plan_features` and `explain` have no use for `prefer`;
  `prepare_candidates` uses only `mode`. Passing `&QueryParams` and ignoring fields is the right
  trade (uniform signatures beat five bespoke sub-structs), but it does mean the struct is a
  superset for some callees — worth stating rather than discovering in review.
- **Behavior must be identical.** `force_plan_differential_agreement` and the existing
  `query()`/`explain` parity tests are the regression guard — this is a signature refactor with no
  intended behavior change, so those passing unchanged is the acceptance bar. Run `cargo test` in
  **debug** as well as release: CI's `rust-test` job is a debug build, so a release-only local run
  skips the engine's `debug_assert` tripwires.

## Do it now, not "next time"

The original filing deferred this — "best done next time this layer is opened for a feature change
rather than as standalone churn, since it touches the signature of every plan-selection function and
would conflict with any in-flight work there." That rationale does not hold as of 2026-07-24, and the
inverse one does:

- **The layer is quiet.** The only open engine PR is #692, which touches `filter.rs` and nothing
  else. Every other open PR is images / fonts / query-runner / DFC work.
- **The next four engine issues all land here.** #754 (plane-subtree compose leaf + general `Not`
  arm), [#731](../00731-engine-compose-universal-evaluator.md) (compose as the universal exact
  evaluator), [#730](../00730-engine-popcount-skip-walk.md) (popcount-skip walk generalized to all
  distinct-ons), and #656 each add plans or args to these exact signatures. Landing the bundle first
  means they build on 4-arg signatures instead of each pushing a 15-arg list to 16 — which is the
  "do it while the layer is open" intent, just resolved in the other direction.

Priority is still **low-stakes** (no correctness or performance effect — a borrow struct compiles to
the same argument passing), but it is now **well-timed**, which is a different claim than the
original "low priority".

## Outcome (as landed)

**Parameters across the 22 functions: 265 → 104**, i.e. 161 removed — better than the 117 estimated
above. The estimate assumed each function would trade its cluster members for one or two struct args
and keep everything else. Five functions did better than that, because args they took *individually*
turned out to be derivable from `ctx.indexes`:

- `run_query_streamed` 18 → 7: `artwork_groups`, `artwork_group_col`, and `max_artwork_groups` are
  all `ctx.indexes` fields, so the caller no longer projects them out one at a time.
- `run_query_streamed_popcount` 13 → 7: same for `planes`.
- `walk_grouped_page` 12 → 4 and `gather_composed_page` 11 → 3: same for `max_artwork_groups`.
- `printing_compose_fastpath` 11 → 3, `printing_range_fastpath` 10 → 3, `exec_from_candidates` 15 → 6,
  `run_query_routed` 13 → 4, `acquire_plan_features` 12 → 4.

`#[allow(clippy::too_many_arguments)]` in `lib.rs`: **25 → 10**. One of the survivors turned out to be
*already* stale before this change (`run_query_streamed`'s), and the pymethod `explain`'s is still
required — `self` counts toward clippy's 7, so its 8 keyword params trip the lint even though the free
`explain` it delegates to now takes 4. The remaining ten are all genuine: the per-printing kernels
(`push_card_matches` 18, `card_match_count` 12), the value-index walkers, and the three PyO3 keyword
surfaces.

Bodies were left byte-identical wherever possible by destructuring at the top of each function
(`let QueryCtx { cards, printings, .. } = *ctx;`), so the diff is signatures and call sites rather
than logic — which is what makes "behavior is identical" checkable by reading it.

**Guard:** `cargo test` passes 139/139 in **both** debug and release (debug matters — CI's `rust-test`
job is a debug build, so release-only runs skip the `debug_assert` tripwires), including
`force_plan_differential_agreement`, `explain_reports_ranked_applicable_plans`, and
`explain_analyze_matches_explain_and_times_every_plan`.

### Deliberately left alone

`aligned_page`, `walk_range_orderby_page`, and `walk_rarity_orderby_page` keep explicit slice args.
They are a different family: each walks *its own value-sorted index slice* against `printing_to_card`,
not the query's store view, so a `QueryCtx` would be a misleading thing to hand them (it advertises
access to `offsets`/`strings` they have no business reading). They were never in the finding's table.

### Test-side shape

Three helpers in `tests.rs` carry the params a kernel test needs: `kernel_params(mode, sort_col,
descending, limit, page_offset)` for enum-space call sites, `explain_params(mode, limit)`, and
`mode_only_params(mode)` for `prepare_candidates` (which reads nothing but `mode` — the helper's doc
says so, and says to pass real values if a callee ever starts reading the rest).

## Follow-up this surfaced: the cost model ignores `prefer`

Building `QueryParams` for the PyO3 `explain` method forced the question of what to pass for `prefer`,
which that method's signature has never accepted. The answer is that it doesn't currently matter *for
what explain reports* — `cost.rs` contains **zero** references to `prefer`, and `cost::PlanFeatures`
has no such field, so both the argmin and every `predicted_ns` are prefer-blind no matter what is
passed.

That is a gap in the cost model, not a property of execution. `prefer` genuinely changes the work a
plan does: it selects each card's representative printing, and `Prefer::Default` specifically lets
`gather_composed_page` early-break on the first set printing instead of scoring every printing of the
card (`run_query_streamed_popcount` has the same find-first vs. score-all split). So an `explain` for
a non-default `prefer` predicts numbers for work the real query wouldn't do, and `explain_analyze` —
which *does* take `prefer` and really runs the plans — will show `trials_ns` responding to it while
its own `predicted_ns` does not.

Worth its own issue: either model `prefer`'s per-card cost in `PlanFeatures`, or add `prefer` to the
`explain` surface and document the estimate as prefer-independent on purpose. Not folded in here —
this PR is a signature refactor with no behavior change, and either fix changes reported numbers.

## Not in scope

Both of these turned up alongside this finding and are independent PRs, so they belong in their own
docs rather than folded in here:

- **Clippy is not in CI, and is not clean.** `rust-tests.yml` runs only `cargo test`; `lint.yml` is
  Python-only. `cargo clippy --all-targets` emits 68 warnings today. That means the 25 `#[allow]`s
  this change sheds are being maintained by hand for a lint nothing enforces — so the payoff will
  quietly regress without a `-D warnings` gate. Highest-leverage cleanup in the crate, and a
  prerequisite for this one's benefit sticking. **Done** in
  [local-engine-clippy-ci-gate.md](local-engine-clippy-ci-gate.md), stacked directly on this change.
- **`lib.rs` is 8146 lines** and already carved by 30 `// ───` section banners that map cleanly onto
  modules. The split is close to mechanical: `mod tests` sits at the crate root
  ([lib.rs:8130](../../../card_engine/src/lib.rs#L8130)) importing ~100 names via `use super::{…}`, and
  a private `use newmod::*;` at the root keeps all of them resolving through `super::` unchanged.
  Sequence it *after* this change, so the new module boundaries carry 4-arg signatures rather than
  15-arg ones.

## Related

- [00745-engine-explain-analyze.md](00745-engine-explain-analyze.md) — the diagnostic work (#752)
  that pushed the arg counts up and surfaced this.
- [00702-engine-plan-selection-layer.md](00702-engine-plan-selection-layer.md) — the
  cost-based router (`run_query_routed`, `acquire_plan_features`) whose signatures this cleans up.
- [local-engine-range-veto-redundancy.md](../local-engine-range-veto-redundancy.md) — the other
  standing cleanup in this layer, but a *semantic* one (a hardcoded second copy of a cost decision)
  that needs measurement first, unlike this one.
