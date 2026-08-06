# The materializing plans re-derive membership compose already computed exactly

Status: **designed and measured, not implemented.** Filed as
[#856](https://github.com/jbylund/sylvan_librarian/issues/856).

**Worth ~6.5× on the population that pays it — but that is 1.22% of routed time and contains no slow
queries, so this is a ~1% aggregate rather than a latency fix.** `set:`-dominated. See
[It touches no slow queries](#it-touches-no-slow-queries-and-that-is-structural) before citing the ratio.
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
population re-identified — see [What it is worth](#what-it-is-worth). Current figures: **87% of routed time at
5.9 ns per printing, against 0.88 ns for this route's bitmap probe — 3.84× end to end.**

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

**Where the sorted list beats both:** for `GatheredScan` the candidate list can be merged against directly,
with no bitmap — cheaper and simpler, and split out as
[#857](00857-engine-membership-merge-sorted-list.md). The designs above remain the right shape for this route,
whose job is the visit orders #857 cannot serve.

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

### What the bitmap probe costs

`card_engine/src/bench_membership_check.rs`, modelled on the measured production shape — 234 candidate cards,
13.6 printings each, 1.56 matching. ns per printing in a visited span, plus the per-query cost of building the
bitmap before anything can be probed:

| matches/card | density | **bitmap probe** | bitmap build |
| --- | --: | --: | --: |
| 1 | 7% | 0.33–0.80 | 0.3 µs/query |
| **2 (measured 1.56)** | **14%** | **0.38–0.88** | **0.3–0.5 µs/query** |
| 4 | 29% | 0.52–0.97 | 0.9–1.7 µs |
| 7 | 50% | 0.64–1.09 | 3.5–4.4 µs |
| 14 | 100% | 0.42–0.98 | 5.5–8.5 µs |

**Ranges, not points, and deliberately so.** These kernels run at 0.1–1 ns per operation, where run-to-run and
build-to-build variation is a large fraction of the value: the production row moved 0.88 → 0.38 between two
versions of the bench that did not touch the kernel, then held at 0.37–0.43 across three consecutive runs. So
the absolute level is soft. What is robust is the **ratio** against the merge, which reads 5.0× in the first
run and 5.4× in the second — and the fact that both routes are an order of magnitude under the 5.9 ns residual
they replace. Every figure quoted elsewhere in this doc uses the **pessimistic end (0.88)**, so the speedups
are conservative.

The same bench measures the [#857](00857-engine-membership-merge-sorted-list.md) merge alongside it and finds
it 5× cheaper (0.17 ns) with no build cost, for the structural reason that it is O(matches) where this is
O(span). This route earns its place on **reach**, not cost: it needs nothing of the visit order, so it serves
`StreamedSelect`'s permutation walk, which the merge cannot.

### For permutation order, scatter — do not sort

A natural idea for the `StreamedSelect` case is to make a forward pointer work anyway: key each candidate pid
by its card's position in the sort permutation (`inv_perm[printing_to_card[pid]]`), sort, then merge during the
walk. Measured, it does not pay — the two per-query setups, at the production point:

| setup | cost/query |
| --- | --: |
| scatter candidates into a printing bitmap | **0.2–0.3 µs** |
| key by permutation position and sort | **2.4–2.9 µs** |

An order of magnitude apart, and it widens with density (5.5 µs against 23.2 µs when every printing matches).
The reason is structural: the scatter is O(k) with one store each, while the sort is O(k log k) with **two
dependent random loads per element** to compute the key. On a ~22 µs query, 2.9 µs of setup would eat most of
what the change wins.

**The right shape for permutation order is a scatter too, just into permutation space** — and the engine
already does it. `run_query_streamed_popcount` scatters the match set through `inv_perm` and walks words at 64
cards a load.

Its limitation is the thing to extend: it runs only for `unique=card` with a `FilterExpr::True` residual, i.e.
where the plane bitmap already *is* the match set. Carrying a real match set through `inv_perm` is what this
route needs, and it needs per-card counts for the skip since a popcount counts cards and not matches. That
extension is tracked at [#730](00730-engine-popcount-skip-walk.md) — whose own subject is deep pagination,
with this as a **second consumer** of the same machinery — and it is also item 3 of
[#852](00852-engine-compose-acquire-p3-p4-ranking.md)'s carried-forward list. #730 has the sort-vs-scatter
numbers recorded so they are not re-derived there.

**One trap, recorded because it produced a believable wrong answer.** A counting kernel
(`if contains { hits += 1 }`) reads a flat 0.4 ns at *every* match density, because the compiler predicates it
into `hits += contains as u32` and vectorizes. Density-flatness was the tell — an unpredictable branch has to
cost something. The real loop conditionally *appends* a match, which cannot be predicated, and once the kernel
does that the expected inverted-U appears. Both kernels are kept in the bench, the counting one labelled as the
trap.

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
those (34%) route to a materializing plan — **1.3% of queries**, and **1.22% of all routed time**. The
change saves **1.03% of total routed time**.

### It touches no slow queries, and that is structural

An earlier draft of this doc claimed "the honest case for doing it is the per-query tail rather than the
mean." **That was asserted rather than measured, and it is false.** Measured routed time, realistic mode:

| | p50 | p90 | p99 | max |
| --- | --: | --: | --: | --: |
| all sampled | 10.6 µs | 49.4 µs | **215.8 µs** | **1,132.6 µs** |
| **this population** | 28.3 µs | 36.4 µs | 64.5 µs | **78.5 µs** |

The population's *maximum* is about the overall *p90*. The engine's actual slow tail — 216 µs at p99, 1.1 ms
worst — lies entirely outside it. These queries are consistently ~3× the median and never in the tail. The
largest per-query win available is 78.5 µs → 9.2 µs, which no user can perceive.

**And that is not luck.** This population is compose-acquired queries whose narrowing came back *tight*, and
a tight narrowing is precisely what makes a query fast. The defect makes already-fast queries slower than they
should be; it cannot produce a slow one. So the case is a ~1% aggregate, not a latency fix, and anything
claiming otherwise should be checked against the table above — `scripts/bench_membership_waste.py` prints it.

The slowest rows, for reference:

```
   78.5us ->    9.2us  (8.5x)  border:white f:historic cn<217
   77.0us ->   12.7us  (6.1x)  usd>=0.15 usd<=0.15 f:pioneer
   71.2us ->   14.6us  (4.9x)  eur:0.09 f:pauper
   46.1us ->    2.2us  (20.5x) tix>=0.03 tix<=416.84 set:neo
```

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
