# Measuring P3 and P4's loop and finish phases with built designs

Branch `engine-compose-feature-accuracy`. Companion to
[reference-cost-model-measurement.md](./reference-cost-model-measurement.md), which covers which tool
answers which question; this covers what the tools said about `StreamedSelect` (P3) and
`GatheredScan` (P4), and what is left.

The method this campaign arrived at, extracted so it can be reused without reading the whole file:
[diagnosing-a-plan-cost-error.md](../workflows/diagnosing-a-plan-cost-error.md).

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

### But that correction alone does not flip the pick (confirmed: it did not)

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

## Making the split non-destructive, and what it cost

`split_planes` is a destructive rewrite run at bind time, before any plan is costed: it moves predicate
out of the filter tree `PrintingCompose` composes from into a `PlaneExpr` compose cannot read, so compose
fails its `plane.is_none()` guard and never reaches the argmin. That guard is right — composing the
residual alone would drop the plane's predicate — but the elimination happens blind, under a layer whose
premise is "plan selection is one cost-based routing layer, not a hand-tuned decision tree". Measured
consequence: compose ran `f:commander`/printing in 1.83 µs against StreamedSelect's 99.38, a plan the
router was never offered.

`bind_and_split_filter` now retains the filter as bound; `printing_compose_applicable` asks about the
whole predicate, and `compose_source` is the one place that picks a representation. Every other plan keeps
plane+residual. Row identity across the whole series: 60 (query, mode, offset) cells over ten legality
shapes, identical on total, page length and a hash of returned `scryfall_id`s.

| query / mode | before | after | ratio |
| --- | --: | --: | --: |
| `f:modern t:creature` / printing | 90.00 | **42.83** | **0.476** |
| `f:modern t:creature` / artwork | 82.38 | 67.50 | 0.819 |
| bare formats, both modes | — | — | 0.97–1.14, compose still picked |

**And it raised regret 15%, which is the honest headline.** Total regret 48.7 → 56.1 ms, with the compose
slice going 39% → 46% of lost time (mean 3.98 → 4.86 µs, miss 10% → 12%). Isolated by reverting just the
consume guard: 56.8 ms with it off against 56.1 with it on — so the guard is **not** the cause and is now
free, where before the split was retained it cost 54× on `f:commander`/printing. The rise is the split
itself: handing the argmin a candidate whose arm is the worst-modelled in the engine (p90 41×, spread 136×
on rarity/usd) imports mispicks.

That is a real trade, and the two ways to read it are both defensible. Against: a measured 15% regret
regression is exactly what this branch has declined six times before. For: the regret is now *visible and
attributable* rather than hidden behind an elimination, and it is bounded by one arm's accuracy — the same
arm whose two defects are already measured (`scan_units` overstating P3 by 13–15×,
`STREAM_RESIDUAL_FLOOR_NS` charging 11.63 ns/card against a measured 6.7). Fixing those converts the
exposure into the win.

**The bare-format artwork mispick survives, and is now purely a cost error.** StreamedSelect with the
consumed form measured 79.83 µs against compose's 177.83 on `f:gladiator`/artwork, and the router still
picks compose — but now because it prices compose 4–6× too cheaply against P3, not because P3 was removed
from the ballot. The architecture stopped hiding the problem, which is what makes the remaining work worth
doing.

## The two cost-arm defects, both now measured

### `STREAM_RESIDUAL_FLOOR_NS`: measured with a built design, and declined

The floor only ever binds on `MASK_COMPARE` (tier 4.00); every other tier exceeds 6.58 and `max` takes the
tier. So `bench_streamed_loop`'s always-true `DateCmp` cells are exactly its population, and at 31,508 cards
they read `card/all_match` 2.49, `card/residual` 7.80, `printing/residual` 4.97 + 3.26 ns/printing.
Subtracting the loop body puts the residual's per-card cost at **2.45 ns against a charged 9.05** — the
`card_pass` call is the whole cost and the mask compare adds nothing measurable.

Traffic disagrees and traffic wins on levels: the fitted `CARD_PASS+FLOOR` column reads **8.19 against the
shipped 9.05 (0.90)** for P3 and **21.59 against 21.89 (0.99)** for P4. Nothing to correct.

That is the **third** time this file has caught the same artifact — an always-true predicate over
chunk-rotated slices measuring a cache state production never reaches, differing by 3.3× here as it differed
1.6–2.2× in the retraction at the top of `bench_streamed_loop`. It is now recorded in the constant's own doc,
so the next person measuring it finds the answer instead of the trap.

### `stream_scan_units`: shipped, and it was necessary but not sufficient exactly as predicted

P3 now has its own scan estimate on a legality-composed acquire, from the divergent share of the candidate
span (`legal_divergent`, 556 of 31,508), floored at one printing per candidate and scoped to filters that
touch legality — `border:black`, `r:mythic` and `watermark:*` measured at parity between the two plans.

It also surfaced a defect this branch had introduced: `acquire_plan_features` was still calling
`compose_printing_estimate` on the RESIDUAL. Once the divergence mask began consuming legality leaves that
residual was `True`, so compose estimated all 97,206 printings for every legality query alike and was costed
as if it returned the corpus for free — `matches` reading 97,206 identically across `f:modern`, `f:predh` and
the rest, at a predicted pick/best ratio of 0.00. Applicability, estimate and execution now share one
`compose_source`. **The numbers reported for the consume-guard commit were measured against that broken arm**
and should not be read as they stand.

| | before | after |
| --- | --: | --: |
| predicted pick/best on the artwork mispicks | 0.22 | **0.63–0.69** |
| `StreamedSelect [printing_compose] / artwork` p90 | 6.69 | **4.73** |
| its p90/p10 spread | 12.3 | **9.2** |
| total regret | 56.1 ms | 55.0 ms |
| row identity, 60 cells | — | identical |

**The mispicks survive, and that is the result.** Sixteen remain. The model prices compose ~1.5× under P3
where P3 measures ~1.7× faster, so ~2.5× survives a corrected feature — and it is not the per-card level
(traffic fits it at 0.90), not the floor, and not the scan feature. Something cell-specific to compose-acquire
artwork is mis-shaped and none of the three things measured here is it.

## It was not compose. It was P3, and fixing it uncovers a third pair

**Retracted: "compose is priced ~1.5× under P3".** Attributing both arms term-by-term against measurement
says the opposite — on the four card-invariant formats compose is the *well*-modelled plan and P3 is the
broken one:

| cell | P3 pred | P3 meas | P3 ratio | compose pred | compose meas | compose ratio |
| --- | --: | --: | --: | --: | --: | --: |
| `f:gladiator` / artwork | 259.58 | 72.58 | **0.28** | 178.73 | 186.54 | 1.04 |
| `f:predh` / printing | 215.80 | 34.08 | **0.16** | 70.62 | 80.21 | 1.14 |
| `f:commander` / artwork | 323.24 | 143.62 | **0.44** | 188.80 | 220.12 | 1.17 |
| `f:oldschool` / artwork *(the divergent format)* | 31.88 | 35.62 | 1.12 | 14.82 | 37.00 | **2.50** |

Compose is under-priced only on `oldschool` — the *opposite* format from where the mispicks are. The
attribution reproduces `predicted_ns` exactly, so this is arithmetic on the shipped arm, not a re-fit.

**The mechanism.** `acquire_plan_features`'s compose branch passed `verify_cost_tier(composed)` with no
gate, where the general candidate path gates on `all_match_known`. On a card-invariant legality format
`card_pass` returns `Tri::True` at card level for every card, so the kernel takes its `all_match` arm and
`printings_examined` reads **0** — while the model charged `CARD_PASS + max(tier, FLOOR)` = 9.05 ns on every
candidate *and* `stream_scan_units × 5.97`. Those two dead terms are **92–94% of P3's predicted cost** on
these cells.

**One retraction inside the fix.** A first attempt priced this as the divergent *share* of the corpus
(`legal_divergent / n_cards`, ~1.76%). That is wrong in principle — for `f:oldschool` the candidates largely
*are* the divergent cards, so a global share under-charges exactly the cells whose residual is real. It
traded 408 mispicks for 118 that were four times worse (mean 54.27 → 206.24 µs) for a net 3.4%. The right
signal is the boolean the engine already derives from data, `plane_expr_is_existential` against
`divergent_formats`, extracted as `plane_leaves_nothing_to_verify` so the router asks what the executor asks.

**The gate works on the pair it targets, and costs regret elsewhere.** Pairwise ordering, 120 s uniform:

| pair, `[printing_compose]` acquire | baseline | with the gate |
| --- | --: | --: |
| `PrintingCompose vs StreamedSelect` | 80% ordered right, mean regret 11.04 µs | **96%, 1.57 µs** |
| `GatheredScan vs StreamedSelect` | 69%, 35.96 µs, gap 0.89 | 69%, 36.82 µs, gap 0.90 |

Total regret went **81.7 → 129.7 ms** (mean 2.07 → 3.50 µs), with `StreamedSelect -> GatheredScan` going
1,045 → 1,830 queries and 37% → 79% of all lost time. The two facts are not in conflict: the P3/P4 pair is
**69% accurate before and after**, unmoved by this change, and correcting P3's cost simply lets P3 reach the
argmin far more often, which multiplies exposure to it. P3's inflated cost was *masking* a pair that was
already wrong — a compensating error, and every measurement of that pair ever taken through it was confounded
by P3 being priced 3–6× over.

### What the gate exposed: P4's scan feature over-counts 1.76× on the same population

`GatheredScan` walks every printing of every candidate, so its scan feature is the candidate SPAN, estimated
by `scan_all` as `est_cards ×` corpus-average printings-per-card `× 2.1`. That shape is right only when
candidates are an average sample of the corpus. With nothing to verify they are not — every printing of a
matching card matches, so the span **is** `printing_matches`, a quantity the branch already computes.
Graded against P4's realized `printings_examined`, 597 card-invariant compose queries:

| feature | p10 | p50 | p90 | p90/p10 |
| --- | --: | --: | --: | --: |
| `scan_units` (was shipped) | 1.13 | **1.76** | 5.08 | 4.5 |
| `printing_matches` (now) | 0.68 | **0.93** | 3.08 | 4.5 |

Scoped to the same boolean, because the grading **inverts** on the other population: with a real residual
`scan_units` is right at p50 0.97 and `printing_matches` badly under at 0.39. A 1.76× over-charge on the
dominant term of P4's arm is what makes P3 win where P4 is better, which is the slice the gate inflated.

### Both together, measured at identical settings

| build | total regret | mean | `PrintingCompose -> StreamedSelect` | `StreamedSelect -> GatheredScan` |
| --- | --: | --: | --: | --: |
| baseline (HEAD) | 81.7 ms | 2.07 µs | 408 q, 27% of loss | 1,045 q, 37% |
| tier gate alone | 129.7 ms | 3.50 µs | 33 q, 0% | 1,830 q, 79% |
| gate + P4 scan | 61.2 ms | 1.55 µs | 48 q, 1% | 1,050 q, 50% |
| + clustering bias on the ball count | 56.4 ms | **1.49 µs** | 49 q, 1% | 999 q, 51% |
| **+ `scan_units` clamped at `n_printings`** | 57.0 ms | **1.54 µs** | 43 q, 1% | 999 q, 52% |

Compare the **means**: the total is a sum over however many queries the budget reached (39,458 down to 37,111
across these rows), so the totals are not comparable and the mean is. **−26% on the baseline.**

The last two rows are within run noise of each other (1.49 against 1.54 µs on transition tables that are
otherwise identical — 999 queries against 999 on the top slice), so the clamp is **aggregate-neutral**. It is
kept for what the aggregate cannot see: it is the only one of the four that moved the pair
`GatheredScan vs StreamedSelect [printing_compose]`, 69% → **75%** with mean pair regret 40.24 → 23.00 µs.
Which is the same lesson as the table itself — a 3% move in a pooled mean is not evidence either way, and the
per-pair diagnostic is what ranks routing work.

The mispick class this started from is gone (408 → 43 queries, mean 54.27 → 13.93 µs). `cargo test` 149 debug
/ 148 release throughout; row identity needs no new run because these are cost-only changes and
`force_plan_differential_agreement` already proves every plan returns the same rows.

The honest caveat: the P4 scan fix **did not fix the pair**. `GatheredScan vs StreamedSelect
[printing_compose]` is still 69% ordered right (gap 0.89 → 0.91), and `StreamedSelect -> GatheredScan`
returned to its baseline level rather than improving on it. What the fix removed was the bias that was
*amplifying* the gate's exposure to that pair. The pair remains item 1 — and it is now diagnosed there as a
named sub-population (broad residuals, both plans scanning the corpus, the two arms' scan rates 2.9× apart)
rather than the variance this section first guessed at. The card-invariant half of the acquire, which is what
these two changes touched, orders **409/409** correctly.

Also worth watching: `GatheredScan vs PrintingCompose [printing_compose]` gap sizing drifted 1.14 → 1.40
while staying 92% ordered right, so it costs nothing yet and is a sign compose's arm is absorbing something.

## The open decision: 13% of regret is unpaid

Regret stands at **55.0 ms against a 48.7 ms baseline**. The rise came from making the split
non-destructive — handing the argmin a candidate whose arm is the worst-modelled in the engine — and both
candidate explanations for it have now been eliminated by measurement. Two honest options:

- **Find compose's remaining shape error.** ~~It is the largest single error in the model~~ — retracted; the
  error on the mispicked cells is P3's, and the real prerequisite is the P3/P4 pair at 69%. See the section
  above. Compose's rarity/usd spread is still real but is not what the artwork mispicks were made of.
- **Revert the non-destructive split** and give back `f:modern t:creature`'s 0.476× until the compose acquire
  can rank its plans. The plumbing is inert on its own; only compose's widened applicability carries the cost.

**This decision is now closed, and neither option was the answer.** The debt was not bounded by compose's arm
— the arm mis-pricing the mispicked cells was P3's. Correcting it plus P4's scan feature took regret to
**61.2 ms against the 81.7 ms measured baseline, −25%**, without reverting the split, so `f:modern
t:creature`'s 0.476× is kept. What is left is not a debt but a named defect: the P3/P4 pair at 69%, item 1.

## Re-baselined after this series, because the ranking moved

`bench_regret_matrix.py --seconds 180`, current build. The acquire this whole investigation was picked from
has dropped from roughly half of all lost time to a fifth:

| acquire | n | mean | SHARE |
| --- | --: | --: | --: |
| **candidates** | 26,251 | 1.69 µs | **78%** |
| printing_compose | 7,536 | 1.60 µs | **21%** (was ~49%) |
| printing_range_scan / plane / card_range_popcount | 3,370 | ≤0.22 | 0% |

| cell | mean | SHARE |
| --- | --: | --: |
| candidates / artwork | 1.91 µs | 32% |
| candidates / printing | 1.68 | 28% |
| candidates / card | 1.43 | 19% |
| printing_compose / printing | 1.63 | 8% |
| printing_compose / card | **2.09** | 7% |
| printing_compose / artwork | 1.25 | **6%** (was 32%) |

Three cautions in reading it, all of which change what the next item should be:

- **The compose acquire is no longer unusually broken.** Its mean is *lower* than `candidates`. The 78/21
  split is volume — 3.5× the queries — not severity. "Candidates is now the problem" describes where the
  aggregate lives, not a newly found defect.
- **The loss is entirely tail.** `p90 = 0.00` on every acquire; only p99 (36–64 µs) carries anything. The
  median query has zero regret, so a mean is the wrong thing to optimise and "improve the average estimate"
  is the wrong instinct.
- **The largest transition is unchanged**: `StreamedSelect -> GatheredScan`, 967 queries, mean 29.38 µs, 50%
  of all loss. But on `candidates` that pair is already **92%** ordered right at 2.42 µs mean pair regret. So
  what remains is the 8% tail of a well-ordered pair spread over high volume — a materially harder and
  lower-yield target than anything fixed in this series, and it should be sized before it is started.

## What is left

1. **Rank `GatheredScan vs StreamedSelect` on the compose acquire.** Now **75% ordered right** over 4,825
   non-tie pairs, mean regret 23.00 µs, gap 0.96 — still the engine's largest single routing error and still
   the top item. Four feature corrections landed against it, and the pattern of which ones moved it is the
   useful part:

   | correction | pair ordered right | pair mean regret | gap |
   | --- | --: | --: | --: |
   | baseline | 69% | 35.96 µs | 0.89 |
   | verify-tier gate | 69% | 36.82 | 0.90 |
   | P4's span feature | 69% | 40.24 | 0.91 |
   | estimator bias onto the ball count | 69% | 40.24 | 0.93 |
   | **`scan_units` clamped at `n_printings`** | **75%** | **23.00** | **0.96** |

   Three of the four moved nothing, and the reason is instructive: `eval_domain` and `scan_units` feed **both**
   arms, so a correction to either moves both predictions the same direction and the difference barely
   changes. The clamp moved it because it lands **asymmetrically** — `scan_units` is 76% of P3's arm on this
   class and 28% of P4's. **For an argmin, a feature fix only pays when the two plans weight the feature
   differently.** That is the same principle as the module header's "a term wrong for every plan cancels",
   applied to features rather than rates.

   **Not variance — a sub-population, and it is named.** The gap ratio invites reading this as noise around a
   correct average; splitting the pairs by whether the sign is right says otherwise. At 69%, over 5,085
   non-tie pairs:

   | group | n | P3 wins (measured) | P3 wins (predicted) | median \|gap\| |
   | --- | --: | --: | --: | --: |
   | right, tier 0 (card-invariant) | 409 | 100% | 100% | — |
   | right, tier > 0 (real residual) | 3,100 | 5% | 5% | 31.8 µs |
   | **wrong, tier > 0** | **1,576** | **98%** | **2%** | **98.4 µs** |

   Every wrong pair is in the `tier > 0` regime — the card-invariant population the two fixes above touched
   is ordered **409/409** correctly. And the wrong group is not mixed: P3 really wins 98% of it while the
   model says P4 wins 98% of it. The model **over-picks P4** on a specific class.

   That class is **broad residuals where both plans examine the whole corpus** — `border:black`,
   `year<=2026`, `cn<336`, bare date ranges. Worst cells, `printings_examined` = 97,206 for *both* plans:

   | query / mode | P3 meas | P4 meas | P3 pred | P4 pred |
   | --- | --: | --: | --: | --: |
   | `border:black` / printing | 861.3 | 1,489.8 | 841.8 | **838.2** |
   | `cn<=226 year>2004` / printing | 1,025.3 | 1,454.7 | 789.6 | **756.4** |
   | `year<=2026` / artwork | 601.6 | 1,026.9 | 1,629.2 | **1,326.1** |

   P3 is genuinely ~1.7× faster and the model has the pair tied to within 5 µs.

   **Retracted guess: "the two scan rates cancel."** Decomposing both arms says the false tie is one-sided.
   On `border:black`/printing, P3 predicts **1.03** of its real time and P4 predicts **0.64** — P3 is priced
   correctly and P4 is under-charged 1.56×. Two errors are not cancelling; one plan is wrong. The mechanism
   was `eval_domain` reading 16,511 where both plans visited all 31,508 cards, which discounts P4 by 2.2× as
   much as P3 because P4's per-card rate is 25.77 ns against 11.63. Fixed by moving the clustering bias onto
   the ball count (see the commit); the broad band went 0.52 → 0.78 against `cards_visited`.

   **Retracted a second time: "what survives three feature corrections is a rate problem."** A fourth
   feature correction moved it 69% → 75%, so that inference was wrong — it rested on three fixes that could
   not have moved the pair for the structural reason above, and read their failure as evidence about rates.
   The estimator fix also made things temporarily worse in a way worth recording: it improved `eval_domain`
   (16,511 → 24,592) and *degraded* `scan_units` (106,970 → **159,325** against a realized 97,206), because
   `scan_all` derives from `est_cards`. P3 went 1.03 → 1.53 of its real time while P4 sat at 0.88 — both
   plans over the same feature. A feature above `n_printings` is impossible rather than merely wrong, which
   is what made the clamp an invariant instead of a calibration.

   **Features are still not exhausted at 75%.** The wrong group retains a feature asymmetry against the
   right group, so the next step is not automatically a rate measurement:

   | feature / realized counter | right group p50 | wrong group p50 |
   | --- | --: | --: |
   | `eval_domain` | 0.98 | **0.85** |
   | `scan_units` | 0.95 | 1.00 (was 1.37 before the clamp) |
   | `matches` | 1.00 | 1.11 |

   `eval_domain` still under-counts by ~1.18× exactly where the ordering fails, and P4's per-card rate is
   2.2× P3's, so that residue still discounts P4 asymmetrically. `meas/pred` reads **−1.17** in the wrong
   group: magnitude right, sign inverted, as before.

   **Answered, without a built design: it is the features, and by a wide margin.** An ORACLE run settles the
   sequencing question directly — recompute both arms substituting each plan's *realized counters* for the
   estimated features (`cards_visited`, `printings_examined`, `matches_pushed`), keeping every shipped rate
   untouched, then re-run the argmin. Over 2,778 non-tie pairs with at least 100 realized cards on both plans:

   | features | ordered right | lost time (sum) |
   | --- | --: | --: |
   | shipped estimates | 58% | 116.2 ms |
   | **oracle (realized counters)** | **83%** | **12.8 ms** |

   | mode | shipped | oracle |
   | --- | --: | --: |
   | card | 58% | **96%** |
   | printing | 62% | 80% |
   | artwork | 56% | 81% |

   Perfect features against **today's rates** reach 83% and cut lost time **9×**. That is the ceiling any
   estimator work can buy, and it is most of the gap — so the rates are adequate to 83% and the remaining 17%
   is what is genuinely attributable to them. **Features are the foundation and come first**; the rate
   question is real but second-order and should not be opened until the estimates stop moving.

   (58% here is not the 75% above: this run requires ≥100 realized cards on *both* plans, which selects the
   larger queries where estimator error dominates. The valid comparison is the internal one, 58 → 83 on
   identical rows with identical rates.)

   One structural caveat on that ceiling, and it is the first thing to settle. The oracle gave each plan **its
   own** counter, where `scan_units` today is one shared number that both arms read while examining different
   amounts — `stream_scan_units` splits it for P3 on legality filters only. So the 58 → 83 gain mixes two
   distinct fixes: *more accurate* shared features, and *per-plan* features. Decomposing that is step one,
   because it decides whether the work is a better estimator or a split feature.

   For when the rate question does open: the existing harnesses already report P3 at 3.30 ns/printing against
   P4's 2.27, a ratio of **1.45**, where the shipped constants are 5.97 and 2.06, a ratio of **2.90**. Both
   were measured warm, but *together*, and a ratio between two equally-warm arms survives the cache caveat
   that voids their levels.

   Two traps attached to any rate work here. A pooled traffic fit **endorses both current rates**
   (StreamedSelect `SCAN_PER_ROW` 6.04, ratio 1.01; GatheredScan 2.53, 1.23), so `fit_cost_model` cannot find
   this and a pooled refit will confirm the status quo. And a built design reads warm-cache, so it yields the
   **shape** — which of the two rates is misattributed — and not the level. Wrong-rate now concentrates by
   mode at artwork 29%, printing 24%, card 18%.

   Superseded item 1 ("compose's remaining shape error") — see the retraction above; compose reads 1.02–1.17
   on the mispicked cells and only `oldschool` is under-priced.

1b. **Review the compose acquire's feature estimation — the foundational work, ahead of any rate work.** The
   oracle run above bounds this at 58% → 83% ordered right and a 9× cut in lost time on the pair, with rates
   untouched. Two questions of that bound are now answered by measurement.

   **Which feature carries it — leave-one-out from the full oracle, shipped rates throughout, 2,768 pairs:**

   | variant | ordered right | lost time |
   | --- | --: | --: |
   | shipped | 59% | 116.7 ms |
   | full oracle (per-plan) | **83%** | **12.4 ms** |
   | oracle, `eval_domain` back to its estimate | 68% | **91.2 ms** |
   | oracle, `scan` back | 79% | 22.4 ms |
   | oracle, `matches` back | 83% | 13.2 ms |
   | oracle, `scan` forced **SHARED** (both read P4's) | 83% | 12.4 ms |
   | oracle, `eval_domain` forced **SHARED** | 83% | 12.4 ms |

   **`eval_domain` is ~75% of the recoverable loss** (78 of the 104 ms), `scan` ~10%, `matches` nothing.

   **And per-plan features are worth exactly zero.** Forcing either feature to be shared, while keeping it
   exact, costs nothing at all. So the answer is **not** to split features per plan — it is to make the one
   shared number accurate. That is visible in the mechanism: on the broad-residual class both plans examine
   the same 97,206 printings, and on the card-invariant class the verify-tier gate already zeroes P3's scan
   term. **A corollary worth checking: `stream_scan_units` may now be redundant with that gate**, since the
   divergence it was built for is the population the gate already handles.

   **Where `eval_domain`'s error lives** (ratio against P4's realized `cards_visited`):

   | path | n | p50 | mean \|log\| |
   | --- | --: | --: | --: |
   | EXACT (`range_card_counts_for`) | 574 | 0.92 | **0.236** |
   | estimated (`calibrated_balls_into_bins`) | 3,154 | 0.90 | 0.382 |

   | query shape | n | % estimated | p50 | mean \|log\| |
   | --- | --: | --: | --: | --: |
   | 1 leaf, range | 1,064 | 46% | 0.91 | **0.226** |
   | 1 leaf, other | 1,384 | 100% | 1.15 | 0.317 |
   | **2+ leaves, ALL range** | **745** | **100%** | **0.62** | **0.507** |
   | 2+ leaves, mixed | 322 | 100% | 1.19 | 0.553 |
   | 3+ leaves, ALL range | 108 | 100% | 0.70 | 0.541 |

   **It was the quantity, and that is now fixed.** The third caveat below turned out to be the answer, so the
   range-path widening was never the right work. `eval_domain` was `est_cards` — a count of *matching* cards —
   graded against `cards_visited`, which counts *candidates*, a superset whenever the narrowing is inexact.
   The distribution is bimodal: **34% of compose rows visit every card in the corpus**, and on those the right
   value is not a better estimate but `n_cards`:

   | on the 986 full-scan rows | p10 | p50 | p90 | mean \|log\| |
   | --- | --: | --: | --: | --: |
   | `est_cards` (was) | 0.43 | 0.65 | 0.83 | 0.454 |
   | `n_cards` (now) | 1.00 | 1.00 | 1.00 | **0.000** |

   Predicted with the predicate and constant the sibling `PrintingRangeScan` branch already uses for the
   identical decision — `range_too_broad_to_narrow(printing_matches, n_printings)`, `MAX_NARROW_FRACTION`
   0.25 — so no new constant. Scored against the realized flag it catches **98%** of full-scan rows at 87%
   accuracy, beating every threshold on two alternative signals. Its 26% false positives over-cost both
   materializing plans by the same factor, which an argmin absorbs; the false negatives were the ones losing
   the pair, so recall is the side to favour.

   | | before | after |
   | --- | --: | --: |
   | `eval_domain [printing_compose]` p50 / p10 | 0.91 / 0.45 | **1.00 / 0.68** |
   | pair ordered right | 75% | **87%** |
   | pair mean regret | 23.00 µs | **4.29 µs** |
   | pair gap meas/pred | 0.96 | **0.98** |

   That brings the acquire within reach of the well-behaved `candidates` acquire (92%, 2.42 µs). **Total regret
   is flat** (1.52 against 1.54 µs) and `StreamedSelect -> GatheredScan` is still 967 queries — because
   `bench_pairwise_ordering` scores every pair including queries where compose wins anyway, so the P3/P4
   ordering never reaches the routing outcome there. Pairwise accuracy is a leading indicator, not the result;
   the value banked is that the features are now trustworthy for everything downstream.

   The other two caveats stand as recorded, and the remaining `eval_domain` error (p10 0.68) is in the
   *narrowed* regime, where these still apply:

   - **The exact path is not exact against the counter** (p50 0.92, p10 0.50), so it is not a 1.00 ceiling.
   - **The two quantities may not be the same thing.** `eval_domain` estimates *matching* cards, while
     `cards_visited` counts *candidates visited* — 24,592 against 31,508 (the whole corpus) on
     `border:black`. Candidates ⊇ matches whenever the narrowing is inexact, which would explain systematic
     under-counting far better than miscalibration does, and would mean the fix is to estimate the
     **narrowed candidate count** rather than to calibrate a match count. Test this first; it decides whether
     any of the above is the right work.
   - Combining two exact range counts is also not free: the boundary table answers each range independently,
     and an `And`'s distinct-card count is not derivable from the two without composing.

   **`scan_units` on selective compose queries** stays on the list at mean |log| **1.22**, p10 0.08, p90 3.62
   — a ~45× spread no bias variant improves — but is now known to be second-order for this pair (~10%).

1c. **Build the pair-level loop harness, once the features stop moving.** `bench_streamed_loop` and `bench_gather_loop` now
   share `bench_loop_design`, so their cells match and can be read side by side — but neither computes the
   cross-plan quantity, deliberately: the header defers plan comparison to `explain_analyze`. That deferral
   is incomplete, because `explain_analyze` compares predicted against measured *per plan* on sampled
   traffic and cannot isolate a per-unit rate. The result is that the one number routing depends on — P3's
   per-printing rate against P4's, on identical cells — is produced by nothing.

   **Extend, do not fork.** `bench_loop_design` exists precisely because a P3-vs-P4 rate comparison is only
   valid when the cells match, and they had already drifted once (`CARD_COUNTS` sharing two of five sizes). A
   third harness that built its own cells would reintroduce that. The increment is to move **cell
   construction** into `bench_loop_design` alongside the parameters, then add one reporting test that runs
   both arms over those cells and prints each rate, the measured ratio, and the shipped ratio beside it.

   Two prerequisites, both already flagged in `bench_streamed_loop`'s own header. The rates there are
   `ns_loop` only, and *"P3's arm may be absorbing setup or finish cost that its loop never pays — that has
   to be ruled out before the gap is called an error."* `Cell` already carries `ns_setup` and `ns_finish`, so
   the data exists and is unused. And the broad-residual population is already the `residual: true` group
   (an always-true `DateCmp` via `DATE_AFTER_EVERYTHING`), so no new cell class is needed — only the
   comparison.
2. ~~**Decide the 13% regret debt.**~~ **Closed** — and neither of the two options on offer was the answer.
   The debt was never bounded by compose's arm; the mispriced arm was P3's. Gating the verify tier on the
   compose acquire plus fixing P4's candidate span took regret to **61.2 ms against the 81.7 ms measured
   baseline, −25%**, without reverting the split, so `f:modern t:creature`'s 0.476× is kept. See the
   retraction section above for why the original framing pointed at the wrong plan.
3. **Extend the popcount-skip walk past `FilterExpr::True`.** What is left of the perm estimate's p90 has two
   sources, neither reachable from a start position: entries *interior* to the walked segment, and clustering
   the predicate does not name (the 5.31-vs-4.26 gap in the regrade table). Scattering the match set through
   `inv_perm` and walking words at 64 cards a load reaches both, and `run_query_streamed_popcount` already
   does that for `unique=card` + `True` — printing mode, where P3 is actually routed, is the case it does not
   cover. Needs per-card counts for the skip, since a popcount counts cards and not matches.
4. **Compose `format:A AND format:B` now that both are usually card-invariant.** The shared-witness objection
   dissolves when neither format diverges: `∃p: A(p) ∧ B` is `(∃p: A(p)) ∧ B`. `compile_plane` still declines
   it with `u64::MAX`, and `legality_and_of_two_formats_declines_but_or_compiles` names the assertion to
   revisit.
5. **A curvature term for the loop rates** — cause of both the `FIXED` disagreement and corpus drift, though
   the five-size sweep demoted it: the drift saturates and production sits at the bottom of the curve.
6. **Gate P4's artwork arm on its own.** Every surviving mispick is in artwork mode, which makes this more
   interesting than when it was queued.
7. Carried over: make the fit loss the actual multi-way routing outcome rather than a pairwise proxy;
   bitmap+extract candidate materialization in the 64–4,095 band; the 4096+ prep band.

## The candidates acquire: `matches`, and a declined tier-1 fix

The re-baseline put `candidates` at 78% of lost time. Investigated, and it is a **different defect from the
compose acquire's** — the compose-acquire lessons do not transfer.

**It is not the features, mostly.** The oracle substitution that bought 89% on compose buys only **29%**
here (60.0 → 42.3 ms). `eval_domain` is **exact at p10/p50/p90**, because on this acquire it is the
materialized narrowed candidate count rather than an estimate. Every losing query is the P3/P4 pair, and it
fails in **both directions** — `StreamedSelect -> GatheredScan` 1,159 queries / 38.8 ms and
`GatheredScan -> StreamedSelect` 861 / 21.1 ms.

**The one bad feature is `matches`, and the sign of its error picks the direction of the mispick:**

| transition | n | `matches` / realized, p50 | sum lost |
| --- | --: | --: | --: |
| `StreamedSelect -> GatheredScan` | 782 | **29.85** | 27.5 ms |
| `GatheredScan -> StreamedSelect` | 767 | **0.88** | 21.4 ms |
| correct | 13,601 | **1.00** | 0.0 ms |

The mechanism is an asymmetry: `matches` drives P4's `PUSH` at 2.24 ns against P3's `EMIT` at 0.12 — 18.7× —
**and** enters P3's `perm_steps` inversely. Over-counting therefore charges P4 more and P3 less, both
favouring P3. `matches_pushed` agreed between the two plans on 100% of rows, so one shared feature is
structurally fine here; it is simply wrong.

`matches` is the candidates' printing/artwork **span** discounted by one constant per mode
(`RESIDUAL_PASS_RATE_PRINTING` 0.40, `_ARTWORK` 0.53). A p50 of 29.85 is far past what any rate explains: the
span is ~75× the truth there, because under a loose narrowing most candidates do not match at all. **Same
class of defect as `eval_domain`'s** — a span is the wrong base for a match estimate — one field over.

### Tier 1, measured and declined as a fix

`filter::touches_printing_field` is new: the `any` composition of the leaf table `printing_dependent` reads
with `all`, factored so the two callers cannot disagree on the table. A card-invariant residual answers
`True`/`False` per card, so a candidate contributes its whole span or none. Published as
`residual_card_invariant`; **nothing in `plan_cost` reads it.**

Implied true pass rate, `shipped / (matches / realized)`:

| mode | residual class | n | p10 | p50 | p90 |
| --- | --- | --: | --: | --: | --: |
| printing | card-invariant | 257 | 0.00 | **0.78** | 1.00 |
| printing | printing-varying | 2,544 | 0.06 | **0.35** | 0.87 |
| artwork | card-invariant | 221 | 0.00 | **0.60** | 0.90 |
| artwork | printing-varying | 2,386 | 0.09 | **0.43** | 0.74 |

The classifier separates the populations — 2.2× in printing mode — and the shipped 0.40 sits on the
printing-varying value, confirming it was fitted there. **But the hypothesis it was built to test is wrong.**
"Card-invariant ⇒ pass rate 1.0" fails: p50 is 0.78 and **p10 is 0.00**. The all-or-nothing reasoning holds,
but the rate is then "what fraction of *candidates* match", which the residual's class does not pin — the
spread inside the class is the full 0.00–1.00. And the class is only **9% of rows**, so a perfect rate there
moves almost nothing.

**Split constants are therefore not worth shipping**, and the diagnostic is kept only to scope the next step.

### Why tier 2 is the work

Printing-varying is 91% of rows and its median is already within 14% of shipped (0.35 against 0.40). The
defect there is **spread — 0.06 to 0.87, a 14× range** — which no constant can address. That is the argument
for a per-predicate estimate rather than a better global rate:

- **Indexed range residuals** (`usd`, `cn`, `date`) have an exact printing count from two
  `partition_point` calls, which `bare_range_bounds` already uses for its `k`.
- **Plane residuals** (border, rarity, legality) have stored popcounts.
- The residual usually is not the whole predicate, so a global pass fraction still assumes independence
  from the candidate set — the assumption that just cost us on `eval_domain`. Preferring a direct count
  where the residual *is* the leaf avoids it; conjunctions still need care.

### Tier 2: both exact-count routes measured and declined

The plan was to replace the global pass rate with a per-predicate count, since the spread (0.06–0.87) is
what a constant cannot fix. Two routes existed without new machinery. Both are worse than the span estimate,
and they fail for the same reason.

**`estimator::estimate_cardinality`** — already in the tree, index-backed leaves, `Cardinality {lo, est, hi}`
with independence for `And` and `min` for the `hi` bound. In production it is called **once**, as a
`hi <= STREAM_MIN_MATCHES` threshold check. Graded against `matches_pushed`:

| mode | shipped `matches` | `estimate_cardinality` |
| --- | --: | --: |
| card | p50 1.40, p90 9.91, mean \|log\| **0.789** | p50 1.45, p90 **33.38**, **1.312** |
| printing | p50 1.09, p90 11.03, **0.953** | p50 0.70, p90 17.12, 1.346 |
| artwork | p50 1.19, p90 10.19, **0.807** | p50 1.17, p90 25.01, 1.281 |

Uniformly worse, and worst in the tail — which is where this acquire's loss lives. It dates from the
estimate-based era of the planner; its `lo` arm already documents that Bonferroni is *unsound* over two or
more printing-varying children, because a card can enter each child's card-space projection via **different
printings** while no single printing satisfies all.

**`RangeCardCounts::distinct_cards`** — genuinely exact, not an estimate: `below`/`at_or_above`/`at` per
distinct value, two `partition_point` probes, exact for every op but a true interior range. It is the right
instinct and it still does not work here:

- **Coverage 9%** of rows with a residual — 7% of the misordered ones, 2.0 ms of a 46.5 ms pairwise gap.
- **6× worse where it applies** — card p50 **6.32** against the shipped 1.12.

Because `bare_range_bounds` matches the **residual**, so the count is exact for `cn<100` *globally* while the
feature needs cards matching `cn<100` **and the rest of the query**. Exact for the wrong set. The span
estimate is worse in principle and better in practice because it at least reflects the candidate set.

**The shared lesson, third instance today.** `eval_domain` confused candidates with matches; the compose
`scan_units` clamp was an impossible value; and now both exact-count routes answer a marginal question where
a conditional one was asked. The quantity is `|candidates ∩ residual|`, and its exact evaluation is the work
being costed. A bitmap AND of the candidate set against an indexed leaf's set would deliver it for the same
9%; nothing cheap covers the remaining 91%.

**What is left for `matches`, in order of expected value:**

1. **Condition the pass rate on the residual TIER, not just the mode.** The tier already exists as a feature
   and it separates the populations: implied rates ~0.24–0.32 for `MASK`, ~0.35 for `SET_LOOKUP`, ~0.95 for
   `TEXT_SCAN`/printing against a shipped 0.40. A level change fitted from traffic, so it is on the right
   side of the branch's rule, and it needs no new machinery. It fixes medians, not the 14× spread.
2. **Bitmap-AND the candidate set with an indexed residual leaf** for an exact conditional count on the 9%.
   Related to `local-engine-candidate-materialize.md`'s finding that a bitmap beats a sorted vec from ~64
   candidates up, though that doc measures the acquire, which regret excludes by construction.
3. **Accept the spread.** The evidence so far is that `matches` is irreducibly estimated on this acquire
   without doing the residual's own work, and that its error is what the P3/P4 pair mostly reflects.

### Miss size predicts the cause, which reverses how to read the 26%

Splitting the candidates acquire's mis-picks (>2 µs gap) by whether realized counters would fix the order:

| cause | n | % of n | sum lost | % of ms | mean | p90 |
| --- | --: | --: | --: | --: | --: | --: |
| feature (oracle fixes the order) | 625 | 26% | 27.9 ms | **37%** | **44.6 µs** | 81.3 |
| rate/shape (survives the oracle) | 1,796 | 74% | 47.4 ms | 63% | 26.4 µs | 43.5 |

| band | n | feature-caused, % of count |
| --- | --: | --: |
| 2–10 µs | 492 | 15% |
| 10–30 µs | 929 | 16% |
| 30–100 µs | 935 | **40%** |
| >100 µs | 65 | **54%** |

**Feature errors are the expensive tail; rate errors are the cheap bulk.** Since regret here is entirely a
tail phenomenon (`p90 = 0.00` on every acquire), the 26% headcount understates feature work — it is 37% of
avoidable time and 54% of the misses over 100 µs. An earlier note in this file called 25% the ceiling on
feature work; that read the wrong column.

### Pattern A shipped: P3's scan gated on within-card invariance

Two feature defects produce all 625. The first is fixed.

A residual invariant *within* a card never goes printing-dependent — `card_pass` returns `True`/`False` and
never `Tri::PrintingDep`, so the streamed kernel sets its per-card `all_match` and `card_match_count` answers
from span arithmetic. **P3 examines no printings at all.** The arm was charging `scan_units ×
STREAM_SCAN_PER_ROW_NS` for the whole candidate span anyway:

    name:s / artwork, order=cmc desc, limit=100        LOST 368.0 us
      StreamedSelect   pred 1507.0 us   meas  456.0 us   <- best
      GatheredScan     pred 1213.8 us   meas  824.0 us   <- picked
      feature          used      P3 realized   P4 realized
      eval_domain      31,508    31,508        31,508      1.00x
      scan_units       97,206    0             60,705      1.60x
      matches          31,508    29,169        29,169       1.08x

580 µs of P3's 1,507 µs prediction, for work it does not do. `!touches_printing_field` is the test, so
`stream_scan_units` goes to 0 there; P4's `scan_units` is untouched, since it walks each span to push and its
60,705 is real. **That asymmetry is why it moves an argmin at all.**

This is the `all_match_known` gate one step weaker: that needs the whole residual to be `True`, this needs
only that it cannot vary within a card — which `name:s`, `o:`, `t:` and `cmc` satisfy as ordinary residuals.
Third place this same class of defect has been found (compose's verify tier, compose's `eval_domain`, here).

| | before | after |
| --- | --: | --: |
| regret mean, all queries | 1.52 µs | **1.43 µs** |
| `candidates / printing` mean | 1.68 | **1.45** |
| mis-picks >2 µs | 2,421 | 2,294 |
| feature-caused lost time | 27.9 ms | **24.0 ms** |
| feature-caused **max** single miss | **421.7 µs** | **272.4 µs** |
| >100 µs band, feature-caused | 54% | **36%** |

`GatheredScan vs StreamedSelect [candidates]` stays at 92% and 2.42 µs, because the class is a few hundred
rows of 11,559 pairs — the win is the tail, not the rate. Earlier runs of the aggregate spanned 1.49–1.54 µs,
so the 1.43 is only modestly outside run noise; the max-miss and band shifts are the load-bearing evidence.

**Pattern B is not fixed**: `matches` over-estimated up to **219×** on selective conjunctions
(`eur<=0.09 usd>=0.38 usd<=4.92` used 31,508 against a realized 144; `eur:0.39` 31,508 against 439). Both are
tier-4 MASK price ranges where the span base is meaningless, and both routes to an exact count were measured
and declined above.

### Pattern B traced upstream: an unnarrowed query has no base for a match estimate

`eur<=0.09 usd>=0.38 usd<=4.92` / artwork used `matches` 31,508 against a realized 144. The chain:

1. every leaf individually exceeds `MAX_NARROW_FRACTION` (0.25), so narrowing declines;
2. `eval_domain` becomes the whole corpus, 31,508;
3. `in_space * RESIDUAL_PASS_RATE_ARTWORK` gives ~24,439, and then
   **`.max(count.min(in_space))` forces 31,508** — the floor, not the rate.

The floor asserts at least one match per candidate. That holds under a tight narrowing and is catastrophic
under a loose one, which is the candidates-versus-matches confusion for the fourth time in this file.

**Removing the floor is nonetheless not a fix.** Split by whether it binds (`matches == eval_domain`):

| population | mode | n | p50 | p90 | mean \|log\| |
| --- | --- | --: | --: | --: | --: |
| floor binds | artwork | 2,883 | 1.06 | 23.25 | **0.936** |
| rate applies | artwork | 1,179 | 1.34 | 8.29 | **0.799** |
| floor binds | printing | 299 | 0.60 | 1.14 | **0.567** |
| rate applies | printing | 4,010 | 1.22 | 17.92 | **1.094** |

Worse in artwork, **better** in printing. Dropping it helps one mode and hurts the other. Two reading notes:
card mode is excluded because its `matches` *is* `count`, so a "floor binds" detector flags all of it by
construction; and the extreme tails sit in both populations (max 1,575× floored against 1,690× rated), so the
tail is not the floor's doing either.

**So the root cause is upstream of both terms.** When no leaf is selective enough for narrowing to fire, the
candidate set is the corpus and *neither* a rate nor a floor has a base related to the answer. That is 57% of
rows on this acquire. It is the same conclusion as compose's `eval_domain`, as tier 2's two dead ends, and as
the floor here: the wanted quantity is `|candidates ∩ residual|`, and nothing cheap computes it for an
unnarrowed query.

The one route not yet closed is a bitmap AND of the candidate set against an indexed residual leaf — exact,
conditional, and available on the ~9% where the residual is a single indexed leaf. Everything else on this
acquire looks irreducibly estimated.

## The largest single regret in the engine is item 6, and a mode sweep isolates it exactly

`o:creature` / artwork / order=power / limit=175 loses **508.9 µs** — the worst routing miss measured. Held
everything constant and varied only `unique`:

| mode | P3 pred | P3 meas | p/m | P3 loop | eval_domain | printing_span | printings_examined |
| --- | --: | --: | --: | --: | --: | --: | --: |
| printing | 68.1 | **56.8** | **1.20** | 55.5 | 23,155 | 61,941 | 0 |
| artwork | 92.5 | **978.1** | **0.09** | 974.0 | 23,155 | 61,941 | 0 |

**Artwork costs P3 921 µs more than printing for identical work, and the model charges 24 µs more.** That is
the whole regret, in one term: `STREAM_ARTWORK_SEEN_PER_CARD_NS` is 1.21 against a realized **39.8 ns/card**
of surcharge — or 14.9 ns/printing over the span, which is very likely the right shape.

Two things the dump makes plain.

**The work is real and uncharged.** `residual_tier_ns100` is 0 here (the oracle word index resolves
`o:creature` exactly), so P3's scan term is gated off — correctly, there being no residual to examine. But
artwork's group-and-score pass walks the candidate span *regardless of the residual*, and no term covers it.

**`printings_examined` does not count it.** It reads **0** while 61,941 printings are walked for grouping,
because the counter covers residual examinations only. That is why every feature grading in this file looked
clean on artwork: the instrument cannot see this work, so the error could only ever show up as a rate.

**This is item 6, already measured and not shipped.** `bench_streamed_loop` found the shape — artwork needs
its own per-card *and* per-printing rates, and its surcharge flips sign with printings-per-card so no additive
correction expresses it. What is new here is that it is the **largest** routing error in the engine, not a
tidy-up, and that a two-cell mode sweep sizes it without any built design.

**And the pattern-A fix in this branch made it more visible**, which is worth stating rather than discovering
later: the `scan_units × 5.97` charge that fix removes had been partly compensating for this missing artwork
term on artwork queries *with* a residual. Same structure as the verify-tier gate — a correct removal
exposing an unmodelled cost underneath. `o:creature` never had that compensation, since its tier is 0, which
is why it shows the error at full size.

### Corrected, and it was not a cost-model defect at all

The diagnosis above was wrong about the mechanism, and the correction is worth more than the original finding.
`printings_examined` reading 0 is **right**: `run_query_streamed` has a fast path that answers an artwork
count from a stored per-card group count without touching a printing. The 921 us was not a grouping walk.

It was `card_pass`. The `all_match_known` skip was **gated off for `Mode::Artwork`**, so `o:creature`/artwork
ran a full oracle-text containment check over all 23,155 candidates that the narrowing had already proved
matched. Printing mode skips it and took 56.7 us for identical work. The gate's own comment cited a ~45%
regression on `t:creature`/artwork, called it "an unexplained codegen/scheduling effect ... not a logical
cost", and attributed it to bisecting **across builds** — trap 1 in the method doc.

Re-measured with a runtime toggle in one binary, the cited regression does not reproduce in either direction,
and **every artwork cell is faster with the gate removed**:

| query, artwork, limit 175 | gate on | gate off | speedup |
| --- | --: | --: | --: |
| `o:this` | 1047.7 us | **43.8** | **24x** |
| `o:target` | 556.6 | **28.7** | **19x** |
| `o:creature` | 1010.5 | **50.6** | **20x** |
| `t:creature` | 84.1 | **38.5** | **2.2x** (the query the gate protected) |
| `o:flying` | 24.1 | 12.1 | 2.0x |
| `c:r` | 32.2 | 16.6 | 1.9x |
| `t:land` | 15.5 | 11.6 | 1.3x |

Row identity is what an `all_match` error breaks silently here — totals do not move, the printing that REPS
each artwork group does. **1,134 cells identical** (21 predicates x 3 modes x 3 orderbys x 3 pages x 2
prefers, hashing the returned `scryfall_id` sequence), 378 of them artwork, including the existential shapes
`f:*`, `border:*`, `r>=rare`, `watermark:*`, `-f:modern`.

| | before | after |
| --- | --: | --: |
| `artwork` regret slice, mean | 1.87 us | **1.59** |
| `artwork` regret slice, max | **504.5 us** | **185.5** |
| total regret, mean | 1.43 us | 1.44 (flat) |

Total regret is flat because this does not change any *pick* — it makes the picked plan faster. The user-facing
win is latency, and it is the largest in this branch.

**Item 6 inverts.** The artwork arm now **over**-costs: P3 reads p/m 1.58-1.83 on these cells against 0.09-0.11
before, so `STREAM_ARTWORK_SEEN_PER_CARD_NS` at 1.21 is now too HIGH rather than 33x too low. The cost model
was right about what should happen throughout — it charges `tier = 0` because `all_match_known` is supposed to
mean no `card_pass` runs, and now it does not. What remains is a level, not a shape, and it is small.

**The general lesson is about the comment, not the code.** A perf carve-out justified by a cross-build
measurement, explicitly labelled unexplained, sat in the hot loop of the mode carrying 36% of all routing loss
and cost up to 24x. This file has now retracted four cross-build findings; that instrument's output should be
treated as unproven until re-measured in one binary, especially where it has been encoded as a permanent gate.

### And yes, the constants need refitting — but the first correction is a gate, and it regresses routing

Removing the artwork `card_pass` gate changed what the arm should charge, so the artwork constants — fitted
against traffic that *included* the redundant `card_pass` — are now stale. P3 reads p/m **1.58–1.83** on the
tier-0 artwork cells where printing mode reads 1.02–1.23 on the same queries, so the surcharge is the suspect.

Measured as the artwork-minus-printing `ns_loop` delta on identical candidate sets, against a charged
**+1.21 ns/card**:

| regime | n | median ns/card | median ns/printing |
| --- | --: | --: | --: |
| `printings_examined == 0` (stored group count) | 9 | **−0.46** | −0.17 |
| `printings_examined == span` (dedup walk) | 8 | **+0.38** | +0.12 |

So it is a **shape** problem before it is a level one. `run_query_streamed` answers an artwork count from a
stored per-card group count when `all_match && have_group_counts` — touching no printing — and only walks the
span to dedup groups when a residual survives per printing. In the first regime artwork is *cheaper* than
printing (one array read against printing's span arithmetic), so +1.21 has the wrong **sign**, not merely the
wrong magnitude. Every `exam == 0` row is `all_match_known` or `residual_card_invariant`, including `name:s`
(exam 0, artwork cheaper by 0.52 ns/card, p/m 2.20) — the same discriminator as the two fixes above.

**Gated on that signal and measured: absolute agreement improves, routing gets worse.**

| | before gate | after gate |
| --- | --: | --: |
| P3 p/m, tier-0 artwork cells | 1.58–1.83 | **1.14–1.34** |
| artwork regret slice, mean | 1.59 µs | **1.71** |
| artwork regret slice, max | 185.5 µs | **732.5** |
| total regret, mean | 1.44 | 1.48 |

**Shipped, after the decline was retracted.** Two things overturned it.

**P4 needs no partner fix.** Measuring *both* arms' artwork-minus-printing delta — which the first attempt
never did — shows P4 is already accurate in artwork mode and P3 is the only mispriced arm:

| regime | | P3 | P4 |
| --- | --- | --: | --: |
| grouping walk does **not** run (n=9) | median surcharge | −0.52 ns/card | **−3.17** |
| | **p/m** | **1.74** | **0.98** |
| grouping walk **runs** (n=10) | median surcharge | +1.47 | −6.89 |
| | **p/m** | 1.11 | 0.87 |

P4's arm carries no mode-dependent term at all, yet lands at 0.87–0.98 — because its `matches` in artwork
mode is the *deduped* artwork count, so its `PUSH` term already shrinks by roughly the real saving. So
"+1.21 compensates for P4 being over-costed" was **false**, and there is only one arm to move.

**And the alarming max was noise.** Re-measured over a longer sample:

| run | total mean | artwork mean | artwork max |
| --- | --: | --: | --: |
| ungated, 180 s | 1.44 µs | 1.59 | 185.5 |
| gated, 180 s | 1.48 | 1.71 | **732.5** |
| gated, 240 s | 1.45 | 1.69 | **189.9** |

The 4× max does not reproduce. What survives is a consistent ~6% cost on the artwork regret slice with total
regret flat, against P3 going **p/m 1.74 → 1.25** where the walk does not run and 1.11 → 1.20 where it does.
Both arms now sit inside 0.86–1.25 in artwork mode.

Taken on the principle this file's own header states: ordering-correct-by-cancellation is a local optimum you
cannot build on, because the next change has no ground truth to check itself against. Same trade as the
non-destructive split, which was kept at a 15% regret cost for the same reason.

**The counter this needed already existed**, which is the other correction. Both kernels publish the artwork
grouping span in `printings_examined` — P4 accumulates `push_card_matches`'s return, P3 accumulates
`card_match_count`'s second value — and P3's fast path correctly reports 0 because no walk happens. An earlier
note here claimed the counter was missing and that a grouping walk was uncounted; both were wrong.

### Item 6 is now closed

Artwork's per-card surcharge was the last of it. The executor gate was removed (up to 24×), P3's surcharge is
gated on the same signal, and P4 was measured and needs nothing. What remains in artwork mode is a level
question inside 0.86–1.25 on both arms, which is not worth a refit against a warm-cache design.

## Compose was `Eq`-only on rarity, and that cost 167×

Chasing `r>=rare`/artwork (100.1 µs regret, six appearances in the top 100) found something bigger than the
routing error. Its full dump:

    r>=rare  artwork  limit=175      acquire=candidates
      GatheredScan     pred 484.4   meas 485.9   p/m 1.00   loop 482.2   ← picked
      StreamedSelect   pred 518.5   meas 381.6   p/m 1.36   loop 376.1
      eval_domain 12,887 = cards_visited     scan_units 58,656 = printings_examined (BOTH plans)

**The features are exact and both arms are individually respectable**, yet the order inverts: the model
charges P3 2.9× more per printing (5.97 vs 2.06) and P4 2.9× more per card, on the *same* 58,656 printings,
and the two errors nearly cancel into a 34 µs predicted gap the wrong way against a 100 µs real one. That is
the `SCAN_PER_ROW` ratio a pooled fit endorses, in its cleanest form.

**But routing was the small half.** Splitting the phases shows what the query actually spends:

| limit | loop (count) | finish (emit) | perm_steps |
| --: | --: | --: | --: |
| 10 | **386.6 µs** | 0.7 µs | 3 |
| 175 | **374.4 µs** | 4.8 µs | 97 |

`query()` returns `(total, page)`, so an exact `total = 20,979` requires visiting every candidate before
anything can be emitted. **99.8% of the query produces the count**, flat in page size, while producing the
rows costs 0.7–4.8 µs. Choosing the better plan wins 100 µs; not scanning at all wins 375.

**And a plan that does not scan already existed — for `Eq` only.** `is_printing_composable` matched rarity
with `CmpOp::Eq` and nothing else, so `r:rare` composed and `r>=rare` fell to a full candidate scan:

| query | before | after | speedup |
| --- | --: | --: | --: |
| `r>=rare` / printing | 349.6 µs | **2.1** | **167×** |
| `r<=uncommon` / printing | 378.8 | **1.9** | **199×** |
| `r>uncommon` / printing | 344.5 | **2.1** | **164×** |
| `r>=rare` / artwork | 487.7 | **82.7** | **5.9×** |
| `r<=uncommon` / artwork | 421.7 | **125.7** | 3.4× |
| `r>uncommon` / artwork | 531.7 | **91.4** | 5.8× |

Printing mode becomes a popcount with no scan at all; artwork still pays the printing→artwork projection,
which is why it is 3–6× rather than 100×.

`rarity_cmp_leaf_bits` enumerates the closed domain — four interior planes plus the sparse tail's postings,
one definition shared with `walk_rarity_orderby_page` via `rarity_ints_present` — and `Or`s the values that
satisfy the op. Two properties fall out. **NULL rarity is excluded for free**, since a rarity-less printing is
in no plane and no posting, which is the trivalent answer for every op including `Ne`. And this is **strictly
more capable than `compile_plane`'s** `compile_rarity_cmp`, which shares one "above mythic" plane and declines
`BucketVerdict::Ambiguous` whenever special must be told from bonus — compose reads those two apart from their
own postings, so it has no ambiguous case.

Row identity: **1,566 cells identical** — every rarity op, negations, conjunctions, `r:special`/`r:bonus`,
three modes × three orderbys × three pages × two prefers, hashing the returned `scryfall_id` sequence.
Regret mean 1.45 → **1.40 µs**; the compose acquire grows 7,532 → 8,636 queries as intended, and its share
goes 22% → 27% because more queries live there now, at a much lower absolute cost.

**The generalisable point**, given compose is meant to become the universal exact evaluator (#731): an
applicability gate that is narrower than the machinery behind it is invisible to every routing metric. The
router never sees the plan, so no regret figure, no pairwise ordering and no feature grading can report it —
`r>=rare` looked like a 100 µs cost-model bug and was a 375 µs missing-plan bug. Auditing
`is_printing_composable` leaf by leaf against what `compose_printing_bits` can actually build is likely to
find more; `printing_compose / card` is now the worst cell at mean 2.55 µs and is the place to look next.

## Sparse compose gather: attempted, and the blocker is now named exactly

`usd>=0.42 usd<=0.43`/artwork appeared in the top 100 at 112 µs of regret. The dump says the regret figure
is nearly irrelevant:

    acquire=printing_compose
      PrintingCompose   pred  105.9   meas    —      PICKED, then DeclineSparseExact
      StreamedSelect    pred  988.2   meas 1249.8    exam 97,206
      GatheredScan      pred 1058.2   meas 1161.5    exam 97,206
      matches (estimated) 20,411      result_total (actual) 837

The router picked compose, compose built the bitmap, **discarded it**, and dispatch re-derived everything with
a full-corpus scan — 97,206 printings examined to return 837 matches, ~1,250 µs. The 112 µs the matrix reports
is only the P3-vs-P4 difference among the fallbacks, because **regret compares only plans that ran** and a
declining plan accumulates no trials. Second time today that blind spot hid the larger finding, after
`r>=rare`.

[local-engine-sparse-compose-gather.md](./local-engine-sparse-compose-gather.md) had already designed the
fix — `gather_composed_page` in place of the `return None` — and verified it byte-identical over 127,640
queries. Its stated blocker was that `plan_cost` could not price the path, and this stack added the
`ComposePaging::Gather` arm, so it looked unblocked.

**Tried it. Two edits, not one:** the fastpath gathers, *and* `compose_paging_for` must predict `Gather`
rather than `Decline`, since a `Decline` prediction costs infinity and keeps compose out of the argmin so the
gather would never run. Rows verified again on this build: **2,304 cells identical** over the sparse
two-sided ranges the change enables plus broad controls, four orderbys including the tie-heavy `usd` and
`rarity`, deep offsets, both prefers.

**And routing got worse, so it is reverted:**

| | decline | gather |
| --- | --: | --: |
| regret mean | 1.43 µs | **1.55 (+8%)** |
| `printing_compose` miss% | 7% | **18%** |
| `printing_compose` mean | 1.69 | **2.43** |
| `printing_compose` p90 | **0.00** | **7.92** |

**The blocker is not the cost arm — it is that the prediction cannot tell the query is sparse.**
`compose_paging_for` branches on `result_total`, which is the *estimate*: 20,411 against a true 837. So it
predicts `Perm` and prices a cheap permutation walk while the executor runs a gather. The arm is fine; it is
being handed the wrong branch. That is the same number as the original doc's p10 0.64 → 0.14.

**Which makes the real prerequisite a two-sided range.** `bare_range_bounds` matches one comparison, so
`usd>=0.42 usd<=0.43` composes as `And` of two one-sided slices — the estimate multiplies the two sides
instead of intersecting one interval. Fusing them fixes the estimate *and* the build cost at once, and only
then does sparse-gather become predictable. That supersedes "widen `bare_range_bounds` for multi-leaf ranges"
as an `eval_domain` idea — it was the wrong motivation for the right change.

Measured, the fusion turned out to be worth more than a prerequisite, for a reason with nothing to do with
compose: the narrowing already handles a selective range and the `And` never reaches it, so
`usd>=0.42 usd<=0.43` cost **1,146.8 µs** against **26.7 µs** for a one-sided range returning *more* rows.
Both halves have now shipped — `4991759` for the narrowing (15–33× on that population) and `7374e19` for
compose's builders, where the estimate was 38.5× off a count that was two binary searches away. Together:
regret 1.42 → 1.30 µs, and the fusible traffic slice to 0.81× of baseline. The evidence, the noise analysis
behind the sparsity gate, and what is left (the sparse gather, now that its prediction is correct):
[local-engine-two-sided-range-fusion.md](./local-engine-two-sided-range-fusion.md).

## A note on the test counts quoted throughout

`149 debug / 148 release` is not a flake. The difference is exactly one test,
`tests::arith_tuple_key_budget_catches_a_blown_domain`, which asserts a `debug_assert` tripwire and so is
compiled out of a release build. That is why both profiles are run and quoted separately: CI's `rust-test`
job is a debug build, and a release-only local run silently skips the engine's `debug_assert` guards.
