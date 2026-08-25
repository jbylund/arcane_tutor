# Engine: Popcount-Skip Walk for Deep Pagination, Generalized to All Distinct-Ons

Status: **prototyped and measured for all three distinct-ons, not wired in.** Filed as
[#730](https://github.com/jbylund/sylvan_librarian/issues/730). Deferred optimization split out of
the #724 printing-space compose work ([00724](done/00724-engine-printing-existential-planes.md)). See
[local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md)
for the prototypes, correctness tests, and real-corpus crossover measurements this doc's "the idea"
section asked for, and
[reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)
for why the alternative (a better cost-model *estimate* of the forward walk's depth) turned out to be
the harder path, which is what led back to this doc.

## Two ways to page a match bitmap

Once a query's matches are a bitmap in the result space, the page can be produced two ways:

- **Forward grouped walk** (`walk_printing_page` / `walk_artwork_page`, and card after the #724
  unification): walk the sort permutation from the front, collapse each card's matching printings to
  the distinct-on's granularity (printing = every set printing, artwork = one representative per
  `artwork_group_id`, card = one per card), and emit until `offset + limit` rows have passed. Cost is
  O(rows visited) = O(offset + limit) worth of the sort order.
- **Popcount-skip order** (`run_query_streamed_popcount`, the #634/#725 card-space walk): word-scan the
  match bitmap + per-card counts to land directly on the O-th match, then emit `limit`. Cost is
  O(bitmap words) to skip + O(limit) to emit — **independent of how deep the offset is**.

At `offset = 0` they're equivalent (the forward walk fills the page immediately). The skip only wins at
**deep pagination** — page 50 of a broad result — where the forward walk pays for the rows before the
page and the skip does not.

## What the #724 unification changed

#724 unified printing / card / artwork compose onto the *one* forward grouped walk (one plan, one cost
formula parameterized by output space). That was the right clarity/correctness call, but it moved **card
compose off the popcount-skip walk** it previously inherited from `CardRangePopcount`. So card compose
now pays the forward-walk offset cost for deep pages. Acceptable because deep pagination is rare in
card-search UIs (page 0 dominates), but it is a regression at the tail worth recording.

## The idea

1. **Restore popcount-skip for deep offsets** — route the compose plan to a skip-order walk when
   `offset` is large enough that visiting the preceding rows dominates (a cost-model decision, or a
   fixed threshold).
2. **Generalize popcount-skip to all three distinct-ons.** Today only card space has it. The bitmaps a
   skip walk needs already exist after #724:
   - **printing** — skip over the composed printing bitmap directly.
   - **card** — the card-existence projection (already built).
   - **artwork** — the artwork-existence projection + per-card artwork counts (`artwork_groups`, already
     stored) to skip whole cards' artwork spans.

   So this is a walk-side addition, not a new index — the projection substrate is in place.

## A second consumer, which changes the deferral calculus

Everything above is about **deep pagination** — skipping past `offset` rows without visiting them. The same
machinery is wanted for a different purpose, and that is new since this doc was written.

[#856](00856-engine-compose-membership-bittest.md) / [#857](00857-engine-membership-merge-sorted-list.md): the
narrowing computes exact printing membership and discards it, so the materializing plans re-derive it per
printing at 5.9 ns each. #857 fixes that for `GatheredScan` with a merge over the sorted candidate list, but a
merge needs ascending pid order and **`StreamedSelect` walks a permutation in sort order**, so it needs
membership as a bitmap *in permutation space* — which is exactly `run_query_streamed_popcount`'s
scatter-through-`inv_perm`.

**The blocking limitation is the same one item 1 below implies:** that walk only runs for `unique=card` with a
`FilterExpr::True` residual, i.e. where the plane bitmap already *is* the match set. Extending it past `True`
— carrying a real match set through `inv_perm` — is the prerequisite for both purposes. It needs per-card
counts for the skip, since a popcount counts cards and not matches.
[#852](00852-engine-compose-acquire-p3-p4-ranking.md) carries the same item in its carried-forward list.

**An alternative was measured and rejected**, so it does not need re-deriving: keying each candidate pid by its
card's permutation position (`inv_perm[printing_to_card[pid]]`) and *sorting*, so a forward pointer works
without a bitmap. **2.4–2.9 µs per query against 0.2–0.3 µs to scatter into a bitmap**, widening to 23 µs
against 5.5 µs when every printing matches — O(k log k) with two dependent random loads per element, against an
O(k) scatter. Numbers in `card_engine/src/bench_membership_check.rs`. The scatter is the right shape.

## Progress: prototyped, measured, not yet worth the deferral reasoning below

A later session, arriving from the cost-model side (trying to *estimate* `walk_grouped_page`'s depth
accurately enough to price it — see the estimator doc linked above), ended up building exactly what
this doc proposed instead of a better estimate. `#[cfg(test)]` prototypes for all three distinct-ons
(`walk_card_page_via_popcount_skip`, `walk_printing_page_via_popcount_skip`,
`walk_artwork_page_via_popcount_skip`), each verified against `walk_grouped_page` row-for-row across
360 random-bitmap cases, and measured on real corpus data: a genuine crossover in every mode, old
cheaper at shallow offsets, the popcount-skip approach 1.2-4x cheaper by offset 8,000-25,000
(`otag:triggered-ability`, this session's own worked clumped-tag example). Full numbers in
[local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md).

Also prototyped: a v1 decision rule (which strategy to run, decided at acquire time) for the one
population with a validated depth estimate (`unique=printing`, bare leaf, EDHREC —
`WalkCheckpoints`), and a follow-on design question — whether the scatter phase can select the page
directly in one pass instead of a separate skip+re-walk, and if so with what data structure — split
into [reference-engine-compose-popcount-skip-topk-select.md](reference-engine-compose-popcount-skip-topk-select.md).
**Answered, negatively**: built and measured both a heap and a specialized batch-prune one-phase
selector; both lose to the three-phase design in the offset range that matters, because a live
top-*k* structure's cost grows with `offset + limit` in a way the three-phase design's cheap-count /
bounded-materialize split avoids. The three-phase design is the one to keep.

None of this is wired into the fastpath yet — see the prototype doc's own open items (no decision
rule for `Card`/`Artwork`) before the reasoning below about whether it's worth building applies.

## Why deferred (the original reasoning, before the above)

Deep pagination is rare, and unifying the three modes onto one walk was the larger win. This is a
targeted optimization to build once/if deep-offset compose queries prove hot — measure the offset
distribution of real traffic before spending the complexity.

**That reasoning covers the pagination case only.** With a second consumer above, the question is no longer
"are deep-offset compose queries hot" alone — it is also whether the `StreamedSelect`-on-compose population is
worth serving. Which is currently **unmeasured**: it never appeared in #856's sample (0 of 377 picked
`StreamedSelect`), so sizing it is the cheap first step, not building the walk.

## Related

- [local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md)
  — the prototypes, correctness tests, and crossover measurements for all three distinct-ons.
- [reference-engine-compose-popcount-skip-topk-select.md](reference-engine-compose-popcount-skip-topk-select.md)
  — collapsing the skip+re-walk into the scatter itself, and which structure should hold the page.
- [reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)
  — the cost-model-estimation path that turned out to be the harder alternative to this doc's own idea.
- [00724](done/00724-engine-printing-existential-planes.md) — the printing-space compose plan whose
  unification this splits off from.
- `run_query_streamed_popcount` (`card_engine/src/lib.rs`) — the existing card-space popcount-skip walk
  to generalize.
- [#856](00856-engine-compose-membership-bittest.md) / [#857](00857-engine-membership-merge-sorted-list.md) —
  the second consumer, and where the sort-vs-scatter measurement lives.
- [local-engine-instrument-fast-paths.md](local-engine-instrument-fast-paths.md) — `run_query_streamed_popcount`
  is also the one site whose counter would instrument two of the four uninstrumented fast paths, so anyone
  opening this file should land that first.
