# The materializing plans re-derive membership compose already computed exactly

Status: **designed and measured, not implemented.** Filed as
[#856](https://github.com/jbylund/sylvan_librarian/issues/856).

**Worth 3.5× end to end on the population that pays it, where the match loop is 86% of query time.**
Re-measured on `main` 2026-08-06, post-#833–#845 — figures and method under "What it is worth". The
earlier ~8× headline is withdrawn: it was a *forced-trial loop-time* ratio, not an end-to-end one.

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

This is the cell both the cost and regret matrices independently named as the top target
([local-engine-cost-model-agreement.md](done/local-engine-cost-model-agreement.md)).

The original measurement here read *"3,054 compose-acquired printing queries: 1,213 ms of match-loop time
over 145 million printings scanned, 6.98 ns each… roughly 8× less"*. That has been re-measured and the
population re-identified — see [What it is worth](#what-it-is-worth). Current figures: **86% of routed time
on 896 queries at 5.8 ns per printing, ~3.5× end to end.**

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

## What it is worth

Re-measured on `main` at `1e5035e` (2026-08-06), after the whole #833–#845 stack, with
`scripts/bench_membership_waste.py`. 30,000 uniform-sampled queries, Docker shut down, two seeds.

**The population.** 2,495 of 30,000 sampled queries are printing-mode on a compose acquire; **896 of those
(36%) route to a materializing plan**, which is the population this change can touch — 3.0% of the uniform
sample. Every one of the 896 picked `GatheredScan`; `StreamedSelect` was never chosen here, so in practice
this is `push_card_matches`, not `card_match_count`.

**The rate, and how much of the query it is.**

| | seed 20260806 | seed 777 |
| --- | --: | --: |
| queries (picked, materializing) | 896 | 874 |
| routed time | 18.5 ms | 18.5 ms |
| of which match loop | 16.0 ms — **86.2%** | 16.0 ms — **86.6%** |
| printings examined | 2.8 M | 2.6 M |
| **ns per printing** | **5.80** | **6.11** |
| per-query median ns/printing | 5.81 | 6.01 |

Pooled and median agree to within 2%, so no single large query is carrying the rate.

**The counterfactual**, at three bit-test costs, because the saving is `rate − bit_test_ns` and the answer
depends on that more than on anything else measured here:

| bit test | saves | of routed time | speedup on the population |
| --- | --: | --: | --: |
| 0.5 ns | 14.6 ms | 78.7% | **4.70×** |
| **1.0 ns** | **13.2 ms** | **71.3%** | **3.48×** |
| 2.0 ns | 10.5 ms | 56.5% | 2.30× |

So **~3.5×** end to end at a 1 ns bit test, and still 2.3× if the bit test costs twice that. The match loop
itself goes ~5.8× faster; the end-to-end figure is smaller because 14% of the query is not the loop.

### Where the old 8× came from, and why it is withdrawn

The all-trials view reproduces the original almost exactly — **1,070 ms over 101.9 M printings** here against
the doc's **1,213 ms over 145 M** — which identifies the original figure as counting **forced trials of plans
the router did not choose**. Those runs are real work, but production never pays them, and they examine 36×
more printings than the picked path (101.9 M against 2.8 M) because a forced plan often scans the whole
corpus.

Two things follow. The 8× was a **loop-time** ratio on a **non-production** population, so it overstated the
end-to-end win by ~2.3×. And the per-printing rate on that view is 10.50 ns against the picked path's 5.80 —
forced runs are slower per printing too, which is why the two views cannot be mixed.

The defect itself is unchanged, and the direction held.

### What this does not establish

- **`n.tight` is not observable from outside Rust**, and neither is the printing-space half:
  `narrowed_repr` is `None` *by construction* for a compose acquire, because `Prep::Range` and `Prep::Plane`
  "materialize no candidate list at all" — the narrowing happens at dispatch, after the router's acquire.
  So 896 is an **upper bound** on the addressable population; the gate may fire on fewer.
- **Uniform sampling.** 3.0% is a uniform-mode share, which over-samples rare shapes by construction. The
  realistic-traffic weight is unmodelled, the same caveat every `is:`/`frame:` figure carries.
- **The 1 ns bit test is an assumption**, not a measurement — hence the sensitivity table rather than one
  number. A kernel micro-benchmark of `bitmap_contains` over a 12 KB bitmap would settle it, and is the
  cheapest thing that would tighten this estimate.

### Reproducing

```bash
.venv/bin/python scripts/bench_membership_waste.py \
    --corpus benchmarks/bitplanes/corpus.jsonl --shm /tmp/membership.store --sample 30000
```

Observational, not an A/B — both inputs are counters `explain_analyze` has published since #833, so there is
no second build and no drift to control for. `ns_loop` is the fastest round's phase split, matching every
consumer's `min(trials_ns)`, so the rate is a floor. Delete the harness when this ships;
[the toolkit audit](local-benchmark-toolkit-audit.md) is a standing argument against keeping one-off benches.

## Verifying it

This changes what rows come back if it is wrong, so measure rows, not timings. The row-diff harness used
for the sparse-gather experiment captured totals and page rows over **127,640 compose-acquire queries**
and compared them before/after; it found 0 differences there and would find these. Timings second:
paired `bench_query_latency_ab.py`, interleaved.

The existential-plane gate above is the part most likely to produce wrong rows rather than slow ones, and it
is per-printing-varying — so verify returned row *identity* against real data across all three distinct-ons,
not just totals ([the repair pattern](reference-engine-printing-varying-plane-repair-pattern.md) is why).
