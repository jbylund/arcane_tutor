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

Two cost terms — both found by a design, both levelled by traffic — and one executor change, all
mirror-verified at 100% and gated interleaved A/B/A/B.

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

**The emission walk starts and ends at the filter's bound on the sort column.** The walk began at
permutation index 0 and stopped only when the page filled, so `cmc>=6 order=cmc asc` stepped 28,275
entries to fill a 60-row page — 10.6 us of a 20.25 us execution — and any page it could not fill also ran
past the last match to the end of the corpus. The permutation is a sorted array on `perm_primary_key`, so
an interval on the sort column's *value* is a contiguous range of *positions*: two binary searches, O(log
n_cards) probes once per query, nothing per card. The same query in printing mode goes 18.29 → 8.71 us,
stepping 4 entries.

| exec, bound on/off (geometric mean) | cells |
| --- | --- |
| clustered, `StreamedSelect` routed | **0.603** over 16 |
| broad control, routed | 1.006 over 6 |

Conservative in three directions, since a wide bound costs steps and a narrow one returns the wrong page:
`And` only (an `Or` arm can match outside its sibling's bound, and `Not` inverts into a union of two
intervals), strict comparisons widened to inclusive, and only the three columns that have numeric
predicates at all. Extracted in `bind_and_split_filter` because `split_planes` compiles `cmc>=6` into mask
algebra and leaves `FilterExpr::True` behind, then carried on `QueryParams` with `UNBOUNDED` as the
default — a path that does not extract a bound walks everything, which is slower and never wrong.

### The realized span it replaced, and why the replacement is not free of interest

The first version tracked `min`/`max` of `inv_perm` over matching cards: realized, so it caught clustering
from *any* source rather than only what the predicate names. It measured 0.51 ns per matching card against
a match loop whose `all_match` arm is ~2.6 ns/card — a 20% tax on `ns_loop`, which forced a gate at ¼ of
the corpus, which still let unlucky cells (`c:r`, and any cluster sitting at the *near* end of the walked
order) pay a premium for nothing. The bound dominates it on every axis:

| | tracked span + gate | sort-column bound |
| --- | --: | --: |
| clustered, routed | 0.689 | **0.603** |
| broad control, routed | 1.034 | **1.006** |
| worst near-end cell | 1.244 | **1.018** |

What the realized span still has is reach: see the regrade below.

### Card mode's win is latent, and only the mode sweep says so

In `unique=card` a plane-consumable predicate leaves `FilterExpr::True`, which makes `PlanePopcountOrder`
applicable — and the router picks it for exactly these clustered queries (`cmc>=6 order=cmc`: 2.46 us
against P3's 12.38). So the card-mode cells improve a plan nobody runs. Printing and artwork mode have no
popcount plan, because a popcount counts cards and not printings, so P3 is picked there and the
improvement is what a user waits for. `bench_walk_span.py` prints the routed plan per cell for this
reason; the first version of that harness measured card mode only and would have overclaimed.

## Measuring this needed one binary, not two

`scripts/bench_walk_span.py` A/Bs `CARD_ENGINE_WALK_SORT_BOUND` in interleaved subprocesses of a single
build, with equal-length env values. That is not fastidiousness — a cross-build attempt got the sign
wrong:

- `ns_loop` wandered 43.00 / 45.38 / 47.17 us across three runs on a phase neither build changed, ±9% in
  both directions. A 0.5 ns/card effect is invisible under that.
- `bench_plan_execution_ab.py` across builds called the change 2% **slower** — while its own acquire
  control, which the change cannot touch, moved 1.9% the same way. Within one binary the control is flat
  (median ratio 1.000) and the verdict is no detectable difference on 419 paired queries.

Both are the failure the toolkit already documents (`min` is a floor estimator; its error is common-mode
within a run, so pairing does not cancel it). The toggle removes it by construction: identical code,
identical layout, one process each.

## Regrading the perm estimate

`perm_steps` realized/estimated, same seed and sample length, ~12.5k walking rows:

| walk | p10 | median | p90 |
| --- | --: | --: | --: |
| unbounded | 0.13 | 1.00 | 6.43 |
| sort-column bound (shipped) | 0.11 | 0.96 | 5.31 |
| realized `inv_perm` span (not shipped) | 0.08 | 0.90 | 4.26 |

Three things fall out of that table.

- **A fifth of the p90 tail was the prefix a bound can name**, and it is gone. p90 is the late-cluster
  case, so this is the estimate's largest error being deleted rather than modelled — the point of doing
  (1) before (2).
- **The rate did not move.** Traffic fits `PERM_STEP` at 1.17 before and 1.19 after. Deleting a fifth of
  the tail without moving the per-step rate is what a real per-unit cost looks like, as against a
  coefficient absorbing its feature's error, which is what `GATHER_FIXED_COST_NS` does. Left at the
  shipped 1.0; no refit.
- **The third row bounds what any start position can do.** A realized minimum reached 4.26 because it sees
  clustering the predicate never mentions (`o:flying` correlates with cmc; a name prefix correlates with
  name order). That gap is now a measured property of the two mechanisms, not a guess — and it is the
  argument for the popcount-skip walk, which needs no bound at all.

What remains at p90 5.31 is partly that, and partly non-matching entries **interior** to the walked
segment, which no start position can skip by construction.

## What was declined, and why that is the more useful result

**`GATHER_FIXED_COST_NS` (169.6, traffic says 85).** Looked like the cheapest win available: a 2×
disagreement with no shape question. It is still declined, but the first reason given for declining it was
wrong and is retracted below.

The design now runs four card counts — 100 / 400 / 1,500 / 4,500 — and the average per-card cost is
**U-shaped**, not monotone:

| single-printing card cell, ns/card | 100 | 400 | 1,500 | 4,500 |
| --- | --: | --: | --: | --: |
| A | 11.25 | 9.16 | 9.11 | 10.65 |
| A′ (same shape, different chunk stagger) | 10.42 | 10.00 | 8.69 | 11.65 |

Two effects, at opposite ends. A per-query **fixed cost** is only visible at the small end (at 4,500 cards
a 170 ns constant is 0.04 ns/card, far under the cell-to-cell spread), and **cache pressure** raises the
marginal rate at the large end. Solving each end separately, over four single-printing cells and
reproducible to ±0.01 across runs:

| | marginal ns/card, 100→400 | marginal ns/card, 1,500→4,500 | curvature | fixed cost |
| --- | --: | --: | --: | --: |
| card | 7.92 | 10.93 | **1.38×** | 250 |
| printing | 8.19 | 11.22 | **1.37×** | 98 |
| artwork | 11.11 | 10.50 | 0.94× | 222 |
| card (stagger control) | 9.30 | 12.88 | **1.38×** | 29 |

So the fixed cost measures **29–250 ns**, which brackets both the shipped 169.6 and the fitted 85 — and
that spread, wider than the disagreement being adjudicated, is why the refit stays declined. The
curvature is the finding: 1.37–1.38× on card and printing, reproducible, and the arm has no term for it.
Artwork shows none, which is consistent with it being a different loop.

**Retracted: "the measured intercept is negative, so the fixed cost is impossible and 85 is curvature
compensation."** The negative intercept (−864, −1,078, −1,616 in the table the bench still prints) is an
artifact of fitting ONE line across a range where a fixed cost dominates one end and cache pressure the
other; least squares weights the 4,500-card cell by `n²`, so the line tilts to the large end and its
intercept dives below zero. Adding a 100-card cell and solving the ends apart removes it. The earlier
run's "card −1,084 / printing −845" also crossed cell boundaries — it paired cell `A` at 400 cards with
`A′` at 4,500 — so up to half its magnitude was chunk-stagger noise.

What survives, on its own evidence rather than on that one:

- **Every plan's `FIXED` is still its arm's error sink.** `fit_cost_model.py` fits one equation per query
  against total dispatch, and `FIXED` is the only term with no feature attached. It read 84 and then 85
  while `COLLECT_PER_PAGE_ROW` moved 15.0 → 9.79 underneath it: a term that does not budge when a
  neighbour changes by 35% is absorbing error, not measuring a cost. `STREAM_FIXED_COST_NS` (217, fitted
  192) is unexamined on this basis.
- **The curvature is real and unmodelled**, at 1.38× within one store — and the corpus-size effect below
  is the same phenomenon at a larger scale. Those two are one problem; the fixed cost is not part of it.

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

### The drift SATURATES, which three corpus sizes could not show

Five log₂-spaced stores (31.5k / 63k / 126k / 252k / 504k cards, built by `upscale_corpus.py` at
1/2/4/8/16 copies) against the previous 1×/4×/13×. The per-card loop rate, measured directly per cell —
no differencing across modes, which is where `PUSH` goes unidentified:

| store, oracle cards | 31.5k | 63k | 126k | 252k | 504k |
| --- | --: | --: | --: | --: | --: |
| A card, ns/card | 10.15 | 13.23 | 20.66 | 24.54 | **24.72** |
| B printing | 10.64 | 14.47 | 22.94 | 25.75 | **25.95** |
| E artwork | 10.96 | 15.99 | 29.90 | 33.01 | **33.19** |
| A′ card (stagger control) | 11.70 | 11.90 | 19.06 | 24.96 | **25.40** |

It is not a drift that keeps going: the rate roughly doubles to 126k, adds ~19% to 252k, and then stops
— all four cells move under 2% between 252k and 504k. That is a cache-residency curve reaching its
asymptote, and it changes both the shape to fit and the urgency:

- **The term is bounded, not logarithmic.** Two levels — a resident rate ~10–11 ns/card and a
  non-resident rate ~25 ns/card — with a knee between 126k and 252k cards on this machine. A `log(n)`
  term fitted to three points would have extrapolated past the plateau and over-costed a large corpus.
- **Production sits at the bottom of the curve** (31.5k cards, ~10 ns/card). So the constants are
  calibrated in the resident regime and the drift is insurance against a ~4× corpus, not a routing defect
  today. That demotes this from "the biggest single item" to "the best-understood one".
- **The two axes are one mechanism.** The within-store curvature (100 → 4,500 cards visited) shrinks as
  the store grows — 1.33–1.36× at 31.5k, 1.07–1.15× at 504k — which is what a single residency curve
  predicts: once every access misses, visiting more cards cannot make it worse. So a curvature term keyed
  on working-set residency should subsume both, rather than needing one term per axis.
- **The "fixed cost" is still not identified.** It reads 14–305 ns at 31.5k and 305–499 ns at 504k. A
  per-query constant cannot depend on corpus size, so something size-dependent is still leaking into that
  end of the fit, and the `169.6`-vs-`85` question stays open.

## Tooling added

- `card_engine/src/bench_gather_loop.rs`, `bench_streamed_loop.rs` — call the real executors and read
  `PhaseStats`, so nothing is reimplemented. Chunk-rotated, per-cell staggered, median over rotations.
  `BENCH_LOOP_STORE` selects the store.
- `scripts/upscale_corpus.py` — replicates the real corpus, rewriting `oracle_id` / `scryfall_id` /
  `illustration_id` and suffixing names, leaving everything cost-relevant alone. Needed because the real
  corpus gives the wide group one chunk at 4,500 cards. **The 126k and 410k stores are ~1.4 GB in
  `benchmarks/loop-scale/` — delete when done.**
- `scripts/bench_walk_span.py` — A/Bs the walk's sort-column bound through `CARD_ENGINE_WALK_SORT_BOUND`
  inside one binary, clustered cells against broad controls, sweeping `unique` so a latent win reads as one.
  The pattern to copy for any change whose effect is smaller than the cross-build floor error: one build,
  a runtime toggle, interleaved subprocesses, equal-length env values.
- `perm_steps` published on `PhaseStats` and graded in `fit_cost_model.py`.

## Where the routing loss actually is (2026-08-03, after the regret netting fix)

With both sides of regret priced the same way, the largest single cell is `printing_compose / artwork` at
**32% of all lost time** (n=1,435, mean 8.24 µs, max 904). Chased to the bottom, it is one feature defect:

**Every bare format predicate mis-picks, in artwork mode only.** `f:predh` −55 µs, `f:gladiator` −75,
`f:modern` −33, `f:standard` −31, `legal:pauper` −30, and so on for nine of twelve shapes probed. Card
mode picks `PlanePopcountOrder` and is right; printing mode picks `PrintingCompose` and is right; artwork
mode picks `PrintingCompose` when `StreamedSelect` is 1.8× faster. Compounds (`f:modern t:creature`) route
to a candidates acquire and are fine.

**Compose is not the problem — it is priced accurately.** On `f:gladiator` artwork, predicted/measured
reads 0.98 for compose and **6.87 for StreamedSelect**; on `f:modern`, 1.10 and 5.70. The router is not
over-confident about compose, it is over-charging P3 by ~6×, and `scan_units × STREAM_SCAN_PER_ROW_NS`
alone is 525 µs of P3's 704 µs prediction against a 91 µs measured loop.

**The feature overstates P3's work by 13–15×.** Graded against realized `printings_examined`:

| query / mode | plan | `scan_units` | examined | ratio |
| --- | --- | --: | --: | --: |
| f:gladiator / artwork | GatheredScan | 88,026 | 54,213 | 1.62 |
| f:gladiator / artwork | StreamedSelect | 88,026 | **5,876** | **14.98** |
| f:modern / artwork | GatheredScan | 101,716 | 73,783 | 1.38 |
| f:modern / artwork | StreamedSelect | 101,716 | **7,770** | **13.09** |

Both plans receive the same per-query `scan_units`, but P3 examines 9× fewer printings. The reason is NOT
the first-match break that the plane branch corrects for — sweeping ten composable filters shows the
defect is confined to **legality**:

| composed filter | P3 examined / P4 examined |
| --- | --: |
| `f:modern`, `f:commander`, `f:vintage`, `legal:pauper`, `f:standard`, `t:goblin or f:legacy` | **0.10 – 0.26** |
| `border:black` | 1.00 |
| `r:mythic` | 1.00 |
| `watermark:riveteers` | 1.00 |

For every other printing-varying leaf the two plans examine *identically* and `scan_units` is right to
within 1.1–2.4×. Legality differs because `card_pass` resolves it at CARD level for every non-divergent
card: `Tri::True` or `Tri::False` comes back without a printing being read, so `card_match_count` answers
from span arithmetic and P3's per-printing cost exists only for the divergent subset. P4 has no such
escape — `push_card_matches` must walk the span to push every match.

The implied estimate is exact rather than fitted, because the engine already holds the set:
`indexes.legal_divergent` is the divergent card list, so P3's scan on a legality-composed filter is
`eval_domain × (divergent / n_cards) × printings_per_card`. Back-solving the measurements gives a divergent
share of 16–22% (`f:modern` 7,770 examined ⇒ 2,514 of 15,700 candidates; `f:commander` 21.7%;
`legal:pauper` 19.9%; `f:standard` 22.4%), which is one corpus constant and not a per-query guess.

So this is NOT the plane branch's correction extended — that one is `prefer`-aware, this one has to be
divergence-aware, and they share only the conclusion that one per-query `scan_units` cannot serve two
plans whose kernels short-circuit differently. Implementing it needs a per-plan field on `PlanFeatures`
(the pattern `compose_scan_printings` already establishes), set on the compose branch and read by P3's arm
alone, plus the matching mirror column in `fit_cost_model.py`.

So the fix is a feature correction on the compose acquire, not a rate — the category the toolkit says is
the only one a rate cannot rescue. It is also NOT the artwork arm of item 4 below: the 6× over-cost exists
in printing mode too (6.23, 5.25), where compose happens to be genuinely faster, so the wrong ratio does
not flip the pick. Artwork is only where the margin is thin enough to expose it.

### But that correction alone does not flip the pick

The term is linear, so the corrected prediction is exact arithmetic rather than a guess:

| query / mode | P3 predicted | with the fix | P3 measured | compose predicted | winner after | truth |
| --- | --: | --: | --: | --: | --- | --- |
| f:gladiator / artwork | 704.0 | **213.6** | 102.5 | 178.7 | compose | **P3** (102 µs) |
| f:modern / artwork | 813.7 | **252.8** | 142.8 | 188.8 | compose | **P3** (143 µs) |

It removes 490–561 µs — the largest single error — and takes P3 from 6.9× over to **2.08×** and 5.7× to
**1.77×**. But compose is predicted at 179/189, so P3 at 214/253 still loses and the 33 µs stays lost.

What remains is P3's per-card term: `eval_domain × (2.58 + 2.47 + max(tier, 6.58))` = 13,587 × 11.63 =
158 µs against a measured loop of 90.9 µs over those same cards — 6.7 ns/card actual, 11.63 charged. The
dominant piece is `STREAM_RESIDUAL_FLOOR_NS`, and at ~2 ns/card the arm lands near 151 µs and P3 wins. So
the floor is the load-bearing half, and it is the term with the worst history here: P4's analogue (18.89)
"was load-bearing despite being unmeasured" and moving it made routing worse. Unmeasured is the point —
it has never had the built-design treatment that corrected the loop intercept above.

Two reasons to ship the feature correction first regardless. It is a realized-counter defect of 13–15×,
which no rate can absorb; and it is **pick-preserving where picks are already right** — the printing-mode
rows still choose compose after the fix, and compose is correct there (94 vs 111 µs, 38 vs 152 µs). So it
buys accuracy without moving sound decisions, which is the cheapest kind of change to gate.

## The bigger lever underneath it: divergence is per-CARD but the data is per-FORMAT

Chasing the estimate above turned up something that makes it partly moot. A full pass over the corpus,
comparing each card's printings format by format:

    97,206 printings, 31,508 oracle cards
    cards with ANY divergent format: 556 (1.76%)
    divergent cards per format:
      oldschool       556

**Every divergent card in the corpus diverges in `oldschool` and nothing else.** No other format has a
single card whose printings disagree.

The engine cannot use that, because it detects divergence as a whole-bitmask comparison and stores one
boolean per card (`reload`: `row.card_legalities != cards.last()...card_legalities` sets
`legality_divergent`). So `plane_expr_is_existential` is true for every legality leaf, the #667 carveout
applies to every format, and `card_match_count` / `push_card_matches` / the popcount plans' `satisfies`
closure all re-verify per printing — on `f:modern`, 7,770 printing examinations that are provably
redundant, because modern legality is card-invariant in this data.

The upgrade is one line at the detection site and is **self-maintaining**: OR the XOR of the two bitmasks
into a corpus-level `divergent_formats: u64` instead of setting a boolean. Then a legality leaf whose
format bit is outside that mask is card-invariant, `all_match` holds, and the per-printing work does not
happen. Nothing hardcodes "oldschool" — if Scryfall ever makes another format divergent, the mask picks it
up from the data and the conservative path returns on its own.

Note what that does to the `scan_units` defect above: with legality non-existential, `split_planes`
consumes the leaf and the residual becomes `True`, so `tier_ns` is 0 and P3's arm charges **no scan term
at all** (`if tier_ns > 0.0`). The 525 µs over-cost stops existing for the queries where it mattered,
rather than being estimated more accurately. Same for the mispick: P3 measured 102 µs against compose's
182, and it is the over-cost that hides that.

So the ordering is: this, then re-measure, and only then decide whether a per-plan `scan_units` field is
still needed for the non-legality compose cases (`border:black`, `r:mythic`, `watermark:*`), where the
current estimate is already right to 1.1-2.4x.

## What is left

1. **Extend the popcount-skip walk past `FilterExpr::True`.** What is left of the perm estimate's p90 has
   two sources, and neither is reachable from a start position: non-matching entries *interior* to the
   walked segment, and clustering the predicate does not name (the 5.31-vs-4.26 gap in the regrade table).
   Scattering the match set through `inv_perm` and walking words at 64 cards a load reaches both —
   `run_query_streamed_popcount` already does exactly that for `unique=card` queries whose filter fully
   consumed to `True`, and printing mode, where P3 is actually routed, is the case it does not cover.
   Generalizing it needs per-card match counts for the skip (a popcount counts cards, not matches) and
   pays a per-card scatter, so it inherits the cost question the realized span had — with the difference
   that its payoff does not depend on matches being contiguous.
2. **A curvature term for the loop rates** — the cause of both the `FIXED` disagreement and corpus drift.
3. **Gate P4's artwork arm on its own.**
4. Carried over: make the fit loss the actual multi-way routing outcome rather than a pairwise proxy;
   bitmap+extract candidate materialization in the 64–4,095 band; the 4096+ prep band.
