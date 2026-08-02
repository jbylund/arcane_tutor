# The four fast paths report features nobody can check

`explain` reports a cost feature vector for all six plans. `explain_analyze` reports execution
counters for two of them. The other four — `PrintingRangeScan`, `PrintingCompose`,
`PlanePopcountOrder`, `CardRangePopcount` — return zeros, so every feature their cost arms key on is
in exactly the state `matches` and `scan_units` were in before #797: reported, consumed by
`plan_cost`, and unverified against anything the executor actually does.

That is not a hypothetical gap. #797's headline finding was a compose-costing bug — the two range
acquire branches price a competing `PrintingCompose` with the untouched `mk_plan_feats` default of
`Gather`, so they cost an arm it never runs. The unchecked features are where the errors are.

The reason it was not fixed there is that it is not four missing `+= 1`s. It needs three different
counter vocabularies and one prerequisite.

## The existing counters are the card-match-loop's vocabulary

`cards_visited` / `printings_scanned` / `matches_pushed` describe one shape: iterate candidates,
evaluate a filter per card, push per-printing matches. That is `exec_gathered_scan` and
`run_query_streamed` and nothing else. None of the four fast paths has that loop — they are bitmap
and index operations.

So their zeros today are **zero-by-definition, not zero-by-omission**, and adding the same three
counters would produce numbers that are either always 0 or unrelated to the feature they would be
checked against. `scripts/bench_cost_model_agreement.py` already has to work around exactly this
distinction for `ns_prepare` ("`== 0` because it has no such phase — which is not the same as
spending 0% of its run there").

## What each plan would actually need

| plan | real cost driver | feature it should be checked against |
| ---- | ---------------- | ------------------------------------ |
| `CardRangePopcount` | words scanned in the scatter + skip | `popcount_words` |
| `PlanePopcountOrder` | same | `popcount_words` |
| `PrintingRangeScan` | index entries walked to the page | none that fits — see below |
| `PrintingCompose` | four separate build passes | `broadcast_printings`, `scatter_printings`, `project_printings`, `popcount_words` |

### The popcount pair is one site, and it is the cheap first step

`exec_card_range_popcount` and `exec_plane_popcount_order_with_bitmap` both delegate to
`run_query_streamed_popcount` (`card_engine/src/lib.rs`). One function, two plans. It does no filter
evaluation at all — membership is the bitmap — and its work is a whole-bitmap popcount, a scatter
through `inv_perm`, and a word-skip to the page. All three are word-counted, which is precisely what
`popcount_words` already predicts.

One counter at one site closes two of the four, against a feature that already exists. Do this first.

### `PrintingRangeScan`'s `k` is not a scan

`printing_range_fastpath` gets `k` from two `partition_point` binary searches. It never visits `k`
printings — it gets the count in O(log n) and then walks only to the page, via `aligned_page` (a
slice) or `walk_printing_page` (a permutation walk).

So there is no counter that would validate `k`, because `k` is not a count of work. The honest
instrumentation here is rows emitted by the page walk, which no current feature predicts. That makes
this plan a cost-model question before it is an instrumentation question.

## The prerequisite: features under a range acquire describe other plans

This is the part that makes it more than a mechanical change. Under `Prep::Range`, the feature vector
is deliberately set up to cost the *materializing alternatives*, not the plan that won:

```rust
// bare range branch — matches=k, eval_domain=n_cards, scan_units=n_printings
let mut feats = mk_plan_feats(ctx, params, k, n_cards, n_printings, verify_cost_tier(filter));
```

with the comment "P3/P4 estimated unnarrowed (their broad regime)". #797 already documents the same
hazard for `materialize_ns`: "Under `Prep::Range` the two materializing plans are estimated
UNNARROWED, so this figure has no referent there — do not pool range-acquired rows with
candidate-acquired ones."

A counter compared against `scan_units` on a range-acquired query would therefore be comparing a fast
path's real work against a number that was never about it. **Per-acquire-branch feature semantics have
to be settled before the counters mean anything**, or the new rows will read as enormous disagreements
that are actually correct behaviour.

## What to change

1. Counter the word passes in `run_query_streamed_popcount`, reported against `popcount_words`. Two
   plans, one site, existing feature.
2. Decide what `scan_units` and `eval_domain` mean under a range or compose acquire — one definition
   per acquire branch, documented — before adding counters that would be checked against them.
3. Counter `PrintingCompose`'s four build passes separately. They are already four distinct features;
   pooling them into one counter would lose the thing that makes them worth checking.
4. Leave `PrintingRangeScan` last. It needs a cost term that describes its page walk before a counter
   has anything to disagree with.

`card_engine`'s `plan_stats_never_leak_between_participants` currently asserts the *opposite* contract
— that these four report zeros. That is deliberate: it pins today's behaviour so that instrumenting
them is a decision someone makes on purpose rather than a silent change in what a zero means. Expect
to update it, one plan at a time, as each is instrumented.

## What this does not cover

Phase timings. The `ns_setup`/`ns_loop`/`ns_finish` split is also card-loop-shaped, and the fast paths
have their own meaningful splits (compose build vs. page walk; scatter vs. skip vs. emit). Worth doing,
but the counters are the higher-value half: a wrong feature count cannot be repaired by any rate
constant, whereas an unattributed phase only widens the residual.
