# Measuring P3 and P4's loop and finish phases with built designs

Branch `engine-compose-feature-accuracy`. Companion to
[reference-cost-model-measurement.md](./reference-cost-model-measurement.md), which covers which tool
answers which question; this covers what the tools said about `StreamedSelect` (P3) and
`GatheredScan` (P4), and what is left.

The earlier campaign in [local-engine-cost-model-agreement.md](./local-engine-cost-model-agreement.md)
fitted rates from sampled traffic. This one built designed experiments instead, because several rates
are **unidentifiable from traffic at any corpus size** — and that turned out to be the least
interesting thing it found.

## The one rule that came out of it

**Shape from a built design; levels from traffic.** Not a preference — six refits of kernel-measured
levels each made routing worse, and the two changes that shipped were both cases where a design found
a *missing term* and traffic then set its rate.

Built designs win on shape because the experimenter controls the ratios. Traffic wins on levels
because it has production's cache state, query mix and interleaving, and every attempt to substitute
a measured level for a fitted one has failed.

## What shipped

Two terms, both found by a design, both levelled by traffic, both mirror-verified at 100% and gated
interleaved A/B/A/B.

**`STREAM_PERM_STEP_NS`** — P3's permutation walk was not charged at all. The walk steps until the page
fills, so it visits ~`page_span × n_cards / matches` entries: inversely proportional to selectivity, and
proportional to nothing else in `PlanFeatures`. Against a fixed 1,500 matches the arm predicted ~397 ns
while the walk cost 1,333 / 3,791 / 10,458 ns at 31.5k / 126k / 410k cards — under by 3.4× at the
production corpus and 26× at 410k. Explains P3's finish phase grading mean |log| 2.06 while carrying 12%
of all measured nanoseconds. Rate 1.0 from the kernel, 1.15 from traffic. **Regret neutral.**

**`GATHER_COLLECT_PER_PAGE_ROW_NS`** — P4's finish phase was charged on `page_span` alone, which a
four-row page sweep falsifies outright:

| offset | limit | page_span | ns_finish |
| --: | --: | --: | --: |
| 0 | 15 | 15 | 125 |
| 0 | 60 | 60 | 584 |
| 0 | 600 | 600 | 16,375 |
| 900 | 60 | 960 | 11,250 |

`page_span` 960 costing less than 600 is impossible under one column: the quickselect scales with
`offset + limit`, the collect with the page returned. Level 9.79 from traffic (the sweep said ~15).
**Neutral to marginally negative** — taken for correctness, since a one-column model four rows disprove
should not survive on a neutral gate.

**The emission walk's match span.** The walk started at permutation index 0 and stopped only when the
page filled, so it stepped every entry ordered before the first match (`cmc>=6 order=cmc asc`: 28,275
entries for a 60-row page, 10.6 us of a 20.25 us execution) and, on any page it could not fill, every
entry after the last match. Bounded to `min..=max` of `inv_perm` over matching cards — realized, so it
holds for any predicate and cannot be wrong about tie-breaking — the same query walks 60 entries in
0.38 us. Row identity verified against `GatheredScan` on an anti-correlated corpus at four offsets in
every mode and direction, because a bound one entry off drops a row rather than crashing.

**Gated**, at candidates ≤ ¼ of the corpus, because the span costs 0.51 ns per matching card against a
match loop that is ~2.6 ns/card: ungated it took `cmc>=1 order=edhrec`'s `ns_loop` from 78.54 to 94.46
us while its walk stayed at 111 steps. The bound can save at most `(n_cards − m) × 0.37 ns` against
`m × 0.51 ns`, so worst-case break-even is 0.42 × `n_cards` and the gate sits inside it.

| exec, span on/off (geometric mean) | cells |
| --- | --- |
| clustered (predicate correlates with orderby) | **0.660** over 8 |
| broad (gate off — identical path) | 1.000 over 4 |

Pooled over 419 sampled `StreamedSelect` queries: no detectable difference, −0.10 us, CI [−0.62, +0.41].

## Measuring this needed one binary, not two

`scripts/bench_walk_span.py` A/Bs `CARD_ENGINE_SPAN_TRACK_CANDIDATE_DIVISOR` in interleaved
subprocesses of a single build, with equal-length env values. That is not fastidiousness — a
cross-build attempt got the sign wrong:

- `ns_loop` wandered 43.00 / 45.38 / 47.17 us across three runs on a phase neither build changed, ±9%
  in both directions. A 0.5 ns/card effect is invisible under that.
- `bench_plan_execution_ab.py` across builds called the change 2% **slower** — while its own acquire
  control, which the change cannot touch, moved 1.9% the same way. Within one binary the control is
  flat (+0.01 us, median ratio 1.000) and the verdict is no detectable difference.

Both are the failure the toolkit already documents (`min` is a floor estimator; its error is
common-mode within a run, so pairing does not cancel it). The toggle removes it by construction:
identical code, identical layout, one process each.

## Regrading the perm estimate

`perm_steps` realized/estimated, same seed and sample length, ~12.3k walking rows:

| walk | p10 | median | p90 |
| --- | --: | --: | --: |
| unbounded | 0.13 | 1.00 | 6.43 |
| bounded, gated | 0.09 | 0.94 | 5.07 |
| bounded, ungated | 0.08 | 0.90 | 4.26 |

Three things fall out of that table.

- **A third of the p90 tail was the leading prefix**, and it is gone. p90 is the late-cluster case, so
  this is the estimate's largest error being deleted rather than modelled — which was the point of
  doing (1) before (2).
- **The rate did not move.** Traffic fits `PERM_STEP` at 1.17 before and 1.15–1.17 after. Deleting a
  third of the steps at the tail without moving the per-step rate is what a real per-unit cost looks
  like, as against a coefficient absorbing its feature's error — the failure mode `GATHER_FIXED_COST_NS`
  turned out to be. Left at the shipped 1.0; no refit.
- **The cost model prefers ungated and the runtime prefers gated.** Recorded, not resolved: the
  mechanism that ends the tension is a predicate-derived start position (below), which costs nothing per
  card and so needs no gate.

What remains at p90 5.07 is **interior** to the span — non-matching entries between matches, which no
start position can skip. Different mechanism, and one the engine already owns elsewhere: the
popcount-skip walk.

## What was declined, and why that is the more useful result

**`GATHER_FIXED_COST_NS` (169.6, traffic says 85).** Looked like the cheapest win available: a 2×
disagreement with no shape question. Measuring the intercept from cells differing *only* in card count
gives **card −1,084 ns, printing −845 ns**. A negative fixed cost is impossible, so the linear-in-cards
shape is wrong — the loop is **convex** in card count (12.40 ns/card across 400–4,500 against 6.31–7.67
over 1,500–4,500). A straight line through a convex curve drives its intercept negative, and a
whole-arm fit puts that same curvature in its intercept.

So 85 is curvature compensation, not a fixed cost. Two consequences:

- **Every plan's `FIXED` is its arm's error sink.** `fit_cost_model.py` fits one equation per query
  against total dispatch, so no plan's fixed cost should be read off it without an independent intercept
  measurement. `STREAM_FIXED_COST_NS` (217, fitted 192) is unexamined on this basis.
- The convexity **is** the corpus-size effect below. P4's `FIXED` disagreement and corpus drift are one
  problem.

## Retractions

Recorded because both were believed and acted on:

- **"The shipped constants are ~2× too high."** They are not. `ITERS` walked one card list repeatedly
  and kept the minimum, so every rate was warm-cache; the first pass costs 1.6–2.2× more. Cold and
  rotated at production corpus size, P4 reads LOOP 6.27 against a shipped 6.88 and SCAN 2.27 against
  2.06. Fixed by chunk **rotation** plus per-cell **stagger** — both needed, since rotation alone left
  cells sharing a chunk, one paying all the misses (10.50 vs 6.11 ns/card on identical cards).
- **"The error is in the features."** No. Every counter/feature ratio reads 1.00 for both plans. The
  1.00s had already ruled it out.
- **"`STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS` is 24–40× too high."** No — those readings came from cells
  in the *perm-walk* branch. 600-card cells reach the gather branch and measure 1.075–1.250 against the
  shipped 1.02. Confirmed, not changed.

## Structural findings that outlive the constants

- **Degenerate columns.** No single `unique` mode identifies P4's three loop rates: card mode pushes once
  per card (`matches == cards`), printing mode pushes every printing (`matches == printings`). Pooling
  separates them but averages over the mode differences being measured. Resolved by fitting each mode in
  the two-parameter space it supports and recovering the three from the pair of pairs — which yields
  `PUSH` twice over (1.24 and 0.88, agreeing to 1.41×) as a linearity check.
- **P3's loop is linear** in its two counters to 1.01×, cross-checked from different cells and columns.
- **P3's gating is right**: zero printings examined under `all_match` at every printings-per-card level.
- **Artwork is a different loop**, needing its own per-card and per-printing rates in P4 (6.57 / 1.09
  against 2.98 / 2.36) while sharing the push. Its surcharge *flips sign* with printings-per-card, so no
  additive correction can express it. Measured, shape-confirmed, **never gated on its own.**
- **Rates are corpus-size dependent, and differently per plan** — over 31.5k → 410k cards, P3's
  `all_match` per-card is flat (2.58 / 2.54 / 2.55) while its residual per-card grows 3.6× and P4's loop
  grows 2.4×. So the P3/P4 balance drifts as the corpus grows with every constant left alone.

## Tooling added

- `card_engine/src/bench_gather_loop.rs`, `bench_streamed_loop.rs` — call the real executors and read
  `PhaseStats`, so nothing is reimplemented. Chunk-rotated, per-cell staggered, median over rotations.
  `BENCH_LOOP_STORE` selects the store.
- `scripts/upscale_corpus.py` — replicates the real corpus, rewriting `oracle_id` / `scryfall_id` /
  `illustration_id` and suffixing names, leaving everything cost-relevant alone. Needed because the real
  corpus gives the wide group one chunk at 4,500 cards. **The 126k and 410k stores are ~1.4 GB in
  `benchmarks/loop-scale/` — delete when done.**
- `scripts/bench_walk_span.py` — A/Bs the walk's span bound through
  `CARD_ENGINE_SPAN_TRACK_CANDIDATE_DIVISOR` inside one binary, clustered cells against broad controls.
  The pattern to copy for any change whose effect is smaller than the cross-build floor error: one build,
  a runtime toggle, interleaved subprocesses, equal-length env values.
- `perm_steps` published on `PhaseStats` and graded in `fit_cost_model.py`.

## What is left

1. **A predicate-derived start position, which would cost nothing per card.** The permutation IS a sorted
   array on `(sort key, edhrec, cid)`, so when the filter bounds the *sort column* — `cmc>=6` under
   `order=cmc`, exactly the correlation that makes a cluster — a binary search for the first position whose
   value satisfies the bound gives a start in O(log `n_cards`) once per query, against 0.51 ns × matching
   cards for the realized span. Seek to the START of the boundary tie block and it can only start too
   early, never too late, which answers the tie-breaking objection that made the realized span the first
   choice. It composes with the span rather than replacing it (free ⇒ no gate ⇒ available to broad queries
   too), and it is what would let the cost model have the ungated column of the regrade table above.
   Conjunction analysis to extract the bound, plus row-identity verification, since it touches paging again.
2. **Extend the popcount-skip walk past `FilterExpr::True`.** What is left of the perm estimate's p90 is
   non-matching entries *interior* to the match span, and a start position cannot reach those by
   construction. Scattering the match set through `inv_perm` and walking words at 64 cards a load is the
   mechanism that can — `run_query_streamed_popcount` already does it for `unique=card` queries whose
   filter fully consumed to `True`. Generalizing it needs per-card match counts for the skip (a popcount
   counts cards, not matches, so printing/artwork mode cannot skip by popcount alone) and pays the same
   per-card scatter the span bound pays, so it wants the same gate or the free start position above.
3. **A curvature term for the loop rates** — the cause of both the `FIXED` disagreement and corpus drift.
4. **Gate P4's artwork arm on its own.**
5. Carried over: make the fit loss the actual multi-way routing outcome rather than a pairwise proxy;
   bitmap+extract candidate materialization in the 64–4,095 band; the 4096+ prep band.
