# The materializing plans re-derive membership compose already computed exactly

Status: **designed and measured, not implemented.** Filed as
[#856](https://github.com/jbylund/sylvan_librarian/issues/856).

**Worth ~6.5× on the population that pays it, which is 1.3% of realistic queries and `set:`-dominated.**
Both sides measured on `main` 2026-08-06, post-#833–#845: the residual costs **5.9 ns/printing**, the match
loop is **87% of routed time**, and the replacement costs **0.17 ns**.

**The recommended design changed as a result: use a two-pointer merge, not a bitmap.** The narrowing already
hands over a *sorted* candidate printing list, and the gather loop visits pids in ascending order, so no
bitmap is needed — and the merge is 5× cheaper than one (0.17 against 0.88 ns/printing) while skipping a
~0.5 µs/query scatter. See "Which structure to test against".

The earlier ~8× headline is withdrawn — it was a *forced-trial loop-time* ratio, not an end-to-end one.

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
population re-identified — see [What it is worth](#what-it-is-worth). Current figures: **87% of routed time
at 5.9 ns per printing against a 0.17 ns merge, ~6.5× end to end.**

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
membership structure in `narrow_candidates_exact` when `n.tight && printing_space`, carry it on
`PreparedCandidates`, and use it in the walk.

**Superseded in one respect: carry the sorted list, not a bitmap.** Both designs above assumed a bitmap and
`bitmap_contains`. The measurement below says to carry `Candidates::Printings` as it already is and merge
against it — 0.17 against 0.88 ns/printing, no allocation, and it is the structure `narrow_candidates_exact`
already produced. Keep the bitmap only as the `StreamedSelect` fallback, since a permutation walk cannot use
a forward pointer. That makes design **A** the smaller change *and* the faster one, which it was not before.

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
`scripts/bench_membership_waste.py` and `card_engine/src/bench_membership_bittest.rs`. 30,000 sampled
queries per run, Docker shut down.

**Read uniform for the rate and realistic for the share**, and do not mix them: uniform resolves the
per-printing rate better because it reaches rare shapes at all, while only realistic weights say how much
traffic this is. Both are labelled below.

**The population** (uniform). 2,495 of 30,000 are printing-mode on a compose acquire; **896 of those (36%)
route to a materializing plan** — 3.0% of the uniform sample. Every one picked `GatheredScan`;
`StreamedSelect` was never chosen here, so in practice this is `push_card_matches`, not `card_match_count`.

**The rate, and how much of the query it is** — uniform, two seeds.

| | seed 20260806 | seed 777 |
| --- | --: | --: |
| queries (picked, materializing) | 896 | 874 |
| routed time | 18.5 ms | 18.5 ms |
| of which match loop | 16.0 ms — **86.2%** | 16.0 ms — **86.6%** |
| printings examined | 2.8 M | 2.6 M |
| **ns per printing** | **5.80** | **6.11** |
| per-query median ns/printing | 5.81 | 6.01 |

Pooled and median agree to within 2%, so no single large query is carrying the rate.

### Which structure to test against

**`set:` already has the index this needs.** `indexes.set_codes` is a `TagIndex` in printing space — set
code → sorted printing ids — and `narrow_candidates_exact` turns `set:X` straight into
`Narrowed::tight(Candidates::Printings(...))`. So on the dominant family the exact answer is *already a
sorted `Vec<u32>`*, and `raw_candidates` still holds it at the point this change wants to capture something
(it is consumed by the flattening to card ids immediately after).

That makes a bitmap optional rather than necessary, and measuring both says skip it.
`card_engine/src/bench_membership_bittest.rs`, modelled on the measured production shape — 234 candidate
cards, 13.6 printings each, 1.56 matching — ns per printing in a visited span:

| matches/card | density | bitmap probe | **merge** | walk floor | bitmap build |
| --- | --: | --: | --: | --: | --: |
| 1 | 7% | 0.80 | **0.14** | 0.10 | 0.3 µs/query |
| **2 (measured 1.56)** | **14%** | **0.88** | **0.17** | 0.09 | **0.5 µs/query** |
| 4 | 29% | 0.97 | **0.22** | 0.09 | 1.7 µs |
| 7 | 50% | 0.64 | **0.31** | 0.09 | 4.4 µs |
| 14 | 100% | 0.98 | **0.64** | 0.08 | 8.5 µs |

The merge wins at every density, and for a structural reason rather than a constant-factor one: it is
**O(matches)** where the probe is **O(span)**, so it never touches the 86% of printings that do not match.
It also avoids allocating and zeroing the bitmap — 0.5 µs per query at production scale, which is ~2% of a
22 µs query.

**Soundness, and the one place it does not hold.** The merge needs the walk to visit pids in globally
ascending order. It does for `GatheredScan`: `cards_of_printings` yields
ascending cids, and each card's printing span is contiguous, so one forward pointer suffices.
**`StreamedSelect` walks a permutation in sort order, so the pointer would be wrong there and the bitmap is
the only option.** Every query measured on this population picked `GatheredScan`, so the merge covers the
measured case and the bitmap is the fallback for the plan that was never chosen.

**Two corrections this measurement forced**, recorded because both produced believable wrong answers:

- A counting kernel (`if contains { hits += 1 }`) reads a flat 0.4 ns at *every* density, because the
  compiler predicates it into `hits += contains as u32` and vectorizes. Density-flatness was the tell — an
  unpredictable branch has to cost something. The real loop conditionally *appends*, which cannot be
  predicated.
- An earlier model strided over *all* cards, which made the merge advance its pointer through pids belonging
  to skipped cards and read 3.75 ns instead of 0.17. That cannot happen: `card_ids` is derived **from** the
  candidate list, so every visited card holds at least one match and the list has no pid outside a visited
  span. The same model also used the corpus-average 3.09 printings per card where visited cards really hold
  13.6, understating span work 4×.

**So the counterfactual, anchored on both measurements** — realistic mode, 377 queries, 8.2 ms routed of
which 7.1 ms (87.1%) is the match loop:

| membership check | saves | of routed time | speedup on the population |
| --- | --: | --: | --: |
| **0.17 ns — merge (recommended)** | **6.9 ms** | **84.5%** | **6.47×** |
| 0.88 ns — bitmap probe | 6.1 ms | 73.9% | 3.84× |
| 2.0 ns — control, 2× worse than the bitmap | 4.7 ms | 57.2% | 2.34× |

**~6.5×** with the merge, ~3.8× with a bitmap, and a 2.3× floor even at a check cost nobody measured.

### Which queries this touches

The question the population share has to answer, since a rate alone does not justify the work. Under
**realistic** family weights, by share of printings examined:

| family | share | n | median density |
| --- | --: | --: | --: |
| `set:` | **63.2%** | 153 | 7.9% |
| `f:` + `set:` | 6.2% | 16 | 7.3% |
| `r:` | 4.3% | 14 | 10.5% |
| `set:` + `usd:` | 3.0% | 7 | 4.9% |
| `keyword:` | 2.8% | 77 | 100.0% |
| `date:`/`tix:`/`cn:`/`eur:`/`year:` + `set:` | ~8% | 18 | 0–15% |

**This is a `set:`-dominated change.** That is not a coincidence: `set:` and `watermark:` are the
*exact-postings* compose leaves added by #748 (index #739), and exact postings are what make the narrowing
tight — which is precisely #856's gate. The families that reach it are the ones whose narrowing can be
tight.

Note `watermark:` is 17.3% under **uniform** sampling and falls out of the top ten under realistic weights,
where it carries 0.5 against `set:`'s 5. Read the uniform run for the rate and the realistic one for the
share.

Two consequences worth stating plainly:

- **`f:` queries benefit only partly.** Legality is an existential plane, and the gate below excludes those
  from the complete-bit-test case — the per-printing plane check still runs.
- **Text predicates are not in this population at all.** `name:`/`oracle:`/`flavor:` are not
  compose-composable ([#731](00731-engine-compose-universal-evaluator.md) step 3, not started), so a query
  like `name:s` never reaches this path; and where a text leaf rides *alongside* a composable one it is the
  residual, which makes the narrowing not-tight, so the gate does not fire either. #856's design B cites
  `memoize_text_predicates` as a mechanism precedent, which is a different thing from sharing its target.

**Scale:** 1,116 of 30,000 realistic-sampled queries are printing-mode on a compose acquire, and 377 of
those (34%) route to a materializing plan — **1.3% of queries**. A 5× win on 1.3% of traffic is roughly a
4% aggregate effect, and the honest case for doing it is the per-query tail rather than the mean.

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
- **Both checks are measured (0.17 / 0.88 ns), but on synthetic spans.** `bench_membership_bittest.rs`
  builds spans and candidate lists synthetically, so it captures the access pattern, the branch behaviour and
  the O(matches)-vs-O(span) difference, but not cache competition: in the real loop these structures share L1
  with `APrinting` rows being streamed. Both figures are therefore floors, which is why the control row at
  2.0 ns is kept — the change still returns 2.3× at a cost an order of magnitude above the merge's.
- **Span placement is idealised.** Visited cards are spread evenly through the corpus; real candidate cards
  cluster (a `set:` is a release, and printings of one release sit near each other in pid order). Clustering
  helps both routes and helps the merge more, so this biases against the recommendation rather than for it.
- **The density figure was a pooled mean and is now a distribution.** 8.7% pooled (uniform) hid a bimodal
  spread: under realistic weights p10/p50/p90 is 4% / 10% / 100%, with 96 of 377 queries above 80% density
  and only 32 in the expensive 20–80% band. A mean is the wrong summary for a non-monotonic cost curve — two
  queries at 1% and 99% average to the worst case while both are cheap.

### Reproducing

```bash
# The residual side, the population, and which families reach it. --mode realistic for the share.
.venv/bin/python scripts/bench_membership_waste.py \
    --corpus benchmarks/bitplanes/corpus.jsonl --shm /tmp/membership.store --sample 30000 --mode realistic

# The bit-test side, swept over candidate sparsity and match density.
cargo test --release bench_membership_bittest -- --ignored --nocapture
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
