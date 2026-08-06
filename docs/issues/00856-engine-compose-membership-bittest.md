# The materializing plans re-derive membership compose already computed exactly

Status: **designed and measured, not implemented.** Filed as
[#856](https://github.com/jbylund/sylvan_librarian/issues/856). Worth ~**8×** on the highest-regret query
class in the engine — see the re-measurement caveat at the bottom before quoting that figure.

**Mechanism re-verified against `main` after the #833–#845 stack.** The anchor line below is unchanged; #843
added `n.proven` as a third element and nothing else.

## No, #843 did not already do this

The first question a reviewer will ask, and the answer is in the code rather than in judgement.
[#843](done/local-engine-proven-conjuncts.md)'s `Narrowed::proven` mask is **card-space only by
construction** — a child qualifies only when its own narrowing came back *"tight AND card-space"*, and a
fused range interval *"is printing-space besides, so it can never be proven"* (`card_engine/src/lib.rs`,
at the mask construction).

So #843 stops `card_pass` from re-verifying **card-space** conjuncts the candidate set proves; this stops the
per-printing walk from re-deriving **printing-space** membership. The two are complementary, and the second
was deliberately out of #843's scope.

## The waste

`narrow_candidates_exact` ends with:

```rust
(Some(n.set), n.tight && !printing_space, n.proven)
```

A printing-space narrowing is never reported as `residual_exact`, even when `n.tight`. That is correct
as far as it goes — `into_card_space` projects "some printing matches", which does not imply "this
printing matches", so card-space candidacy really is lossy. But `n.set` IS exact membership in printing
space, and it is discarded.

So `all_match_known` is false, and the materializing plans re-derive per printing what the narrowing
already proved: `push_card_matches` and `card_match_count` evaluate the full residual for every printing
they touch.

Measured over 3,054 compose-acquired printing queries: **1,213 ms of match-loop time over 145 million
printings scanned, 6.98 ns each**. An O(1) bit test is ~1 ns, so ~145 ms — **roughly 8x less**. This is
the cell both the cost and regret matrices name as the top target
([local-engine-cost-model-agreement.md](done/local-engine-cost-model-agreement.md)).

There is in-tree precedent: `exec_card_range_popcount` threads `range_pbits` beside its card bitmap and
membership-tests in O(1), for exactly this reason — "the shown printing must actually be in range, not
just belong to a card that has some in-range printing".

## It benefits all three distinct-ons

The bitmap is printing-space truth, so any mode that walks printings under a candidate can use it:
card mode breaking at the first match, artwork grouping printings, printing enumerating them. The
current design pays a full residual evaluation in all three.

## Two designs

**A. Thread `member: Option<&[u64]>`** into `push_card_matches` / `card_match_count`, replacing
`FilterExpr::residual_matches(...)` with `bitmap_contains(bits, pid)` where present. Two signatures,
11 call sites, contained.

**B. Rewrite the filter** into a printing-bitmap membership variant, the way `memoize_text_predicates`
already rewrites `TextContains` into a memoized id-set. All 11 sites and all three modes then benefit
with no signature change — but `FilterExpr` is matched exhaustively on purpose ("a new variant must get
a considered cost here"), so it touches ~20 matches across filter.rs, estimator.rs, planes.rs and
lib.rs, plus 39 in tests.rs.

B is the better shape; A is the smaller change. Either way the plumbing is the same: capture the
bitmap in `narrow_candidates_exact` when `n.tight && printing_space`, carry it on
`PreparedCandidates`, and use it in the walk.

## The correctness constraint that decides the gate

The bitmap covers the NARROWED subexpression only. A plane is ANDed separately and is card-level truth,
so:

- **no plane, or a non-existential plane** — the bit test is complete. Every printing of a candidate
  card satisfies a card-invariant plane, so membership plus card candidacy is the whole answer.
- **existential plane (legality)** — NOT complete. "The card has some legal printing" does not mean this
  printing is legal, which is the same carve-out `plane_true_for_mode` already encodes. The per-printing
  plane check must still run.

Gate the fast path on that distinction, exactly as `prepare_candidates` already does for
`all_match_known`.

## Re-measure the magnitude before quoting it

The defect is verified present; the **numbers are not current**. The 1,213 ms / 145 M printings / 6.98 ns
figures predate the #833–#845 stack, and two of its layers moved the inputs:

- [#843](done/local-engine-proven-conjuncts.md) cut re-verification for card-space conjuncts, which changes
  how much of that 1,213 ms was this defect versus the one it fixed. `o:this border:black` went 1,993 → 542 µs
  on that change alone.
- [#840](done/local-engine-is-frame-predicates.md) / #842 changed what narrows at all, so the 3,054-query
  population is not the same population.

Expect the direction to hold — a discarded exact bitmap is still discarded — and the factor to change. Take
the measurement before writing the PR description, not after.

## Verifying it

This changes what rows come back if it is wrong, so measure rows, not timings. The row-diff harness used
for the sparse-gather experiment captured totals and page rows over **127,640 compose-acquire queries**
and compared them before/after; it found 0 differences there and would find these. Timings second:
paired `bench_query_latency_ab.py`, interleaved.

The existential-plane gate above is the part most likely to produce wrong rows rather than slow ones, and it
is per-printing-varying — so verify returned row *identity* against real data across all three distinct-ons,
not just totals ([the repair pattern](reference-engine-printing-varying-plane-repair-pattern.md) is why).
