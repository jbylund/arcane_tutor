# GatheredScan/card: Printing-Varying Leaf Scan Depth

Extracted from item 1 of
[local-engine-cost-model-cleanup-remaining.md](local-engine-cost-model-cleanup-remaining.md) once it
became an ongoing iteration ledger rather than a single-pass fix. Base branch for all work here is
`engine-cost-model-cleanup`, never `main`.

## Problem

**Population**: `GatheredScan`/`unique=card` is the worst-agreement, highest-frequency cell in the
whole cost model — by frequency alone it dominates routing regret more than any other cell.

**What we already know**: the shape-level breakdown (bucketing `printing_compose`/card-mode queries by
AST shape) found `and-2` and `and-3` — specifically pairs/triples over printing-varying fields
(`price_eur`, `price_usd`, `price_tix`, `collector_number_int`, `released_at`, and mixed pairs like
`card_color_identity + price_tix`) — carrying the bulk of the remaining magnitude-weighted error
(tens of millions of units each, at investigation time). These don't qualify for the prior session's
card-invariant depth-1 fix: a printing-varying field has no "first printing settles it" guarantee (a
card matching `price_usd<5` can have OTHER printings that don't), so they still fall through the flat
`domain_cards * printings_per_card * COMPOSE_CANDIDATE_SPAN_BIAS` fallback in the `scan_all` closure
(`card_engine/src/lib.rs:11616`; constant at `:11041`, currently 2.1) — and that formula prices every
card as if it needed its *average* reprint history walked, regardless of how selective the predicate
actually is at the printing level.

Two starting-point ideas, either or both may end up in the ledger below:

1. **Match-density depth proxy.** The query's own printing-level match density (`matches /
   domain_cards` — average number of matching printings per matching card) is a much better proxy for
   expected scan depth than the corpus-wide `printings_per_card` average. A per-card-first-match
   expectation, using order statistics on the position of the first match among a card's printings, is
   the natural model: `expected_depth ≈ (avg_printings_per_card + 1) / (avg_matches_per_matching_card +
   1)`, capped at the card's own span. `COMPOSE_CANDIDATE_SPAN_BIAS` was fit against the OLD flat-average
   shape and should be re-derived (likely much closer to `1.0`) once the depth term itself carries real
   selectivity information, not stacked on top of the new term unchanged.
2. **Per-leaf independence-product combination.** A generalization of (1) to multiple printing-varying
   leaves at once: combine each leaf's own printing-level selectivity via an independence product
   (with a fudge factor) rather than a single aggregate depth term — see Constraints below for why this
   needs an explicit correlation guard before it can be trusted.

## Constraints

- **Pre-computation over hot-path computation, hard requirement.** This repo has a specific, measured
  precedent for what goes wrong otherwise: relaxing `compose_printing_estimate`'s `best_other`
  intersection threshold from `>=2` to `==1` closed a logical gap but caused a **23.6x acquire-time
  regression** (875ns → 20,646ns median) on the newly-admitted population, because it added an
  unconditional `eval_planes`/`popcount_with_bits` pass paid by every query in that population
  regardless of whether the tightening ever changed the routing decision. Reverted; see
  [local-engine-cost-model-cleanup-remaining.md](local-engine-cost-model-cleanup-remaining.md)'s
  "Explicitly considered and rejected" section for the full account — link, don't restate it. Any new
  idea here must trace every new number to an existing precomputed index/table/constant, not a new
  per-query scan whose cost grows with match/printing/candidate count.
- **Price-triple correlation risk.** `price_usd`, `price_eur`, and `price_tix` are near-identical market
  values expressed in different currencies/units — they are NOT independent. An independence-product
  combination across this triple (or any pair of them) will badly underestimate the true joint count.
  Any independence-style idea must be explicitly tested against this triple before being trusted, not
  just against `collector_number_int`/`released_at`-shaped queries. (Power/toughness correlation is
  already handled exactly elsewhere via `arith_tuple_count` — not a risk in this population, no need to
  re-verify it here.)
- **Out of scope, hard**: `card_engine/src/estimator.rs` (its `estimate_cardinality` is live at
  `lib.rs:11146` behind the `STREAM_MIN_MATCHES` gate — editing it can move a shipped routing decision,
  and its `compose_and` independence estimator is unwired PR1 of #702, validated for soundness only).
  Items 2-4 of the parent punch-list doc. `Mode::Printing`/`Mode::Artwork`. Anything outside `lib.rs`,
  `cost.rs`, `tests.rs`, and this doc.

## Current best

As of Round 0 (baseline, `engine-cost-model-cleanup` @ `97dc30c8`), nothing from this doc has shipped
yet — the fix is still the flat fallback described above. Baseline measured against an isolated release
build (`maturin build --release`, extracted wheel, `PYTHONPATH`-pinned — never `maturin develop` into
the shared `.venv`, which silently redirects every other session's `import card_engine`):

```
GatheredScan   card   n=35,074   median 0.67   p10 0.25   p90 2.75   16% within 25%   FAIL
```
(`.venv/bin/python scripts/bench_cost_model_agreement.py --seconds 300 --seed 0`, run from a
`costcell/00-baseline` worktree branched off `engine-cost-model-cleanup`.)

As of Round 1 (match-density depth proxy, `costcell/01-depth-proxy`), the flat fallback is replaced by
`domain_cards * expected_depth * COMPOSE_CANDIDATE_SPAN_BIAS` where `expected_depth = (printings_per_card
+ 1) / (density + 1)`, `density = printing_matches / domain_cards`, and `COMPOSE_CANDIDATE_SPAN_BIAS` is
refit to `0.7`. Same protocol:

```
GatheredScan   card   n=33,944   median 0.72   p10 0.25   p90 3.20   17% within 25%   FAIL
```

Still FAIL by the [0.8, 1.25] median bar — see Round 1 below for why the whole-cell number barely
moves despite a real, controlled improvement in the feature itself.

**As of Round 9 (`costcell/trunk` @ `58eebfdc`), the cell has crossed from FAIL to PASS** — the first
time since this doc opened. Independently re-measured (fresh isolated build, same protocol, not just
the shipping round's own self-reported numbers):

```
GatheredScan   card   n=35,132   median 0.81   p10 0.44   p90 2.99   26% within 25%   PASS
```

Nine rounds landed six kept, held-out-validated fixes (Rounds 1, 3, 4, 6, 7, 9) and one clean
mathematical rejection (Round 2), spanning two previously-separate root causes: the printing-varying
range-leaf family (`compose_printing_estimate`/`scan_all`'s feature estimation, `lib.rs`) and, as of
Round 9, `GatheredScan`'s own cost FORMULA (`cost.rs`) under-charging zero-match `candidates`-acquire
queries by ~4x. The 26%-within-25% figure is still well short of the 90%-within-10% aspiration this
doc opened with — Round 8's diagnostic identified two more concrete, unaddressed mechanisms
(card-mode's unconditional `matches = count` ignoring residual selectivity, and an `Or`/negation
population invisible to this benchmark's flat-conjunction sampling) as the next candidates.

As of Round 3 (`COMPOSE_RANGE_AND_CLUSTER_BIAS`, `costcell/03-cluster-bias`), the `est_cards` fallback
for an `And` of 2+ different-index printing-varying range leaves (the ~37% subset Round 1 identified
as ceiling-capped, and Round 2 proved no independence-product combination can fix) uses its own
clustering-bias constant, `1.1`, instead of `COMPOSE_CARD_ESTIMATE_BIAS`'s `1.78`. Held-out paired-diff
(1,500 and2/and3 RANGE_FAMILIES queries, `unique=card`, hash-of-query split): 433 improved / 117
regressed / 192 tied, total absolute `scan_units` error 8.60M → 8.02M on the held-out half. Same
`GatheredScan`/`card` FAIL as before on the single-run agreement gate — see Round 3 below for why that
is expected and not a sign the fix did nothing.

As of Round 4 (`COMPOSE_RANGE_AND_BROAD_SCAN_SCALE`, `costcell/04-broad-guard`), the LATER
`range_too_broad_to_narrow` guard's full-corpus reset (see Round 3's mid-investigation finding above)
scales `scan_units` alone down to `0.7 * n_printings` whenever `is_cross_index_range_and` holds --
`eval_domain` is left at the full `n_cards`, confirmed exact (0 total absolute error against the real
`cards_visited` counter) for this population, not merely assumed. Held-out paired-diff (372/1,500
guard-fired rows, hash-of-query split): 166 improved / 15 regressed / 0 tied, total absolute
`scan_units` error 5.40M → 1.90M on the held-out half. Same `GatheredScan`/`card` agreement-gate FAIL
as before (16%, unchanged) — this guard-fired subset is a small slice of that pooled cell, same
reasoning as Round 3.

As of Round 6 (`COMPOSE_BARE_RANGE_BROAD_SCALE`, `costcell/06-bare-range`), the `CardRangePopcount`
arm's OWN `range_too_broad_to_narrow` reset -- a bare single range leaf under `unique=card`, a
completely separate acquire branch from Rounds 3/4's `PrintingCompose` target, and Round 5's
diagnostic finding of the single largest bucket in the whole pooled cell (53.7% of error) -- scales
`scan_units` alone down to `0.43 * n_printings`. `eval_domain` is left untouched: 96.6% of rows read
exactly 1.0 (mean 0.975; the small tail is real, driven by price-field null-exclusion, not chased).
Held-out paired-diff (3,500 guard-fired rows, hash-of-query split): 1,704 improved / 31 regressed,
total absolute `scan_units` error 93.3M → 16.0M on the held-out half. Same `GatheredScan`/`card`
agreement-gate result as Rounds 3/4 (15-17%, essentially unchanged) -- this is the largest single
lever fixed so far by pooled-error share, and it still barely moves the headline number, confirming
that gate's grain is simply too coarse to see any single arm's fix, not that this fix is inert.

As of Round 7 (`COMPOSE_SAME_RANGE_BROAD_SCAN_SCALE`, `costcell/07-candidates-range`), `PrintingCompose`'s
OWN `range_too_broad_to_narrow` reset -- the rest of Round 5/6's "single:range" bucket that Round 6's
`CardRangePopcount` fix never reaches, because a bare range fails `card_range_popcount_applicable`
whenever no sort permutation exists for the query's orderby/direction, and a fused two-sided bound
(`eur>=0.23 eur<=0.45`) never reaches `CardRangePopcount` at all -- scales `scan_units` alone down to
`0.52 * n_printings`, gated on a new `is_same_index_range_only` (bare leaf or same-field `And`, as
opposed to `is_cross_index_range_and`'s different-index `And`). `eval_domain` is left untouched:
exact at 1.000 mean/median. Held-out paired-diff (13,053 guard-fired rows, hash-of-query split): 6,422
improved / 33 regressed, total absolute `scan_units` error 304.8M → 57.6M on the held-out half. Same
`GatheredScan`/`card` agreement-gate result as every prior round (17-18%, essentially unchanged) --
this population turned out to be even larger by row count than Round 6's, and still barely moves the
headline number, same grain argument as Rounds 3/4/6.

As of Round 9 (`GATHER_FIXED_COST_ZERO_MATCH_NS`, `costcell/09-zero-match`), `PhysicalPlan::GatheredScan`'s
cost arm (`cost.rs`, not `lib.rs` — the first fix in this doc that lives in the cost FORMULA rather
than feature estimation) charges `42.0` instead of `GATHER_FIXED_COST_NS` (`169.6`) whenever `matches
== 0`, gated the same way the arm's own `tier_ns > 0.0` neighbor already is. Targets Round 8's
mechanism 1: a `Prep::Candidates`-acquired zero-match round collapses every OTHER term in the arm to
zero, so the whole prediction used to read as `GATHER_FIXED_COST_NS` alone — confirmed independently
(fresh 31,030-row sample, 9,890 zero-match, every non-fixed term exactly 0). Held-out paired-diff
(hash-of-query split, 9,890 zero-match rows): calibration half (n=4,944) sets the constant to its
median measured `plan_self_ns`, 42.0; held-out half (n=4,946) reads 4,577 improved / 369 regressed / 0
tied, total absolute ns error 530,256 → 103,110 (5.1x), median ratio 0.248 → 1.000, within-25% 0.1% →
57.7%. `GatheredScan`/`card` agreement-gate cell moves from 11% to 30% within [0.8, 1.25] (median 0.57
→ 0.77) — still FAIL by the median bar (0.77 < 0.8) but the largest single-round movement of this
number since Round 0, unlike every range-family round's "same 15-18%, unchanged" result; the by-unique
`GatheredScan`/`card` cell flips FAIL → PASS (0.69 → 0.80). See Round 9 below for the residual-risk
caveat this round found and verified as immaterial (a shared-`PlanFeatures` edge case in the
RANGE_ACQUIRES-forced-competitor population, checked against `bench_regret_matrix.py` and found to move
total regret by 0.0 ms).

## Iteration ledger

| # | Idea | Outcome | GS/card within-25% | Other cells | Notes |
|---|------|---------|--------------------|-------------|-------|
| 0 | (baseline, `engine-cost-model-cleanup` @ `97dc30c8`) | — | 16% | — | n=35,074, median 0.67, p10 0.25, p90 2.75 |
| 1 | match-density depth proxy | kept | 16% → 17% (noisy, uncontrolled) | none, within run-to-run noise | paired-diff (controlled): 946 impr / 544 regr, 29.6M → 9.86M abs `scan_units` error; `BIAS` refit 2.1 → 0.7 |
| 2 | independence-product `domain_cards` for 2+ different-index range leaves | rejected at self-check | n/a (no code shipped) | n/a | printing-space variant: 38 impr / 496 regr, 17.3M → 18.1M abs error (worse); card-space variant: 0/1500 changed (mathematically incapable of firing) — see Round 2 below |
| 3 | second clustering-bias constant (`COMPOSE_RANGE_AND_CLUSTER_BIAS`) for the same shape | kept | n/a (see Round 3 below — noisy at this cell's grain) | none, within run-to-run noise | held-out paired-diff (controlled): 433 impr / 117 regr, 8.60M → 8.02M abs `scan_units` error; new bias 1.1 against `COMPOSE_CARD_ESTIMATE_BIAS`'s 1.78 |
| 4 | downward `scan_units` scale (`COMPOSE_RANGE_AND_BROAD_SCAN_SCALE`) for the `range_too_broad_to_narrow`-fired subset of the same shape | kept | n/a (see Round 4 below — noisy at this cell's grain) | none, within run-to-run noise | held-out paired-diff (controlled): 166 impr / 15 regr / 0 tied, 5.40M → 1.90M abs `scan_units` error; new scale 0.7; `eval_domain` left untouched (measured exact, 0 error) |
| 5 | diagnostic: re-bucket remaining error by AST shape | diagnostic | n/a (no code shipped) | n/a | see Round 5 below — fresh magnitude-weighted bucketing (n=30,892) finds 74.9% of all pooled `scan_units` error sits in the `range_too_broad_to_narrow` broad-guard reset FIRING OUTSIDE `is_cross_index_range_and` (a population Round 3/4's own comment already flagged as unscaled on purpose); Rounds 1-4's target shape drops to 2.3% of pooled error, median ratio 1.00 — confirming the shipped fixes worked, just on a small slice of the cell |
| 6 | downward `scan_units` scale (`COMPOSE_BARE_RANGE_BROAD_SCALE`) for the `CardRangePopcount` arm's own `range_too_broad_to_narrow` reset (single bare range leaf, `unique=card`) | kept | 15-17% both builds, unchanged (noisy at this cell's grain, same as Rounds 3/4); the finer `GatheredScan/card_range_popcount` sub-row moved 47%→52% within [0.8,1.25], median 0.94→1.05 | none, within run-to-run noise; regret matrix unchanged (96% `printing_compose` share both builds) | held-out paired-diff (controlled): 1,704 impr / 31 regr, 93.3M → 16.0M abs `scan_units` error; new scale 0.43; `eval_domain` left untouched (96.6% of rows exactly 1.0, mean 0.975 — a real but small tail from price-field null-exclusion, not chased); flags the sibling `else` branch's `scan_units = card_est` as itself badly under-calibrated (median ratio ~0.25-0.37 by field) — not fixed this round, out of scope, noted for a future round |
| 7 | downward `scan_units` scale (`COMPOSE_SAME_RANGE_BROAD_SCAN_SCALE`) for `PrintingCompose`'s OWN `range_too_broad_to_narrow` reset, gated on a NEW `is_same_index_range_only` (bare single range leaf, or a fused same-field two-sided bound) — the rest of Round 5/6's "single:range" bucket that `CardRangePopcount` never reaches | kept | 17-18% both builds, unchanged (noisy at this cell's grain, same as every prior round); the pooled `GatheredScan/printing_compose` row (all `unique` modes) unchanged at 24% both builds — expected, small slice of a much larger diverse pool | none, within run-to-run noise; regret matrix unchanged (95% `printing_compose` share both builds) | held-out paired-diff (controlled): 6,422 impr / 33 regr, 304.8M → 57.6M abs `scan_units` error; new scale 0.52; `eval_domain` left untouched (measured exact, median/mean 1.000); population confirmed to be a SEPARATE, independently-broken slice of "single:range" from Round 6's, reached via a different acquire branch (`printing_compose`, not `card_range_popcount`) for two independent reasons — see Round 7 below |
| 8 | diagnostic: bucket candidates-acquire `GatheredScan`/`card` error by shape | diagnostic | 13% (n=22,190, median 0.60), unchanged from checkpoint — expected, no code shipped | n/a | see Round 8 below — pivots off the printing-range-index family entirely (Rounds 1-7's whole target) onto `Prep::Candidates`, the OTHER acquire branch feeding this same pooled cell. Finds `eval_domain` exact (median 1.00 against `cards_visited`) and `scan_units` also near-exact-to-UNDER-predicting (median 1.00, several high-magnitude buckets 1.2-1.8x, i.e. real work exceeds the estimate) — the OPPOSITE direction from the pooled ns-space over-cost (median 0.49-0.60), so neither size feature is the culprit; the bug is in how `GATHER_*` rate/fixed constants convert those (correct) features into ns for the `candidates` (and sibling `plane`) acquire branch specifically. Two concrete mechanisms found: (a) `GATHER_FIXED_COST_NS` (169.6ns) is ~4x too high for the 32% of the sample with zero matches (median measured 42ns); (b) card-mode's `feats.matches = count` (unconditional, `candidate_feats`, lib.rs~11776) ignores real residual selectivity — `is:vanilla`-shaped high-selectivity residuals push 2-3% of the predicted match count, and the whole per-candidate verify-tier charge (`GATHER_CARD_PASS_NS + max(tier_ns, GATHER_RESIDUAL_FLOOR_NS)` × `eval_domain`) doesn't discount for short-circuit-driven cheap-average-case cost the way real `card_pass` behaves at low match rates. A THIRD population invisible to `bench_cost_model_agreement.py`'s own flat-conjunction sampler — Or/negation/nested-paren structures via `structured_query()` — shows the opposite tail shape (median near 1.0, p90 1.25-3.48x UNDER-cost) and needs its own round. |
| 9 | lower fixed cost (`GATHER_FIXED_COST_ZERO_MATCH_NS`) for `PhysicalPlan::GatheredScan`'s zero-match rounds, gated on `matches == 0` the same way the arm's `tier_ns > 0.0` neighbor is gated — the first fix in this doc inside `cost.rs`'s cost FORMULA rather than `lib.rs` feature estimation | kept | 11% → 30% (n=38,435→38,889, median 0.57→0.77) — largest single-round movement since Round 0; by-unique `GatheredScan`/`card` cell flips FAIL (0.69) → PASS (0.80) | `GatheredScan/printing_compose` unchanged (median 1.15→1.14, 24%→24%); `GatheredScan/printing_range_scan` and `/card_range_popcount` unchanged; `bench_regret_matrix.py` total regret unchanged (27.6ms both builds); `bench_query_latency_ab.py` same-build canary swings by a comparable magnitude to the real A/B diff (-0.2µs vs -0.3µs) — no real latency effect claimed | held-out paired-diff (hash-of-query split, 9,890 zero-match rows): calibration half (n=4,944) median measured `plan_self_ns` sets constant to 42.0; held-out half (n=4,946) 4,577 impr / 369 regr / 0 tied, 530,256 → 103,110 abs ns error (5.1x), median ratio 0.248 → 1.000, within-25% 0.1% → 57.7%. Confirmed a real risk this round could not fully close within its `cost.rs`-only blast radius: `plan_cost` costs EVERY candidate plan from ONE shared `PlanFeatures` per acquire (`lib.rs:12917`), so `matches == 0` also fires for `GatheredScan` costed as a competitor/picked plan under `printing_compose`/`card_range_popcount`/`printing_range_scan` (RANGE_ACQUIRES) acquire, where `eval_domain == 0` is an unset accounting default rather than a real empty candidate list, and dispatch pays a real (sometimes large, e.g. 4,959ns median for one `printing_compose` slice) `prepare_candidates` rebuild this arm has no term for at all — pre-existing (already 29x under-predicted before this round) and NOT introduced by this fix, but made numerically worse in isolation (29x → 118x under on that slice). Checked for real routing impact directly (a same-build wheel diff on two flip cases, `date<1993-08-05`/`tix<0.01` under `printing_range_scan`) and via `bench_regret_matrix.py` (total regret 27.6ms unchanged) and `bench_cost_model_agreement.py` (no other cell moved) — no measurable regression found, but the gate is a correlated proxy, not the exact phenomenon, for this sliver of RANGE_ACQUIRES rows; flagged for a future round that can touch `lib.rs` to add an acquire-branch-aware feature |
| 28 | scope `COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` (Round 4) and `COMPOSE_SAME_RANGE_BROAD_SCAN_SCALE` (Round 7) to `Mode::Card` only, leaving `Mode::Printing`/`Mode::Artwork` at the pre-existing unscaled `n_printings` ceiling | kept | not this doc's own metric (see below) | pooled `scan_units` feature accuracy (`bench_feature_accuracy.py`), the metric a fresh `main`-vs-`costcell/trunk` A/B (Round 27) found regressed: median 0.70 (UNDER-COUNTS) → 0.94 (clean), against `main`'s own 1.00 | see "Round 28" narrative below — both scales were fit exclusively on `unique=card` samples (each round's own doc says so) but applied unconditionally to all three modes; `Mode::Printing`/`Mode::Artwork`'s real `printings_examined / n_printings` reads EXACTLY 1.000 (zero spread) for this guard-fired population, so the card-only-derived scale was silently manufacturing an under-count for two modes it was never calibrated against |
| 30 | `STREAM_SMALL_TOTAL_REDO_BIAS`, a `stream_scan_units` correction for `printing_compose`'s bare `else` arm (`Mode::Card`, no legality partner) — Round 1's `scan_units` revision was inherited verbatim by `StreamedSelect`'s own feature, which structurally under-prices a SECOND, unmodeled `push_card_matches` pass `run_query_streamed`'s small-total branch pays and `GatheredScan` never does | kept, partial | n/a (this doc's own agreement-gate metric untouched; see the flip/regret numbers below instead) | `#852` ordering 88%→88% clean; Round 28's pooled `scan_units` median 1.00→1.00 clean | see "Round 30" narrative below — of 114 reproduced f3f4a017 flip queries, 50 (44%) now correctly re-route to `GatheredScan`; `StreamedSelect -> GatheredScan` regret matrix slice -7% share of traffic / -12% regret-ms; residual traced to the acquire-time `result_total` ESTIMATE itself being unreliable near `STREAM_MIN_MATCHES` for cross-index-range Ands (this doc's own Round 1 "separate, uninvestigated `domain_cards` bug" flag) — not a `cost.rs` rate problem, so chunk 2 (rate refit) is unlikely to close the rest on its own |
| 32 | new `PlanFeatures::perm_walk_span` feature (`cost.rs`/`lib.rs`) for `StreamedSelect`'s OTHER branch (`walks_permutation`, `total > STREAM_MIN_MATCHES` — different from Rounds 30/31's small-total gather): `perm_steps`'s estimate multiplied by `n_cards` unconditionally, when the real executor already bounds its walk to the filter's own interval on the sort column | kept | n/a (not this doc's metric; see below) | `#852` 88%→89% clean; Round 30/31 territory (`StreamedSelect -> GatheredScan` regret slice) flat; Round 28's `scan_units` unreachable by this change | see "Round 32" narrative below — held-out mean \|log ratio\| 1.033→1.001 pooled (both halves improve independently); `StreamedSelect/candidates` cost-model-agreement cell unchanged (median 0.59 both builds) because the targeted correlation (filter bounds the same field the query orders by) is rare under uniform traffic; shipped as a strict-generalization correctness fix (collapses to the old formula when unbounded), not for measured impact on this specific cell |

### Round 1

The self-check (constraint 3 in the parent doc) cleared cleanly: every new number
(`printing_matches` = `est.result.printing`, `domain_cards`, `printings_per_card`) was already
computed before `scan_all` runs, so the fix is a handful of extra float ops, not a new scan --
confirmed by the correctness/latency gates below showing no execution-time effect.

The surprising part showed up in the paired-diff, not the self-check: for **~37% of the and2/and3
RANGE_FAMILIES sample**, `domain_cards` itself (the candidate-card estimate this fix multiplies by a
depth term) is already smaller than `domain_cards * printings_per_card` -- i.e. smaller than the
*maximum possible* span for that many candidate cards -- while the REAL measured
`printings_examined` exceeds even that ceiling. No depth formula operating on top of `domain_cards`
can fix those rows; the error is upstream, in `compose_printing_estimate`'s own candidate-card count
for an And of several different-index range leaves (price/cn/released_at each have their own index,
so an And across them doesn't hit the same-index fusion or the plane-based tightening this session's
prior work added -- it falls back to `calibrated_balls_into_bins` on the min-folded printing match
count). Restricted to the other ~63% (`domain_cards` not the bottleneck), the fix moves within-25%
from 7.5% to 36.7% and the median from 2.67 to 0.92 -- a real, large improvement, just capped by a
separate, uninvestigated `domain_cards` bug for multi-range-index Ands. That bug is the natural next
target, since it bounds how far ANY depth-side fix here can go.

The primary gate (`bench_cost_model_agreement.py`, single uncontrolled 300s run each) moved the
target cell only 16% -> 17%, which looks like noise rather than signal on its own -- confirmed as
noise by checking UNRELATED cells this change cannot touch (`CardRangePopcount` 59% -> 66%,
`GatheredScan`/`candidates` 12% -> 15%, `StreamedSelect`/`printing_compose` p90 1.72 -> 2.98) moving
by comparable or larger amounts between the same two runs, purely from the two 300-second windows
sampling a different number and mix of queries. The paired, same-query-set diff (1,500 shared queries,
identical rng seed against both builds) is the only one of the two that actually isolates this
change's effect, and it shows the real number.

### Round 2

Target: fix `domain_cards` itself for the ~37% subset Round 1 identified as ceiling-capped — an
And of 2+ DIFFERENT-INDEX printing-varying range leaves (`price_usd`/`price_eur`/`price_tix`/
`collector_number_int`/`released_at`, each its own separate index), where `exact_result_total`
returns `None` (no pair-table/arith-tuple/plane-compile coverage exists for this combination) and
`est_cards` falls back to `calibrated_balls_into_bins(printing_matches, n_cards)` on the min-folded
(loosest) printing match count.

**Self-check (constraint 3):** the only new per-query work in either variant tried is one extra
`PrintingValueIndex::range` partition-point probe per distinct range field the And names (`O(log
n)`, bounded by query length, never by match/printing/candidate count), reusing the exact same
lookup `bare_range_bounds`'s other callers already pay per leaf — paid only after `exact_cards` has
already declined. Every number traces to an existing precomputed structure (`PrintingValueIndex`,
`calibrated_balls_into_bins`/`COMPOSE_CARD_ESTIMATE_BIAS`), no new per-query scan. This cleared the
self-check; the idea failed on the empirical paired-diff instead, described below.

**Attempt 1 — printing-space independence product.** Combined each leaf's own exact printing-match
selectivity (`k_i / n_printings`) via `Π(k_i / n_printings) * n_printings`, excluding any
combination touching 2+ of the price triple (checked directly: `price_usd<5 price_eur<4`-shaped
queries are a real correlation risk, confirmed on a 500-query price-only paired diff below, though
moot once the whole approach failed). Wired in as a `.min()` against the existing min-fold, feeding
both `est_cards`'s fallback and (to keep the pair internally consistent) the Round-1 depth term's
`density` numerator.

Paired diff (1,500 shared and2/and3 RANGE_FAMILIES queries, unique=card, identical seed against
baseline `costcell/trunk`@`f3f4a017`): **38 improved / 496 regressed / 966 tied, total abs
`scan_units` error 17.3M → 18.1M (worse), within-25% 11.8% → 8.3% (worse)**. Price-triple-only
subset (500 queries, `usd`/`eur`/`tix` and2/and3): 0 improved / 0 regressed — the correlation guard
worked exactly as designed (no combination in that subset reached the independence branch), but
this is moot given the whole-population result.

**Why it failed, and why no independence variant can work here:** a probability-product
combination is mathematically bounded above by its smallest single factor whenever every factor is
`<= 1` (`Π p_i <= min(p_i)`) — so ANY such combination can only ever SHRINK an estimate relative to
the tightest single-field bound, never raise it. Checking the baseline's own error DIRECTION on the
1,500-query sample confirms this is the wrong direction for the dominant failure mode here: 866/1500
rows (58%) already UNDER-estimate `scan_units`, only 450/1500 (30%) over-estimate, and Round 1's own
investigation established that `domain_cards` for this population is a FLOOR that undershoots the
true candidate span, not a ceiling that overshoots it (`printings_examined` exceeding even `domain_cards
* printings_per_card`). A transform that can only shrink an already-too-small number cannot fix it;
it just pushes the under-estimating majority further from the truth while incidentally helping the
smaller over-estimating minority, netting negative overall — exactly the 38/496 split measured.

**Attempt 2 — card-space independence product**, tried as "the closest variant" once attempt 1's
mechanism was understood to be structurally wrong-directioned: instead of combining printing-space
selectivities, combine each leaf's own CARD-space estimate (`calibrated_balls_into_bins(k_i,
n_cards)`), on the theory that "this card has SOME printing satisfying leaf i" is a weaker,
superset condition of the filter's real "one printing satisfies every leaf" semantics, so the
combination should be able to raise the estimate instead of shrinking it. Wired in as `.max()`
against today's fallback. Empirically: **0/1500 changed** — the `.max()` branch never won even
once. This confirms the same math from the opposite direction: each leaf's own card-space estimate
is itself `<= n_cards`, so the product-of-fractions form is bounded by the smallest such factor
regardless of which space it is computed in — moving to card space changes what each factor MEANS,
not the shape of the ceiling the combination is stuck under.

**Conclusion:** rejected at self-check-plus-paired-diff. Both variants respected the
pre-computation constraint (no new per-query scan class) but neither can fix a floor-too-low bug by
construction — this rules out the whole "independence product" family for this specific target, not
just a tuning miss. No code committed; both attempts were reverted (`git checkout --
card_engine/src/lib.rs` against `costcell/trunk`, confirmed clean via `git diff --stat`).

**Next steps for a future round:** the diagnosed bug (domain_cards undershoots the true candidate
span for this shape) needs a mechanism that can RAISE the estimate, which independence-style
combination cannot do. Two candidates worth checking before another attempt: (a) a flat,
shape-specific multiplicative correction on top of `calibrated_balls_into_bins`'s output — same
precedent as `COMPOSE_CANDIDATE_SPAN_BIAS`/`COMPOSE_CARD_ESTIMATE_BIAS` — but fit with a proper
calibration/held-out split (not the same 1,500-query sample used to diagnose the bug, per this
repo's benchmark-methodology rule); (b) investigating whether the undercount's true source is
within-card printing clustering (reprints of the SAME card may jointly satisfy multiple range
conditions at a materially higher rate than corpus-wide field marginals imply, since a card's own
printings are not independent draws — the same clustering `COMPOSE_CARD_ESTIMATE_BIAS`'s 1.78
divisor already corrects for at the SINGLE-field level), which would need a different precomputed
source than the ones checked here, not just a different combination formula.

### Round 3

Target: Round 2's own "next steps" item (b) — a flat, shape-specific multiplicative correction on
top of `calibrated_balls_into_bins`'s output, fit with a genuine calibration/held-out split, for the
same And-of-cross-index-range-leaves population. A second constant, `COMPOSE_RANGE_AND_CLUSTER_BIAS`,
routes `est_cards`'s fallback (`acquire_plan_features`, the `PrintingCompose` arm) to
`calibrated_balls_into_bins_with_bias(printing_matches, n_cards, COMPOSE_RANGE_AND_CLUSTER_BIAS)`
instead of `COMPOSE_CARD_ESTIMATE_BIAS`'s 1.78, whenever the new `is_cross_index_range_and` detects
an `And` with 2+ children whose `bare_range_bounds` indexes are pairwise distinct (same-index
children, e.g. a two-sided `usd>=a usd<=b`, still fuse to one index and don't count — that population
already gets an exact `k` from `fuse_and_range_children` upstream and never reaches this fallback).

**Self-check (constraint 3):** trivially clears. `is_cross_index_range_and` is O(children) —
`bare_range_bounds` per child is a pure match + float comparison, no index probe, bounded by query
length never match count — and only runs inside the `unwrap_or_else` closure, i.e. only after
`exact_cards` has already declined. No new per-query scan class; confirmed by `cargo test` and the
same-build latency canary below showing nothing distinguishable from noise.

**A structural surprise mid-investigation, not in the fix itself:** the first Python re-derivation of
`scan_units` (needed to sweep the bias without a Rust rebuild, same trick Round 1 used for
`COMPOSE_CANDIDATE_SPAN_BIAS`) matched the live build's own `scan_units` on only 1,138/1,500 rows.
The other 362 are `range_too_broad_to_narrow` — a LATER, separate guard in `acquire_plan_features`
(after `domain_cards`/`scan_units` are computed) that resets both to the full corpus whenever the
And's min-folded `printing_matches` alone exceeds `MAX_NARROW_FRACTION` (0.25) of `n_printings`,
**independently of any bias**. This is not a bug this round touches — it is a separate, deliberate
"narrowing degrades to a full scan" model, verified elsewhere — but it means a clustering-bias
constant here can only ever move the NARROW subset (562/742 of the held-out half): the broad subset's
`scan_units` is bias-invariant by construction. Modeling the guard in the Python re-derivation brought
the self-check to 1,500/1,500 exact matches before any sweep was trusted, and the real Rust build's
paired diff (below) matches the Python-simulated numbers exactly, confirming the model was right.

**Fit.** Captured, per sampled query, each leaf's own exact printing-match count (`k_i`, via an
isolated `unique=printing` sub-query per predicate — exact, no estimate) and the real
`printings_examined` GatheredScan counter, over 1,500 and2/and3 RANGE_FAMILIES queries (`unique=card`,
same population and precedent size as Rounds 1-2). Split by `hash(query) % 2` — 758 calibration / 742
held-out. Swept the bias 0.20–1.78 in steps of 0.02 on the calibration half only; the error-vs-bias
curve is smooth and convex on BOTH halves with minima 0.04 apart (1.14 calibration, ~1.06 held-out),
which is what a genuine signal looks like rather than noise fit to one split. Picked `1.1`, inside
both minima, rather than either half's precise argmin.

```
                          calibration (n=758)              held-out (n=742)
scan_units total abs      8.70M (1.78) -> 8.31M (1.1)       8.60M (1.78) -> 8.02M (1.1)
improved / regressed                 417 / 148                      433 / 117
narrow-only subset (n)                    576                            562
narrow-only total abs     3.15M (1.78) -> 2.76M (1.1)       3.19M (1.78) -> 2.61M (1.1)
price-triple subset (n)                   382                            363
price-triple total abs    3.87M (1.78) -> 3.74M (1.1)       3.73M (1.78) -> 3.47M (1.1)
```

Direction matches the assignment's hypothesis: smaller than 1.78 (less division of `k`, so a HIGHER
effective ball count and a higher resulting estimate), correcting Round 2's diagnosed floor-undercount
rather than repeating `COMPOSE_CARD_ESTIMATE_BIAS`'s saturating-overcount correction.

**Price-triple check.** The held-out price-triple subset (`usd`/`eur`/`tix`, 2+ of them) improves
proportionally in line with the whole population (213/68/82) — no sign of the correlation risk Round 2
flagged, because this is a flat multiplicative rescaling of `calibrated_balls_into_bins`'s existing
math, not a combination formula across per-leaf estimates; there is no per-leaf independence
assumption for near-identical fields to violate.

**Verified against the real build, not just simulation:** rebuilt the modified engine and re-ran the
same 1,500-query, same-seed sample through it directly (not the Python re-derivation) — the real
paired diff (baseline `costcell/trunk`@`ef78a984` vs modified) landed on the exact same numbers as the
simulation (758/742 split, 8.70M→8.31M and 8.60M→8.02M), confirming the Python model used to pick the
constant was not itself a source of error.

**Why the single-run agreement gate doesn't move.** `bench_cost_model_agreement.py`'s `GatheredScan`/
`card` cell stayed at 15% within [0.8, 1.25] on both builds (35,918 vs 35,946 rows) — expected, not a
sign the fix is inert: this cell pools every card-mode `PrintingCompose` acquire, and the affected
shape (And of 2+ different-index range leaves, narrow enough to escape `range_too_broad_to_narrow`) is
a small slice of it. The held-out paired-diff above is the controlled measurement; this cell is the
same noisy sanity check Round 1 already established is uninformative at this grain.

### Round 3 confirmation runs

- `bench_regret_matrix.py --seconds 120 --mode uniform`: same shape as Round 1's — regret still 96%
  `printing_compose` share, `StreamedSelect -> GatheredScan` / `GatheredScan -> PrintingCompose` still
  the largest picked/best mismatches, nothing resembling the 23.6x acquire-time precedent.
- `bench_query_latency_ab.py --mode realistic --sample 800 --seed 1`, baseline vs modified, interleaved
  A1/B1/A2: real diff `B - A = -0.3µs, 95% CI [-0.6, -0.1]`, "B is FASTER". Same-build canary (A1 vs
  A2, zero code difference): `-0.6µs, CI [-0.9, -0.4]`, also "B is FASTER" — a swing of comparable (here
  larger) magnitude with nothing changed, matching Round 1's own non-interleaved-run drift finding. Read
  as no detectable latency effect either way, not as a confirmed speedup.

### Round 4

Target: the "broad" ~24-25% of the same `is_cross_index_range_and` population Round 3 flagged out of
scope -- the subset where `range_too_broad_to_narrow` (a LATER, independent guard, found mid-
investigation by Round 3) resets `eval_domain`/`scan_units` to the full corpus regardless of any
bias, because the And's min-folded `printing_matches` alone is too broad a fraction of `n_printings`
to trust `domain_cards`. Round 3's own report called the ~69% "real usage" figure for this subset a
mid-investigation finding, not validated -- this round re-derives it from scratch on a fresh sample
before building anything on it.

**Re-derivation.** Sampled 1,500 and2/and3 RANGE_FAMILIES queries (`unique=card`, fresh seed) via
`query()` with `Shape(families=RANGE_FAMILIES, predicates=2 or 3)` -- family draws are distinct-
without-replacement and each of the 5 `RANGE_FAMILIES` maps 1:1 to its own printing-value index, so
every sampled query is `is_cross_index_range_and` by construction, same reasoning Round 3 used.
Detected the guard firing by its signature (`eval_domain == n_cards` and `scan_units == n_printings`,
which for this printing-varying population -- never card-invariant, never a bare collection leaf,
and Round 2 already proved `est.result.card` is never `Some` here -- means the guard fired with none
of its four exemptions applying): **372/1,500 (24.8%)**, matching the ~24-25% cited going in.

Read the real GatheredScan counters via `explain_analyze` (`num_warmups=0, num_trials=1`; counters
are round-invariant -- checked directly by rerunning 20 queries at `(0, 1)` against `(2, 5)` with
identical `cards_visited`/`printings_examined` both times). Result, on the 372 guard-fired rows:

    real cards_visited      / n_cards       mean 1.000   median 1.000   (0 rows below 1.0)
    real printings_examined / n_printings   mean 0.697   median 0.713

`eval_domain` (`n_cards`) is EXACT for every one of the 372 rows, not just close -- real card-space
narrowing gives up at the same `range_too_broad_to_narrow` threshold this guard checks (the function
is shared with the real narrowing path, not just this pricing site), so a GatheredScan the router
actually runs after this fires really does visit every card. `scan_units` is the opposite: the guard's
`n_printings` ceiling is real (never measured over 1.0) but loose, at a stable ~0.70 of it -- this
re-derives the ~69% figure cleanly, and settles the "did the guard also give up on the printing side"
question the eval_domain number could not answer.

**Fix.** Left `eval_domain` untouched (scaling an already-exact number down would reintroduce the
under-charge this guard exists to prevent -- the exact failure mode the four existing exemptions were
each added to fix, so this round does not risk it even via a downstream scale). Added
`COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` (0.7), applied to `scan_units` alone, gated on
`is_cross_index_range_and(composed, indexes)` -- reused unchanged from Round 3, not reimplemented.
This is a scale on the ALREADY-DECIDED reset, not a 5th exemption: the guard's unconditional reset
still fires exactly as before; only what `scan_units` (never `eval_domain`) resets TO changes, and
only for this one shape.

**Fit.** Split the 372 guard-fired rows by `hash(query) % 2`: 191 calibration / 181 held-out. Swept
0.60-0.84 in steps of 0.01 on the calibration half only; both halves' error-vs-scale curves are
smooth, convex, and minimize at the SAME 0.71 (closer agreement than Round 3's two minima 0.04 apart).
Picked 0.7, inside the flat bottom of both and matching the sample's own mean/median realized fraction
almost exactly.

```
                          calibration (n=191)              held-out (n=181)
scan_units total abs      5.64M (1.0) -> 1.92M (0.7)        5.40M (1.0) -> 1.90M (0.7)
improved / regressed                172 / 19                        166 / 15
price-triple subset (n)                  79                              71
price-triple total abs   1.91M (1.0) -> 0.79M (0.7)        1.82M (1.0) -> 0.75M (0.7)
```

**Price-triple check.** The held-out price-triple subset (`usd`/`eur`/`tix`, 2+ of them) improves
proportionally in line with the whole population (62 improved / 9 regressed) -- same reasoning as
Round 3's own price-triple check: a flat scale on an already-computed ceiling has no per-leaf
independence assumption for the near-identical price columns to violate.

**Verified against the real build, not just simulation.** Rebuilt the modified engine and re-ran the
identical 1,500-query, same-seed sample through it directly -- `eval_domain` matched `n_cards` on all
372 guard-fired rows (0 mismatches) and `scan_units` matched `round(0.7 * n_printings)` exactly (0
mismatches), and the real paired diff landed on the exact same numbers as the Python-side
re-derivation (5.64M/5.40M -> 1.92M/1.90M, 172/19 and 166/15).

**Why the single-run agreement gate doesn't move.** `bench_cost_model_agreement.py`'s `GatheredScan`/
`card` cell stayed at 16% within [0.8, 1.25] on both builds (33,966 vs 33,806 rows) -- expected: this
cell pools every card-mode `PrintingCompose` acquire, and the guard-fired subset of
`is_cross_index_range_and` is a small slice of it, same reasoning as Round 3.

### Round 4 confirmation runs

- `cargo test --manifest-path card_engine/Cargo.toml`: 167 passed, 0 failed, 56 ignored.
- `cargo clippy --manifest-path card_engine/Cargo.toml --all-targets -- -D warnings`: clean.
- `bench_regret_matrix.py --seconds 120 --mode uniform`: same shape as Rounds 1 and 3 -- regret still
  96% `printing_compose` share, `StreamedSelect -> GatheredScan` / `GatheredScan -> PrintingCompose`
  still the largest picked/best mismatches, nothing resembling the 23.6x acquire-time precedent.
- `bench_query_latency_ab.py --mode realistic --sample 800 --seed 1`, baseline vs modified, interleaved
  A1/B1/A2: real diff `B - A = +0.4µs, 95% CI [+0.3, +0.6]`, "B is SLOWER". Same-build canary (A1 vs
  A2, zero code difference): `-0.4µs, CI [-0.5, -0.2]`, "B is FASTER" -- a swing of comparable
  magnitude with nothing changed, matching Rounds 1 and 3's own non-interleaved-run drift finding. Read
  as no detectable latency effect either way, not as a confirmed regression.

### Round 5

Diagnostic only, no code changes -- re-run the magnitude-weighted AST-shape breakdown from scratch
against the current `costcell/trunk` tip (`8ab0b4cc`), since a full-corpus checkpoint
(`bench_cost_model_agreement.py`) still shows `GatheredScan`/`card` at 15% within 25%, essentially
unchanged from Round 0's 16%, despite three landed, held-out-validated fixes (Rounds 1, 3, 4).

**Method.** Isolated release wheel (`maturin build --release`, extracted, `PYTHONPATH`-pinned).
Sampled with `QuerySampler(corpus, "uniform")`, reimplementing `query()`'s body inline (predicate
count → `_draw_families` → `predicate` per family) so each row keeps which FAMILIES were drawn --
`query()` itself doesn't return them, and every other part of the sampling loop (limits, offsets,
warmups/trials, `unique`/`orderby`/`direction` drawn independently) matches
`bench_cost_model_agreement.py` exactly. Every family maps to one of six categories: `range`
(`usd`/`eur`/`tix`/`cn`/`released` -- the printing-varying, range-indexed fields this whole doc is
about), `numeric_other` (`pow`/`tou`/`cmc`/`loyalty`), `rarity`, `text` (`name`/`oracle`/`flavor`/
`artist`), `arith` (the extended syntax), and `collection` (everything else -- type/legality/
identity/color/set/keyword/produces/tag/border/frame/watermark/devotion). `sampler.query()` only
ever emits a flat conjunction (no Or/Not/regex), so the whole GatheredScan/card population this cell
measures is single leaves and `and2`/`and3` -- there is no Or-composed or Not-wrapped subpopulation
to bucket here; that's a property of what `bench_cost_model_agreement.py` samples, not something
this round chose.

Per row: `predicted = acquire["scan_units"]`, `measured = plan["printings_examined"]` (`GatheredScan`
only, non-declined) -- the same pairing Rounds 3/4 used, per `scan_units`'s own doc comment at
`lib.rs:11213` ("the real `printings_examined` GatheredScan counter"). Bucket key = `structure`
(`single`/`and2`/`and3`) + sorted category tuple. Ranked by total absolute `scan_units` error per
bucket (magnitude-weighted), with each bucket's row count and median ratio reported alongside so a
high-count-but-tied bucket and a rare-but-catastrophic one are both visible.

300s budget (same protocol/seconds as Round 0's baseline run) → **30,892 GatheredScan/card rows**,
same order of magnitude as Round 0's 35,074 and Rounds 1-4's 1,500-query calibration samples for
their narrower held-out slices. Total pooled absolute `scan_units` error: 175,122,864. (Note: this
is a `scan_units`-space ratio, same quantity Rounds 1-4 worked in, not the ns-space
measured/predicted ratio `bench_cost_model_agreement.py`'s headline 15%/16% number reports --
the two measure different things and are not expected to match numerically.)

**Ranked bucket table** (all buckets with n ≥ 1; buckets below 0.1% share are real but tiny):

```
bucket                                 n       sum |err|   share  median ratio  within25%
single:range                        3041      93,991,483   53.7%          0.64        1%
and2:collection+range                2301      17,699,351   10.1%          1.21       15%
and2:numeric_other+range              801      14,662,649    8.4%          0.74       25%
single:collection                    6993      11,248,207    6.4%          1.00       68%
single:rarity                         614       8,168,926    4.7%          1.08       41%
and2:range+rarity                     199       5,631,260    3.2%          1.50        5%
and2:numeric_other+rarity             169       3,714,009    2.1%          0.41       21%
and2:collection+numeric_other        1628       2,553,659    1.5%          1.00       42%
and2:range+range                      398       2,442,018    1.4%          1.15       35%
single:numeric_other                 2355       2,405,375    1.4%          1.00       95%
and2:collection+rarity                466       2,234,802    1.3%          0.54       14%
and2:arith+range                      215       1,708,742    1.0%          0.58       15%
and2:collection+collection           2442       1,497,390    0.9%          0.20       20%
and3:collection+numeric_other+range   355       1,303,555    0.7%          0.44       12%
and3:collection+range+range           232         855,513    0.5%          0.80       21%
and3:numeric_other+range+rarity        30         725,010    0.4%          0.31       17%
and3:collection+collection+range      522         526,766    0.3%          0.18       10%
and2:range+text                       814         449,305    0.3%          0.86       20%
and3:numeric_other+range+range         62         434,311    0.2%          0.44       15%
and3:numeric_other+numeric_other+range 39         398,600    0.2%          0.27       10%
and3:collection+range+rarity           99         261,534    0.1%          0.77       12%
and2:arith+rarity                      35         247,425    0.1%          0.43       29%
single:text                          2454         239,195    0.1%          1.00       46%
and2:numeric_other+numeric_other      144         168,614    0.1%          1.00       82%
and3:range+range+range                 24         154,252    0.1%          1.02       17%
[remaining 38 buckets each < 0.1% share, ~0.4% combined, mostly text/arith-involving rows with n<100]
```

**Confirmation: Rounds 1-4's target shape did drop, as expected.** The `is_cross_index_range_and`-
equivalent population (an `and2`/`and3` with 2+ `range`-category families -- exactly what
`is_cross_index_range_and` requires) is **807 rows (2.6% of the sample), 4,069,972 abs error (2.3%
of the pooled total), median ratio 1.00, 28% within 25%**. Before Rounds 3/4 this shape was
"tens of millions of units each" and the single dominant contributor by every account in this doc;
now it sits at a median ratio of exactly 1.00 (as good as any bucket in the table) and would not
make a top-10 list by magnitude. The three shipped fixes worked exactly as designed on their target
population -- they just never had a chance to move the pooled cell, because that population turns
out to be a small slice of it (2.6% by row count, 2.3% by error), not the ~37%+ this doc's earlier
rounds estimated from the narrower and2/and3-RANGE_FAMILIES-only calibration sample. That estimate
was never wrong on its own terms (it was scoped to the RANGE_FAMILIES-only shape from the start);
it just wasn't representative of the whole `GatheredScan`/card population once measured against it
directly.

**What actually dominates: the SAME broad-guard reset, everywhere Round 4 didn't scale it.** Flagging
every row where `predicted == n_printings` exactly (the `range_too_broad_to_narrow` guard's
telltale signature -- both `PrintingCompose`'s and the sibling `CardRangePopcount`/
`PrintingRangeScan` arms' resets set `scan_units` to the literal, unscaled `n_printings` when they
fire) finds **2,412 rows (7.8% of the sample) carrying 131,170,530 abs error -- 74.9% of the ENTIRE
pooled total -- at median ratio 0.45** (predicted ~2.2x too high). Zero of these 2,412 rows are
`is_cross_index_range_and` -- Round 4's scale never had a chance to touch any of them, by
construction. Split by structure: `single` (n=1,851, 91.6M), `and2` (n=534, 37.3M), `and3` (n=27,
2.2M).

Reading `lib.rs` confirms this is not a new bug -- it is Round 3/4's own noted, deliberate scope
limit, finally showing up as the dominant term now that the target shape it excluded is fixed. Two
separate sites:

1. **`PrintingCompose`'s own broad-guard reset** (`lib.rs:12239-12259`) scales `scan_units` by
   `COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` *only* `if is_cross_index_range_and(composed, indexes)`; the
   inline comment at `:12248-12250` says outright: "every other query reaching this branch (a single
   broad range, a broadcast legality, ...) never had this scale's calibration sample in it, so it
   keeps today's unscaled `n_printings` ceiling." That "everything else" population is exactly what
   this round measured. Per-bucket broad/narrow split confirms the broad slice is a small-count,
   huge-magnitude minority within each mixed bucket:
   ```
   and2:collection+range      broad n=210  (9% of bucket rows, 78% of bucket's error) median ratio 0.30
                               narrow n=2091 (91% of rows, 22% of error)               median ratio 1.39
   and2:numeric_other+range   broad n=188  (23% of rows, 94% of error)                 median ratio 0.21
                               narrow n=613  (77% of rows, 6% of error)                 median ratio 0.93
   and2:range+rarity          broad n=81   (41% of rows, 91% of error)                 median ratio 0.36
                               narrow n=118  (59% of rows, 9% of error)                 median ratio 3.37
   ```
   Example rows (all `predicted == n_printings == 97,812`, the full corpus):
   `tou<=5 tix>=0.02 tix<=0.04` → measured 26,834 (ratio 0.27); `tou>=2 tou<=4 year<2025` → measured
   12,491 (ratio 0.13); `r>=uncommon tix>0.02` → measured 46,256 (ratio 0.47); `r>=uncommon eur<0.49`
   → measured 49,726 (ratio 0.51).

2. **The sibling `CardRangePopcount` (`lib.rs:11801-11845`) and `PrintingRangeScan`
   (`lib.rs:11846-11872`) acquire arms** -- which serve a single BARE range leaf under `unique=card`
   (e.g. `usd>=0.24` alone, no `And` at all) -- have their own structurally identical
   `range_too_broad_to_narrow`-gated reset to `(n_cards, n_printings)`. This is a completely separate
   code path from `PrintingCompose` (confirmed live: `usd>=0.24` alone acquires via
   `count_source: card_range_popcount`, not `printing_compose`), never in scope for any of Rounds
   1-4 (whose investigation was explicitly `compose_printing_estimate`/`PrintingCompose`). This is
   the `single:range` bucket -- the single largest bucket in the whole table, 53.7% of pooled error
   on its own, median ratio 0.64. Example rows (all `predicted == 97,812`): `usd>=0.24` → measured
   40,782 (ratio 0.42); `cn>=127` → measured 45,904 (ratio 0.47); `tix<0.12` → measured 53,260
   (ratio 0.54); `year>=2023` → measured 61,411 (ratio 0.63).

**Secondary, smaller finding, opposite direction.** `and2:range+rarity`'s NARROW (non-broad) subset
reads median ratio 3.37 -- badly UNDER-costed, the opposite direction from everything else in this
round. Small in absolute terms (118 rows, ~9% of that bucket's 5.6M error, so well under 1M total)
-- not worth its own round yet, but worth a one-line flag for whoever next touches range+rarity
combinations, since it's a direction-flip rather than more of the same over-cost pattern.

**What Round 6 should target.** The `range_too_broad_to_narrow` broad-guard reset, generalized
beyond `is_cross_index_range_and`, at two sites:

- Extend (or add a sibling to) `COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` inside `PrintingCompose`'s own
  reset so it also scales `scan_units` when the guard fires but `is_cross_index_range_and` is false
  (a lone broad range leaf mixed with a collection/numeric_other/rarity leaf, or a bare broadcast
  legality/range predicate). This is exactly the pre-computation-safe pattern Round 4 already used --
  a flat multiplicative scale on an already-computed ceiling, no new per-query scan -- just widened
  in scope. Needs its OWN calibration/held-out split before trusting a number: this round's median
  ratio here (0.45) reads meaningfully lower than Round 4's fitted realized fraction (~0.70-0.71) for
  the `is_cross_index_range_and` population, so reusing 0.7 unchanged is not obviously right --
  and the two mixed-leaf buckets above disagree with each other too (0.21-0.36 median), so a single
  universal constant may not fit either; check whether the guard's realized fraction varies
  systematically with the NON-range leaf's own selectivity before picking one or several constants.
- Add the analogous scale to the `CardRangePopcount`/`PrintingRangeScan` arms' own broad-guard reset
  (`lib.rs:11831`, `:11862`) for a single bare range leaf -- a different acquire branch than
  `PrintingCompose`, so it needs its own gate check and likely its own constant (median ratio here,
  0.64, differs again from both of the above), even though the underlying guard function
  (`range_too_broad_to_narrow`) is the same shared code. This is `single:range`, the single largest
  bucket by magnitude in the whole table -- the highest-leverage place to start.
- Population parity note for whoever fits this: unlike Rounds 3/4's RANGE_FAMILIES-only calibration
  sample, this population spans every family category (collection/numeric_other/rarity/text mixed
  with a range leaf, plus bare single range leaves with no other predicate at all) -- a proper
  calibration/held-out split here should draw from the SAME uniform-mode, all-category sampling this
  round used, not a re-use of the narrower RANGE_FAMILIES-only sample Rounds 1-4 built their splits
  from, since that sample structurally cannot contain the `single:range` or mixed-category rows that
  now turn out to matter most.

### Round 6

Target: Round 5's own top recommendation -- the `CardRangePopcount` arm's own
`range_too_broad_to_narrow` broad-guard reset (`lib.rs:11831` as of Round 5's tip), the single
largest bucket in the whole pooled `GatheredScan`/`card` error table (`single:range`, 53.7% of pooled
error, n=3,041 in Round 5's sample, median ratio 0.64). A bare single range leaf under `unique=card`
(e.g. `usd>=0.24` alone) -- confirmed live via `count_source: card_range_popcount`, a completely
separate acquire branch from `PrintingCompose`'s `is_cross_index_range_and` guard Rounds 3/4 fixed.

**Population re-derivation.** Sampled with `Shape(families=RANGE_FAMILIES, predicates=1,
unique={"card"})` (same shape `bench_card_range_estimate.py` already uses for this exact acquire
branch), filtered to `count_source == "card_range_popcount"`, varying `limit`/`offset`/`orderby`/
`direction` per query (matching `bench_cost_model_agreement.py`'s own protocol rather than pinning
them, so the population is not an artifact of one page shape). Of all `card_range_popcount` rows,
**52-54% have the guard fire** (two independent 3,500-row samples: 54.1% and 52.1%) -- the "broad"
population this round targets. Real `GatheredScan` counters (GatheredScan is always tried as a
forced trial in `explain_analyze` regardless of which plan the router actually picks, same trick
Rounds 3/4 used) over 3,500 guard-fired rows:

```
eval_domain realized fraction (cards_visited / n_cards):     mean 0.975  median 1.000  min 0.233
scan_units realized fraction (printings_examined / n_printings): mean 0.447  median 0.434  min 0.159  max 0.798
```

Per-field `scan_units` median: `cn` 0.41, `usd` 0.43, `eur` 0.43, `released` 0.42, `tix` 0.48 --
stable within a ~20% relative band across all five `RANGE_FAMILIES`, not one field dominating or
diverging.

**Self-check (pre-computation constraint).** The only change is a multiply-and-round on two numbers
already computed before this branch's `unwrap_or_else`-equivalent `if`/`else` runs (`n_printings` is
a corpus-wide constant read from `ctx`, `k`/`idx.len()` already drive the `range_too_broad_to_narrow`
call the branch makes regardless). No new per-query scan, no new index probe -- confirmed by
`cargo test` and the latency A/B below showing nothing distinguishable from noise once run-order
confounds are controlled for (see below).

**A structural surprise, not in the target arm itself: the sibling `else` branch is also
miscalibrated, for a different reason.** The arm's own comment claims "the sibling `PrintingRangeScan`
branch below assumes the opposite (always unnarrowed) and its cells agree to within 1% -- this makes
both exact," which reads as a claim that the NARROW-subset `(card_est, card_est)` branch is exact.
Measured directly, on the guard-NOT-fired rows from the same sample: `card_est / cards_visited`
(eval_domain check) is indeed exact at the median (1.00), but `card_est / printings_examined` (scan_units
check) reads median 0.25-0.37 depending on field -- `card_est` (a DISTINCT-CARD estimate) badly
undershoots `printings_examined` (a printing count) whenever a card has multiple reprints inside the
narrowed range, which is common for `cn`/`released`/`usd`. This is the assignment's own "verify what
that comment refers to" check: it refers to the two branches' feature vectors being internally
CONSISTENT with each other (not to either being numerically accurate), and the `else` branch's
`scan_units` side is a real, separate miscalibration -- **not fixed this round** (out of the assigned
blast radius; scoped as a follow-up in the ledger table above, not silently folded into this
constant).

**Why `eval_domain` is left untouched despite not being perfectly exact here (unlike Round 4's
population).** 96.6% of guard-fired rows read exactly 1.0; the remaining 3.4% are concentrated
entirely in `usd`/`eur`/`tix` queries at extreme thresholds (`eur>=1.05`, `tix>0.04`, ...), where most
cards have no printing with that currency at all, so the real materializing scan still narrows out
the null-complement even though the guard correctly judged the VALUE range too broad to narrow on.
Scaling the dominant 96.6%-exact regime down to chase a rare, structurally different tail would
reintroduce the under-charge the guard exists to prevent -- same call Round 4 made, but this time
independently verified rather than assumed to transfer, per the assignment's instruction.

**Fit.** Split 3,500 guard-fired rows by `hash(query) % 2`: 1,765 calibration / 1,735 held-out. Swept
0.30-0.60 in steps of 0.01 on the calibration half only; both halves' error-vs-scale curves are
smooth and convex, minimizing one step apart (0.43 calibration, 0.44 held-out).

```
                          calibration (n=1,765)            held-out (n=1,735)
scan_units total abs      96.0M (1.0) -> 15.4M (0.43)       93.3M (1.0) -> 16.0M (0.43)
improved / regressed              1,742 / 23                        1,704 / 31
```

Picked 0.43, inside the flat bottom of both curves.

**Per-field constant considered and rejected as not worth it.** A per-field scale (each field's own
median as an oracle upper bound) reaches 30.3M total abs error against the flat scale's 31.4M -- only
~3.5% further reduction, except for `tix` (275 rows, smallest subgroup) where the per-field oracle
does meaningfully better (0.80M vs 1.71M). Given the modest aggregate gain and this round's mandate to
prefer a flat constant unless the fit clearly does not hold, one flat `COMPOSE_BARE_RANGE_BROAD_SCALE`
was kept; a future round revisiting `tix` specifically could reconsider.

**Price-triple sanity (per-field, not cross-field correlation -- that check does not apply to a bare
single leaf).** `usd` (0.43), `eur` (0.43), `tix` (0.48) all sit close to the chosen 0.43; no
individual price field is a pricing outlier.

**Verified against the real build, not just the Python-side sweep.** Rebuilt with the constant and
re-ran the identical 3,500-query sample directly against it: `scan_units` matched
`round(0.43 * n_printings)` exactly on all 3,500 rows (0 mismatches), and the real paired diff landed
on the exact same total (31,400,955 combined) as the simulation.

**Routing-decision check (why this round is different from Rounds 3/4's structural risk).** Lowering
a feature this branch's shared `PlanFeatures` also prices COMPETING plans (`GatheredScan`/
`StreamedSelect`) against could in principle flip the router away from `CardRangePopcount` toward a
now-artificially-cheap competitor. Checked directly: the router picked `CardRangePopcount` on
500/500 sampled bare-range queries under BOTH the baseline and modified build (same kw), and
`bench_regret_matrix.py`'s `acquire` table shows `card_range_popcount` at 0.00 mean / 0% miss in both
builds (n=659 baseline, n=658 modified) -- no misrouting introduced.

**Why the single-run agreement gate barely moves, and why that's not evidence against the fix.**
`bench_cost_model_agreement.py`'s `GatheredScan`/`card` cell read 17% within [0.8, 1.25] on both
builds (n=33,019 baseline, n=33,251 modified) -- same story as Rounds 3/4: `card_range_popcount` is
only ~1,563-1,572 of that ~33,000-row pooled cell (~4.7%), so even fixing its single largest error
bucket cannot move a pooled median by much. The finer `GatheredScan`/`card_range_popcount` sub-row
(grouped by acquire branch, not pooled across all of `unique=card`) DID move: median 0.94 -> 1.05,
within-25% 47% -> 52% (n=1,563 / 1,572, single uncontrolled runs -- read as corroborating, not proof,
same noise caveat as every other single-run number in this doc).

### Round 6 confirmation runs

- `cargo test --manifest-path card_engine/Cargo.toml`: 167 passed, 0 failed, 56 ignored.
- `cargo clippy --manifest-path card_engine/Cargo.toml --all-targets -- -D warnings`: clean.
- `bench_regret_matrix.py --seconds 120 --mode uniform`: same shape as every prior round -- regret
  still 96% `printing_compose` share, `StreamedSelect -> GatheredScan` / `GatheredScan ->
  PrintingCompose` still the largest picked/best mismatches, `card_range_popcount`'s own regret
  unchanged (0.00 mean / 0% miss, both builds) -- nothing resembling the 23.6x acquire-time precedent.
- `bench_query_latency_ab.py --mode realistic --sample 800 --seed 1`: the FIRST paired run (baseline
  measured first, modified second) read `+4.1µs, 95% CI [+3.6, +4.5]`, "B is SLOWER" -- a magnitude
  that, unlike every prior round's canary-comparable noise, looked like a real signal at first glance.
  Investigated directly rather than accepted: (1) the specific queries showing the largest slowdowns
  were `t:legendary`, `c:g`, `set:usg` and similar -- filters that never reach `CardRangePopcount` at
  all, ruling out a routing-side effect from this change; (2) re-running with the build ORDER swapped
  (modified first, baseline second) produced `-0.1µs`, "NO DETECTABLE DIFFERENCE"; (3) two further
  same-build canaries (baseline-vs-baseline, modified-vs-modified, each a fresh pair) read `+0.8µs`
  ("B is SLOWER") and `-0.4µs` ("B is FASTER") respectively -- swings of comparable or larger magnitude
  than two of the three real A-vs-B diffs measured, with nothing changed. Read as run-order-dependent
  machine drift (exactly the failure mode the harness's own module docstring warns about), not a
  real latency effect in either direction -- consistent with the routing-decision check above finding
  zero picked-plan changes on the target population.
- `cargo build`/wheel blast radius: `git diff --stat costcell/trunk` shows only `card_engine/src/lib.rs`
  touched (58 lines: one new constant + its doc, five lines in the `CardRangePopcount` arm).

### Round 7

Target: resolve the population-size discrepancy the assignment opened with -- Round 5's "single:range"
bucket (a single family/predicate drawn from `RANGE_FAMILIES`, `unique=card`) was 3,041 rows, 53.7% of
pooled error, but Round 6's fix only touches the `CardRangePopcount` arm, which Round 6 itself measured
at ~1,660 rows (~4.7% of the pooled cell) -- smaller than the bucket. Where does the rest go?

**The `Prep::Candidates` hypothesis was checked first and refuted.** Reading `card_range_popcount_
applicable` (lib.rs:9485) confirms it requires `plane.is_none()`, a bare range (`bare_range_bounds`),
AND `indexes.sort_perms.order(sort_col, descending, cards.len()).is_some()` -- both sort-permutation
directions for the query's exact orderby/direction/card-count combination. But the NEXT branch acquire
tries when that fails is not `Prep::Candidates` -- it is `PrintingCompose`, which is mode-agnostic and
requires no sort permutation at all (`printing_compose_applicable`, lib.rs:9437, and `is_printing_
composable`'s range arm, lib.rs:6866, both gate only on `bare_range_bounds(...).is_some()`). A direct
sample confirms this empirically: of 1,184,753 bare single-range `unique=card` queries generated
(varying orderby/direction/limit/offset the way `bench_cost_model_agreement.py` does), 56.3% acquired
via `card_range_popcount` and the remaining 43.7% via `printing_compose` -- **zero** via `candidates`.

**A second mechanism, found while building that sample, matters just as much: two-sided bounds.**
`Shape(families=RANGE_FAMILIES, predicates=1, unique={"card"})` -- Round 6's own generator for this
population -- can render its one drawn predicate as a fused two-sided bound (e.g. `eur>=0.23
eur<=0.45`), because `QuerySampler`'s `bounded` parameter defaults to `None` (either shape, drawn at
random) rather than `False` (one-sided only). `bare_range_bounds`, `CardRangePopcount`'s own gate,
matches a single comparison and never an `And` (confirmed directly in `fuse_and_range_children`'s own
doc: "a FUSED two-sided range never arrives here at all"), so a two-sided bound reaches
`PrintingCompose` regardless of sort permutation. Round 5's AST-shape bucketer keyed "single" on the
SAMPLER's predicate count (one family drawn), not on `FilterExpr` structure -- so "single:range"
always included these two-sided `And`-shaped rows, they were just never told apart from true bare
leaves until this round asked.

**Conclusion: the missing population is real, independently broken, and reaches `PrintingCompose`'s
OWN broad-guard reset -- exactly Round 5's "what Round 6 should target" recommendation item 1, which
Round 6 explicitly deferred** ("Extend ... `PrintingCompose`'s own reset so it also scales `scan_units`
when the guard fires but `is_cross_index_range_and` is false ... a bare broadcast legality/range
predicate"). Sampled 23,039 `printing_compose`-acquired rows from the same shape (240s budget, fresh
seed): 13,053 (56.6%) have the guard fire (`scan_units == n_printings`), split 5,300 true bare-single /
7,753 fused two-sided. Measured against the real `printings_examined` GatheredScan counter:

```
                                  eval_domain/cards_visited   scan_units/printings_examined   printings_examined/n_printings
broad (guard fired, n=13,053)             mean 1.000                  mean 2.023 (median 1.917)        mean 0.518 (median 0.522)
narrow (guard not fired, n=9,986)         mean 0.905 (median 0.963)   mean 0.381 (median 0.382)        mean 0.122 (median 0.131)
```

`eval_domain` is exact on the broad subset (matches every prior broad-guard round). `scan_units` is
badly over-costed there (predicted ~1.9-2.0x too high), confirming Round 5's bucket-level median ratio
of 0.64 for "single:range" was a blend of this over-costed `printing_compose` slice and Round 6's
now-fixed `card_range_popcount` slice, not evidence Round 6 left its own target undone. The narrow
subset is badly UNDER-costed (median 0.38) -- a second, separate bug in `PrintingCompose`'s non-broad
branch for this same shape, structurally the same phenomenon Round 6 flagged in `CardRangePopcount`'s
sibling `else` branch (a card-count-shaped estimate undershooting a printing count) -- **not fixed this
round**, out of the assigned scope, noted below for a future round.

**Self-check (pre-computation constraint).** The new gate, `is_same_index_range_only` (lib.rs, next to
`is_cross_index_range_and`), is O(children): it calls `bare_range_bounds` per child (a pure match plus
float comparison, no index probe) and compares index pointers, the identical technique and complexity
class `is_cross_index_range_and` already uses, only run inside the same `unwrap_or_else`-adjacent
branch after `exact_cards` has already declined. No new per-query scan, no new index probe -- confirmed
by `cargo test`/`cargo clippy` and the latency A/B below.

**Fit.** Split the 13,053 broad rows by `hash(query|orderby|direction) % 2`: 6,598 calibration / 6,455
held-out. Swept 0.20-0.80 in steps of 0.02 on the calibration half only; both halves' error-vs-scale
curves are smooth and convex, minimizing at the SAME 0.52 (each sub-shape's own argmin -- 0.48 bare-
single, 0.55 fused two-sided -- brackets it tightly, so one flat constant was kept rather than two).

```
                          calibration (n=6,598)            held-out (n=6,455)
scan_units total abs     310.8M (1.0) -> 60.2M (0.52)      304.8M (1.0) -> 57.6M (0.52)
improved / regressed              6,560 / 38                       6,422 / 33
```

**Price-triple sanity (per-field, not cross-field correlation -- a flat scale on an already-computed
ceiling has no per-leaf independence assumption to violate, same reasoning as every prior broad-guard
constant).** `usd` (0.534), `eur` (0.546), `tix` (0.574) all sit in the same band as `cn`/`date`/`year`
(0.46-0.49); `tix` reads highest but not an outlier.

**Verified against the real build, not just the Python-side sweep.** Rebuilt with the constant and
replayed the identical 13,053 rows directly against it: `scan_units` matched `round(0.52 *
n_printings)` exactly on all 13,053 rows (0 mismatches, i.e. `is_same_index_range_only` correctly
recognized every one of them), and the real paired total (117,812,900) landed on the exact same number
as the calibration+held-out simulation combined (60,175,506 + 57,637,394).

### Round 7 confirmation runs

- `cargo test --manifest-path card_engine/Cargo.toml`: 167 passed, 0 failed, 56 ignored.
- `cargo clippy --manifest-path card_engine/Cargo.toml --all-targets -- -D warnings`: clean.
- `bench_regret_matrix.py --seconds 120 --mode uniform`: same shape as every prior round -- `printing_
  compose` still 95% share both builds, `StreamedSelect -> GatheredScan` / `GatheredScan ->
  PrintingCompose` still the largest picked/best mismatches, total regret comparable (49.9ms baseline
  vs 50.7ms modified) -- nothing resembling the 23.6x acquire-time precedent.
- `bench_query_latency_ab.py --mode realistic --sample 800 --seed 1`: real diff (baseline vs modified)
  `+0.7µs, 95% CI [+0.5, +0.9]`, "B is SLOWER". A same-build canary (baseline vs a second baseline run,
  identical protocol, nothing changed) read `+0.5µs, CI [+0.3, +0.7]`, also "B is SLOWER" -- same sign
  and comparable magnitude with zero code difference, matching every prior round's non-interleaved-run
  drift finding. Read as no detectable latency effect, not a confirmed regression.
- `git diff --stat costcell/trunk` shows only `card_engine/src/lib.rs` touched (101 lines: two new
  constants + their docs, one new helper function, four lines wiring it into the broad-guard branch).
- Full-table checkpoint (`bench_cost_model_agreement.py --seconds 300 --seed 0`): `GatheredScan`/`card`
  17% -> 18% within [0.8, 1.25], both within noise of each other (n=33,218 baseline, n=33,121
  modified) -- expected, same reasoning as every prior round: this cell pools every card-mode
  `PrintingCompose`/`CardRangePopcount`/`candidates`/`plane` acquire, and this round's target (a single
  range family reaching `printing_compose`'s broad guard) is a small slice of it. The pooled
  `GatheredScan`/`printing_compose` row (every `unique` mode, n=54,827/54,658) also held steady at 24%
  both builds -- same story, an even larger and more diverse pool this fix touches only a slice of.

**Next steps for a future round.** The narrow-subset (`range_too_broad_to_narrow` NOT fired)
`printing_compose` bare-range population found mid-investigation above (median `scan_units` ratio 0.38,
n=9,986 in this round's sample) is real, separately broken, and out of this round's assigned scope --
structurally the same "card-count-shaped estimate undershooting a printing count" bug Round 6 flagged
in `CardRangePopcount`'s sibling `else` branch, now confirmed to have a `PrintingCompose`-side
counterpart too.

### Round 8

Diagnostic only, no code changes. Rounds 1-7 exhausted the printing-range-index family
(`compose_printing_estimate`/`CardRangePopcount`/`PrintingCompose`, all reached via `Prep::Range`) and
the pooled `GatheredScan`/`card` cell still reads 13-16% within [0.8, 1.25], essentially unchanged from
Round 0's baseline. This round asks where the rest of the error lives, and finds it in a completely
different acquire branch: `Prep::Candidates` (`count_source == "candidates"`), reached whenever
`prepare_candidates`/`narrow_rec` cannot resolve the query to a bare range or a fully plane-compilable
expression -- text search (`name`/`o`/`ft`/`a`), the extended arithmetic syntax (`power+toughness<6`,
`cmc>=power`), `is:`-rewrite predicates, `loyalty`, and any `Or`/negated/nested-paren structure, none of
which `is_printing_composable`/`is_broadcast_leaf_shape` accept.

**Checkpoint** (`bench_cost_model_agreement.py --seconds 180 --seed 0`, isolated release wheel, same
protocol as every prior round): `GatheredScan`/`candidates` reads `n=22,190 median 0.60 p10 0.25 p90
0.92 13% within 25% FAIL` -- the single largest acquire-branch row in the whole per-plan table by row
count, well below the `[0.8, 1.25]` bar, and **over-costed** (`median < 1`), the opposite direction
from every range-leaf fix Rounds 1-7 shipped.

**Method.** Two throwaway samplers (not checked in), both pinning `unique=card` and varying
`orderby`/`direction`/`limit`/`offset` the way `bench_cost_model_agreement.py` does, against an
isolated release wheel:

- **Flat-conjunction sample** (`QuerySampler.query()`'s own body, reimplemented inline per Round 5's
  trick so each row keeps which families were drawn): 300s, uniform mode, seed 0 -- 125,680 queries
  sampled, **45,451 kept** after filtering to `count_source == "candidates"` and a non-declined
  `GatheredScan` trial. This is the same population `bench_cost_model_agreement.py` itself samples
  (same generator), just larger and carrying per-row family/shape metadata the harness doesn't keep.
- **Structured-connective sample** (`QuerySampler.structured_query()`, which draws `Or`/negated/
  parenthesized/regex shapes `query()` can never produce): 240s, uniform mode, seed 1 -- 47,058
  sampled, **29,192 kept**. `bench_cost_model_agreement.py` cannot see this population at all --
  `sampler.query()` only ever emits a flat conjunction -- so it is invisible to the checkpoint number
  above regardless of how large its error turns out to be.

Per row: `predicted_ns` = `costbench.predicted_ns` (the `GatheredScan` trial's `predicted_ns`),
`measured_ns` = `costbench.plan_self_ns` (the same netting rule the checkpoint gate uses -- `candidates`
is in neither `RANGE_ACQUIRES` nor exempt, so `plan_self_ns` is the executor alone, no `ns_prepare`
added back). Feature-level: `explain`'s own `acquire.scan_units`/`acquire.eval_domain` against the
`GatheredScan` trial's real `printings_examined`/`cards_visited` counters -- the same pairing Rounds
3-7 used for the range family, applied here to `Prep::Candidates` for the first time.

**Which feature is actually mismatched -- checked, not assumed.** Over the flat-conjunction sample:

```
eval_domain / cards_visited     n=30,794   median 1.00   p10 1.00   p90 1.00   (essentially exact)
scan_units  / printings_examined n=30,586   median 1.00   p10 0.52   p90 3.00   (noisier, but not
                                                                                  systematically over)
overall measured_ns / predicted_ns  n=45,451  median 0.49  p10 0.24  p90 0.86   within25% 8%
```

`eval_domain` is exact everywhere sampled. `scan_units` is close to exact at the pooled median and, in
several of the highest-magnitude buckets below, **under**-predicts (real `printings_examined` bigger
than the estimate) -- the opposite direction from the pooled ns-space over-cost. Neither size feature
is the mismatched one; the bug is downstream, in how `GatheredScan`'s rate/fixed constants
(`cost.rs`'s `PhysicalPlan::GatheredScan` arm) convert these already-correct features into nanoseconds
for this acquire branch specifically.

**Ranked bucket table** (flat-conjunction sample, `structure:sorted-category-tuple`, same taxonomy
style as Round 5 but rebuilt for this population -- `arith`/`text`/`collection`/`broadcast`/`range`/
`rarity`/`legality`/`loyalty` categories, since Round 5's range-family taxonomy under-describes a
population dominated by families no printing-range machinery ever sees):

```
bucket                                 n   share(abs ns err)  med_ns  med_scan_units  med_eval_domain  within25%
single:arith                        2188              25.3%    0.66            1.00             1.00        0%
and2:arith+range                     681              16.6%    0.71            1.74             1.00       30%
single:collection                    724              13.9%    0.66            1.00             1.00        4%
single:text                         8420               6.6%    0.58            1.00             1.00       14%
and2:collection+range                289               5.8%    0.61            1.70             1.00       12%
and2:range+text                     2799               4.4%    0.63            1.16             1.00       19%
and2:arith+rarity                    142               4.2%    0.59            1.72             1.00       27%
and2:arith+broadcast                 662               2.7%    0.66            1.00             1.00        1%
and2:broadcast+collection            269               2.6%    0.49            1.00             1.00        1%
and2:arith+collection                1291              2.6%    0.52            1.00             1.00        8%
```

(`arith` = extended syntax over `power`/`toughness`/`cmc` compounds, never `is_broadcast_leaf_shape`-
eligible since that gate requires a bare `NumField`, not a `NumExpr::Add`, so every arith predicate
lands in `candidates` unconditionally; `collection` = `type`/`keyword`/`tag`/`produces`/`set`/`border`/
`frame`/`watermark`/`devotion`; `broadcast` = `color`/`identity`/`cmc`/`pow`/`tou` singleton leaves that
usually escape to `Prep::Plane` but land here when paired with a non-composable partner.)

**Not shape-concentrated -- broad-based instead.** Every top-10 bucket reads `median_ns` in a tight
0.49-0.71 band regardless of which families are involved -- text-only, collection-only, and every
arith combination all cluster together. This is the opposite of Rounds 3-7's range-leaf findings,
where the fix was scoped to one precise shape; here the shape taxonomy is not the axis that
separates fixed from broken. `scan_units`'s per-bucket median tells the same story from a different
angle: it reads exactly 1.00 (agreeing with the real count) for every bucket where the query's
predicates carry high selectivity relative to the corpus, and 1.16-1.74 (UNDER-predicting) for the
`range`/`rarity`-paired buckets -- i.e. the one feature that DOES vary across buckets moves in the
wrong direction to explain a uniform over-cost.

**What actually separates fast-and-cheap from over-costed: `eval_domain` SIZE and match rate, not
shape.** Cutting the same sample by predicted `eval_domain` decile:

```
eval_domain range        n      median ns_ratio
0                     13,635          0.25   (deciles 0-2, exactly zero candidates)
(0, 2]                 4,545          0.39
(2, 9]                 4,545          0.46
(9, 23]                4,545          0.53
(23, 57]               4,545          0.60
(57, 161]              4,545          0.62
(161, 937]             4,545          0.68
(937, 31724]           4,546          0.68
```

and by verify-cost tier (`residual_tier_ns100`, from `filter.rs`'s `verify_cost_tier`):

```
tier                          n   share(abs ns err)   median ns_ratio
MASK_COMPARE (400)         7,102              49.0%              0.49
0 / all_match_known       15,590              39.3%              0.57
SET_LOOKUP (900)          16,490               9.6%              0.47
TEXT_SCAN (2,300)          5,578               1.4%              0.38
REGEX_MACHINERY (5,000)     691               0.7%              1.59
```

The two biggest tiers by magnitude (MASK_COMPARE, all_match_known) are not the two most *miscalibrated*
by ratio -- they dominate by ROW COUNT (88% of rows between them), same "volume, not tier-specific
miscalibration" pattern the doc has seen before. The real signal is the monotonic decay above: ratio
degrades steadily as `eval_domain` shrinks toward zero, which points at **two separate, compounding
mechanisms** rather than one shape-specific bug:

1. **`GATHER_FIXED_COST_NS` (169.6ns) is ~4x too high for zero-match rounds.** 14,657 of the 45,451
   sampled rows (32%) have `matches == 0` -- every multiplicative term in `PhysicalPlan::GatheredScan`'s
   `cost.rs` formula vanishes, so `predicted_ns` collapses to exactly `GATHER_FIXED_COST_NS` (median
   predicted 169.6ns, matching the constant to the decimal). Real measured cost for these rounds: median
   42.0ns -- a clean, isolated, shape-independent 4x over-charge with no other term involved. Cheap in
   absolute ns per query, but 32% of the whole `candidates` population by row count, so it alone would
   move a meaningful share of the within-25% pass rate.

2. **Card-mode's `feats.matches = count` (unconditional, `candidate_feats`, `lib.rs` ~11776) ignores real
   residual selectivity, and the per-candidate verify-tier charge doesn't discount for it either.**
   Printing/artwork mode already has a residual-pass-rate discount here (`RESIDUAL_PASS_RATE_PRINTING`/
   `_ARTWORK`); card mode has none -- `matches` is the full candidate count regardless of whether
   `all_match_known` holds. Concrete example, resampled 41 times in this run (`is:vanilla`, a static
   `tag`-family value): `eval_domain = pred_matches = 17,437` (`residual_card_invariant = true`, tier
   `MASK_COMPARE`), but `real_matches_pushed = 343` -- **2.0%** of predicted. `predicted_ns ≈ 601,657`,
   `measured_ns ≈ 95,000`, ratio **0.16** -- worse than the zero-match mechanism above, and at a LARGE
   `eval_domain`, contradicting a naive "small eval_domain only" read of the decile table. Not an
   `is:`-specific artifact: the same `eval_domain >= 2,000` + `MASK_COMPARE` slice (n=756, 684 distinct
   queries) reads median ratio 0.67, and the non-`is:` members alone (`t:creature year>2001` ratio 0.39,
   `cmc>=power year>=1997` ratio 0.34, `name:s eur<=5.06` ratio 0.48, ...) show the same direction and
   comparable magnitude. Over the whole residual-present population (`tier > 0`, n=29,861):
   `real_matches_pushed / pred_matches` reads median 1.000 (most queries genuinely do have most
   candidates match) but **p10 0.033** -- a real, fat left tail of 30x-overestimated match counts, not
   a single outlier. `GATHER_PUSH_PER_MATCH_NS` (2.24 ns/match) explains only part of the gap in the
   `is:vanilla` example (~39K ns of the ~507K ns predicted-minus-measured gap); the dominant term is
   `eval_domain * (GATHER_LOOP_PER_CARD_NS + GATHER_CARD_PASS_NS + max(tier_ns, GATHER_RESIDUAL_FLOOR_NS))`
   (~449K ns of that gap) -- i.e. the flat per-candidate verify-tier charge itself is too high whenever
   the residual is this selective, plausibly because a real `card_pass` short-circuits cheaply on most
   candidates at low match rates in a way `verify_cost_tier`'s single-node "worst child wins" model
   cannot see, and `GATHER_RESIDUAL_FLOOR_NS` (18.89, calibrated -- per its own doc comment -- against
   `bench_streamed_loop`'s always-true `DateCmp` design, a HIGH-match-rate population) may not transfer
   to a low-match-rate residual the way that comment's own precedent ("the third time this file has
   caught the same artifact") would predict. Both `residual_card_invariant = 0` (n=25,677, median ratio
   0.47) and `= 1` (n=4,184, median ratio 0.38) show the same direction, so this is not exclusive to
   card-invariant residuals either.

**Pooling check (the task's explicit ask): does the over-cost direction hold uniformly, or does it
mask an opposite error?** Within the flat-conjunction sample, YES it holds uniformly at the AST-shape
level (every top-10 bucket's median sits in 0.49-0.71, no bucket flips sign) -- but the
`scan_units`-feature check above already found the masked opposite: several buckets' `scan_units`
*feature* under-predicts (1.16-1.74x) inside the SAME rows whose *time* prediction over-costs, meaning
a naive "fix scan_units" reading of this cell would move the wrong lever. The real masking is
structural rather than per-bucket: `bench_cost_model_agreement.py`'s flat-conjunction sampler cannot
produce the population below at all, so its 13% headline is blind to it entirely, not merely diluting it.

**A third, structurally invisible population: `Or`/negation/nested-paren connectives.** Sampled via
`structured_query()` (`STRUCTURES`, never reachable through `sampler.query()`), 29,192 candidates rows:

```
structure         n   share(abs ns err)  median ns  p10   p90   within25%
regex          3,812              33.4%       0.54  0.23  1.28        7%
neg-or         3,895              18.8%       0.96  0.36  2.27       23%
or3            2,203              16.3%       0.72  0.36  1.41       27%
and-of-ors     2,650               9.7%       1.05  0.40  3.48       22%
or2            1,668               8.0%       0.68  0.34  1.28       21%
neg-and        2,519               5.9%       0.58  0.29  1.25       17%
paren-or       2,637               4.9%       1.07  0.40  2.62       21%
and-or         2,238               2.4%       0.68  0.25  1.96       19%
and2/and3/and4/single (this run)  7,570        0.5%  0.25-0.63  0.24-0.41  0.69-0.93   4-10%
```

This population's median ratios (0.54-1.07) look far closer to the `[0.8, 1.25]` bar than the flat-
conjunction population's do -- but the p90 column tells the opposite story: 1.25-3.48x, a severe
UNDER-cost tail, the OPPOSITE direction from the flat-conjunction over-cost. Pooling this in with the
flat population (which the real `bench_cost_model_agreement.py` never does, since it cannot sample
`Or`/negation at all) would report something close to "fine," masking a tail that is large enough by
row-count share (regex alone is 33.4% of THIS sample's pooled error) to plausibly drive real routing
regret -- a query whose true cost is 2-3x its prediction can lose an argmin to a plan that looks
cheaper on paper but isn't. This population needs its own round; it cannot be fixed by the same lever
as the flat-conjunction findings above (median direction is opposite), and no existing harness tracks
it at all.

**What Round 9 should target, in order:**

1. **`GATHER_FIXED_COST_NS` for zero-match `candidates`-acquired `GatheredScan` rounds** (mechanism 1
   above) -- cleanest, most isolated, no shape dependency, same "precomputed floor constant" pattern as
   every prior round's fix; likely the highest-confidence, lowest-risk first move given how cleanly it
   isolates (predicted collapses to exactly one constant, real measured is a flat ~42ns).
2. **Card-mode's `feats.matches` / the per-candidate verify-tier charge at low real match rates**
   (mechanism 2) -- larger in magnitude (dominates the top-10 bucket table) but needs a genuine
   calibration/held-out split against the real `card_pass` short-circuit behavior before trusting a
   constant, not just a flat scale reused from mechanism 1; the price-triple-style correlation check
   from Rounds 2-3 has no equivalent risk here (no independence-product combination proposed), but the
   held-out split discipline from every prior round still applies.
3. **The `Or`/negation/nested-paren population**, once 1-2 are shipped and re-measured -- needs its own
   sampler wired into whatever harness tracks it going forward, since `bench_cost_model_agreement.py`'s
   own generator structurally cannot see it.

### Round 9

Took Round 8's item 1 (`GATHER_FIXED_COST_NS` for zero-match `candidates`-acquired `GatheredScan`
rounds). First fix in this doc that lives in `cost.rs`'s cost FORMULA rather than `lib.rs` feature
estimation -- Rounds 1-8 all fixed a feature (`scan_units`/`eval_domain`) feeding an otherwise-correct
formula; here the features are already exact and the RATE/FIXED constant converting them to ns is
wrong.

**Independent re-confirmation of Round 8's diagnosis.** Fresh sample (not Round 8's own, a new
throwaway sampler, uniform mode, seed 0, 240s, isolated release wheel): 31,030 `GatheredScan`/
`candidates` rows, 9,890 (31.9%) with `matches == 0` -- matching Round 8's reported 32% closely.
Checked every non-fixed term individually rather than trusting the "collapses to one constant" claim:

```
field                     nonzero_count / n     max
eval_domain                      0 / 9,890        0
scan_units                       0 / 9,890        0
artwork_seen_printings           0 / 9,890        0
cards_visited                    0 / 9,890        0
printings_examined                0 / 9,890        0
matches_pushed                    0 / 9,890        0
```

Every term this arm multiplies by really is zero (not just small) for this population, so
`predicted_ns` reads EXACTLY `169.6` (min == max == median across all 9,890 rows) -- confirmed, not
assumed. Real measured `plan_self_ns`: median 42.0, p10 41.0, p90 84.0 (bimodal: card/printing modes
cluster at ~42, artwork at ~84 -- see below). Ratio (measured/predicted): median 0.248, 0/9,890 (0.0%)
within [0.8, 1.25] -- confirms the ~4x over-charge exactly as Round 8 reported.

**Calibration.** Hash-of-query split (`sha256(q) % 2`), same rule as every prior round. Calibration
half (n=4,944): the L1-optimal single constant is the median measured `plan_self_ns`, `42.0` -- chosen
without looking at the held-out half. Held-out half (n=4,946):

```
                         before (169.6)     after (42.0)
median ratio                   0.248             1.000
within-25%                      0.1%            57.7%
total abs ns error           530,256           103,110   (5.1x reduction)
paired diff: 4,577 improved / 369 regressed / 0 tied
```

**Per-mode split, not chased.** `PlanFeatures` carries no `unique`/mode field this arm can read, so
one pooled constant is what `cost.rs` alone can express. Held-out breakdown by mode: card (n=1,657)
83.6% within-25%, printing (n=1,625) 90.0%, artwork (n=1,664) 0.3% -- artwork's real zero-match cost
reads a flat ~2x higher (~84ns vs. card/printing's ~42ns, plausibly `exec_gathered_scan`'s
unconditional per-printing dedupe check setup), so a single pooled constant necessarily leaves
artwork's ratio at ~2.0 (previously ~0.495 -- same log-magnitude, flipped sign, and still a net win on
absolute ns error: |84-169.6|=85.6 -> |84-42|=42.0). Splitting this by mode needs a new `PlanFeatures`
field, which needs a `lib.rs` change -- out of scope for a `cost.rs`-only round, noted for later.

**A gate-precision risk found and checked, not assumed safe.** `matches == 0` is not exclusive to the
`candidates`/`plane` acquire branches Round 8 scoped its diagnosis to. `explain_analyze` costs every
CANDIDATE plan from one shared `PlanFeatures` per acquire (`plan_cost(plan, &facts.feats)`, called
once per plan in a loop at `lib.rs:12917`), so `GatheredScan`'s own `matches == 0` also fires when it
is costed under a `printing_compose`/`card_range_popcount`/`printing_range_scan` (RANGE_ACQUIRES)
acquire branch -- and there, `eval_domain == 0` is not a real empty candidate list, it is this
branch's shared `feats` never having computed one for `GatheredScan` specifically (the acquire chose a
different plan and never ran `prepare_candidates`). If `GatheredScan` is later picked or forced as a
competitor, dispatch pays a REAL `prepare_candidates` rebuild (`plan_self_ns` adds `ns_prepare` back in
for RANGE_ACQUIRES, per `costbench`'s netting rule) that no term in this arm prices at all -- sampled
directly: 375 `printing_compose`-acquired `GatheredScan`/`matches==0` rows, 358 with `eval_domain==0`,
median measured 4,959ns against a predicted 169.6ns (29x under, PRE-EXISTING, not caused by this
round). Lowering the fixed cost to 42.0 makes this already-broken slice numerically worse in isolation
(29x -> 118x under) — same direction, no new sign flip.

Checked for a REAL regression, not just reasoned about: this population is not purely diagnostic —
`GatheredScan` is the actually-`picked` plan in 93/96 (97%) of sampled zero-match `printing_compose`-
acquire rows. Directly diffed a same-build wheel and found 2 genuine routing flips in a 107-row sample
of RANGE_ACQUIRES zero-match rows where a competing plan's predicted cost sat between the old (169.6)
and new (42.0) constant (`date<1993-08-05` and `tix<0.01` under `printing_range_scan`: `PrintingRangeScan`
predicted 150.0 picked at baseline, `GatheredScan` predicted 42.0 picked after this round's change).
Ran the two tools built to catch exactly this:

- `bench_regret_matrix.py --seconds 60 --seed 0`: total regret 27.6ms baseline, 27.6ms modified (18,181
  vs 18,349 multi-plan queries — wall-clock-budget variance, not a code effect); no new row in the
  `picked -> best` mismatch table.
- `bench_cost_model_agreement.py --seconds 300 --seed 0`: `GatheredScan/printing_compose` unchanged
  (n=58,444→59,178, median 1.15→1.14, within-25% 24%→24%); `GatheredScan/printing_range_scan` unchanged
  (median 1.09→1.08, 61%→62%); `GatheredScan/card_range_popcount` unchanged (median 0.96→0.97, 51%→51%).
  12/17 acquire-branch cells inside [0.8, 1.25] both builds (unchanged); by-unique table improves 9/12
  -> 10/12 (`GatheredScan/card` flips FAIL -> PASS).

So the affected RANGE_ACQUIRES slice is real, pre-existing, and made worse in isolated ratio terms, but
too small (2 flips in 107 sampled rows; the whole slice is ~2% of its own already-passing pooled cell)
to move any reported cell or the regret total. `matches == 0` is therefore a correlated proxy, not the
exact phenomenon Round 8 scoped ("`Prep::Candidates` zero-match"), and a future round that can touch
`lib.rs` should add an acquire-branch-aware feature (or a `real_candidates_built: bool`) to gate this
cleanly rather than relying on this round's empirical "checked, found immaterial" result indefinitely.

### Round 28

Round 27 ([reference-engine-cost-model-cleanup-final-ab.md](reference-engine-cost-model-cleanup-final-ab.md))
ran the first fresh, paired `main`-vs-`costcell/trunk` A/B this whole 27-round effort had done and
found a real, previously-invisible regression: `bench_feature_accuracy.py`'s pooled `scan_units`
feature (graded against the real `printings_examined` counter, not against a rate-fit like
`bench_cost_model_agreement.py`) reads clean on `main` (median 1.00) but `UNDER-COUNTS` on
`costcell/trunk` (median 0.70). This section is the follow-up round tasked with finding and fixing it.

**Bisection.** Built an isolated release wheel at every `Engine:` commit between `main` and
`costcell/trunk`'s tip (17 candidates) and ran `bench_feature_accuracy.py`'s pooled `scan_units`
reading at each. Clean through Round 6 (`ce860337`, pooled median 0.94). The very next commit,
`e1c40466` ("A Broad-Guard Scale for PrintingCompose's Own Bare/Fused Range Reset", this doc's own
Round 7 above), drops it to 0.69 — the exact commit that tips the pooled metric from PASS to FAIL.
Every commit after that (Rounds 9, 14/15's verify-bypass work, Round 22's `best_other` gate, Round
24's `PairTotals` extension) holds steady at 0.68-0.70, confirming Round 7 is the trigger, not a later
round compounding an already-broken number.

**Mechanism.** `e1c40466`'s own fit — and Round 4's `COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` fit above it
— were each calibrated exclusively against `unique=card` samples (Round 4: "Sampled 1,500 and2/and3
RANGE_FAMILIES queries (`unique=card`..."; Round 7: "the same shape" as Round 6's own
`unique={"card"}` generator). But the guard both scales live in (`acquire_plan_features`, the branch
starting `let (eval_domain, scan_units) = if ... range_too_broad_to_narrow(...)`) runs *after* the
`match mode { Mode::Printing => ..., Mode::Card => ..., Mode::Artwork => ... }` block, unconditional on
`mode` — so both scales were applied to `Mode::Printing`/`Mode::Artwork` too, shapes neither
calibration sample ever contained.

Checked directly rather than assumed: a fresh sample of this exact guard-fired population, split by
`unique`, reading `printings_examined / n_printings` (the real, unscaled ground truth):

```
('broad', 'card'):     n=303  p10=0.520  p50=0.520  p90=1.127
('broad', 'printing'): n=230  p10=0.520  p50=0.520  p90=0.520
('broad', 'artwork'):  n=956  p10=0.520  p50=0.520  p90=0.520
```

`Mode::Printing`/`Mode::Artwork` read **exactly** 0.520 at every percentile — zero spread, because
`printings_examined == n_printings` on every single row: those two modes' materializing kernels never
short-circuit, so a query broad enough to fire this guard really does walk the *entire* candidate
printing span, always. `Mode::Card`'s own kernels do short-circuit per candidate (the property both
scales were fit to exploit), which is why its own ratio has real spread (p90 1.127, not pinned to
0.520). Applying `COMPOSE_SAME_RANGE_BROAD_SCAN_SCALE`/`COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` to
Printing/Artwork mode was therefore manufacturing a clean, deterministic ~0.52x/0.7x under-count out
of a population whose true ratio is 1.0 — not a mixed population, not noise, a mode-scoping bug with a
single, uniform failure mode.

This is *also* why the pooled metric moved as much as it did despite `scan_units [printing_compose]`'s
own per-acquire median barely changing (0.39 → 0.39 across the fix): the guard-fired subset is only
~4-12% of `printing_compose`'s rows per mode (dwarfed by the pre-existing, separately-tracked "narrow"
bucket — the `range_too_broad_to_narrow`-NOT-fired population Round 7 above already named and
deferred: "a card-count-shaped estimate undershooting a printing count... not fixed this round"). But
before this fix, those rows sat *above* 1.0 (the OLD unscaled `n_printings` ceiling, ~1.9-2.0x
over-counted per Round 7's own measurement) — moving them down to 0.52 didn't change the sub-bucket's
own median, but it did remove ~1,500-2,200 rows from *above* the global rank used to compute the
POOLED median, letting that rank fall into the dense, already-under-counted "narrow" bucket below it.
Round 7's fix was a real, validated improvement for the `unique=card` population it targeted — the
mode-scoping bug is what let a genuine fix for one mode quietly worsen the pooled number by removing a
compensating error for two others.

**Fix.** Gated both `is_cross_index_range_and`'s and `is_same_index_range_only`'s scale branches on
`matches!(mode, Mode::Card)`; `Mode::Printing`/`Mode::Artwork` now fall to the existing `else` branch
(the unscaled `n_printings` ceiling, already correct for every other shape reaching this guard). Zero
new computation — `mode` is already a bound local, the added check is a single enum-tag comparison —
so this carries no acquire-time cost, per this doc's own pre-computation constraint.

**Results** (isolated release wheels, `bench_feature_accuracy.py --seconds 300 --seed 0`, `main` @
`ca016410`, branch tip @ `865fb03e`):

```
                          pooled scan_units median   verdict
main                              1.00                (clean)
costcell/trunk (unfixed)         0.70                UNDER-COUNTS
costcell/trunk (fixed)           0.94                (clean)
```

The regression is closed: 0.94 sits inside the same `[0.8, 1.25]` agreement band `main`'s own 1.00
does, with no verdict flag. The residual gap between 0.94 and `main`'s 1.00 is the two *other*,
already-documented, separately-tracked contributors this round did not touch: the "narrow"-bucket
`PrintingCompose` under-count Round 7 itself named and deferred above, and the era-correlated
print-position confound for bare existential leaves
([local-engine-domain-cards-existential-arith-and.md](local-engine-domain-cards-existential-arith-and.md)'s
Round 25 section, "confirmed real, confirmed severe... out of \[that round's\] blast radius"). Both are
real, both pre-date this fix, and neither is a regression introduced by any commit on this branch —
fixing either would need touching `domain_cards`'s own broad-range estimate for bare ranges (the
`RangeCardCounts::distinct_cards` undercount this doc's own `scan_all` comments already name), which
nine prior rounds of this same effort found hard and did not attempt; left open, matching this doc's
own "Next steps for a future round" note under Round 7.

**Correctness gates.** `cargo test --manifest-path card_engine/Cargo.toml`: 177 passed, 0 failed
(debug); `--release`: 176 passed, 0 failed. `cargo clippy --manifest-path card_engine/Cargo.toml
--all-targets -- -D warnings`: clean. `git diff --stat costcell/trunk`: `card_engine/src/lib.rs` only
(19 lines).

**Confirmation pass**, before (unfixed tip) vs after (fixed), plus `main` where noted:

- `bench_regret_matrix.py --seconds 120 --mode realistic` (routed-phases builds): mean regret/query
  0.94µs (tip) → 0.95µs (fixed) → 0.95µs (`main`) — unchanged within noise; `picked -> best` SHARE
  table proportionally identical (`Perm` 69%→69%, `Gather` 15%→14%, `StreamedSelect -> GatheredScan`
  50%→47% of a shrinking pie), no anomalous transition.
- `bench_cost_model_agreement.py --seconds 300 --seed 0`: 12/17 (tip) → 13/17 (fixed) cells inside
  `[0.8, 1.25]`; every reported cell moved by less than 0.05 in ratio, one boundary flip
  (`GatheredScan/candidates` 0.78→0.81) consistent with sampling noise, not a real shift — matches
  this tool's own documented insensitivity to a feature-only fix (a rate elsewhere absorbs it).
- `bench_query_latency_ab.py --mode realistic --sample 800 --seed 1`, two order-alternated rounds plus
  a same-build canary: round 1 (tip, fixed) `+0.9µs` "B is SLOWER"; round 2 (tip, fixed) `-0.3µs` "B is
  FASTER"; canary (fixed vs fixed) `0.0µs`, CI `[-0.2, +0.3]`, no detectable difference. Opposite signs
  of comparable magnitude across the two real rounds, both inside the canary's own noise band — no
  detectable latency effect, expected for a zero-new-computation accuracy fix.
- `bench_pairwise_ordering.py --seconds 60`, realistic and uniform, `GatheredScan` vs `PrintingCompose`:
  realistic overall 89%→89% (`[printing_compose]` 91%→90%, `[plane]` 84%→86%); uniform overall 87%→87%
  (`[printing_compose]` 86%→86%). Essentially unchanged in both modes — unlike Round 7's own change,
  this fix does not touch the ordering that mattered to `#852`.

### Round 30

**Regression found by a prior diagnostic round, confirmed by bisection + literal replay (not just
correlation):** Round 1's own `scan_all` fix above (the match-density depth proxy) was legitimate and
already validated for `GatheredScan`'s `scan_units` -- but `StreamedSelect`'s own feature,
`stream_scan_units`, defaults to inheriting `scan_units` verbatim (`mk_plan_feats`'s doc: "only an
acquire that knows P3 examines fewer printings overrides it") unless the `printing_compose` acquire's
own override logic (`lib.rs`, the `feats.stream_scan_units = if tier == 0 {...} else if
filter_touches_legality(...) {...} else {...}` block) says otherwise. For a printing-varying leaf with
no legality partner (`price_usd`/`cn`/`released_at`, or an And of them), that block falls to its bare
`else { scan_units as u32 }` arm -- so Round 1's legitimate downward revision to `scan_units` rode
straight through into `stream_scan_units` too, with no acquire branch ever taught the difference. This
grew the `StreamedSelect -> GatheredScan` misroute (router picks P3 when P4 is actually faster) from
1,284 to 1,618 occurrences (mean regret 17.0us -> 21.4us) on matched-size `bench_regret_matrix.py
--mode realistic` runs -- the single largest remaining regret slice on the branch (43% share) going
into this round.

**Mechanism, confirmed directly against real dispatch counters** (not assumed from reading the code
alone): `run_query_streamed` (P3's executor) runs a first pass (`card_match_count`, over every
candidate) that is structurally identical to `GatheredScan`'s own single pass in `Mode::Card` -- both
break at the first printing satisfying the residual under `Prefer::Default`, confirmed by matching
`printings_examined` counters exactly (2,449 on both plans, `f:pioneer cn>=30 cn<=39`). What differs is
a SECOND pass this first pass's counter never sees: `run_query_streamed`'s `total <= *STREAM_MIN_MATCHES`
branch re-derives `card_pass` and re-walks the printing span for every MATCHING card a second time to
select the page (`push_card_matches`, called again, its return value discarded -- so
`printings_examined`, and therefore any `scan_units`-shaped feature, structurally cannot see this
second pass no matter how it's computed). `cost.rs`'s `StreamedSelect` arm already has a term for this
branch's OWN O(n_cards) "scan every stored count" overhead (`STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS *
n_cards`), but that floor is a per-CORPUS constant that cannot vary with a query's own `matches` count --
it was fit on a population where `matches` was small enough that the actual per-card REDO was
negligible next to the floor. On `f:pioneer cn>=30 cn<=39` (853 real matches, close to the
`STREAM_MIN_MATCHES` ceiling of 1,024) the floor alone (32.4us, `n_cards=31,724 * 1.02`) materially
undershoots the real `ns_finish` (65.3us) -- the remainder is exactly this unpriced redo, and it is why
`StreamedSelect`'s real dispatch (108.9us) is 2.3x `GatheredScan`'s (46.6us) despite identical
`printings_examined`.

**Fix** (`card_engine/src/lib.rs`, the `printing_compose` acquire's `feats.stream_scan_units` bare
`else` arm): adds a `STREAM_SMALL_TOTAL_REDO_BIAS` (`1.32`, a new lib.rs constant, NOT a `cost.rs` rate)
scaled term on top of the inherited `scan_units`, `Mode::Card` only. The redo-candidate count is the
acquire-time `result_total` estimate when it sits at or below `STREAM_MIN_MATCHES` (mirroring the same
threshold `compose_paging_with_total` already gates its own decline prediction on, a few hundred lines
up in the same function), else capped at `feats.limit` (the permutation-walk branch's own bound) rather
than dropped to zero outright -- a hard cliff at the threshold would turn the acquire estimate's own
noise into an all-or-nothing coin flip, which matters here specifically: this round's own concrete
example's acquire-time estimate (1,983) sits ABOVE the 1,024 threshold despite its REAL total (853)
landing inside the small-total branch.

**Calibration.** Bias fit against `ns_finish` minus the existing floor's own contribution (isolating
the previously-unpriced redo specifically, not re-deriving the floor), converted to `stream_scan_units`
units via the existing, untouched `STREAM_SCAN_PER_ROW_NS` (5.97), over a held-in/held-out split
(hash-of-query, 1,875/1,949 rows) of `unique=card` `printing_compose` rows where the acquire-time
estimate gates the correction AND real dispatch confirms the small-total branch actually ran
(`perm_steps == 0`, `matches_pushed > 0`). Median fitted bias 1.32 "printing units" per redone
candidate; held-out total absolute error on the unpriced remainder: 2.18e7 -> 2.06e7 (a real but
partial reduction -- this population's per-query redo cost is heavy-tailed (implied bias p10 -38.7, p90
12.7), dominated by per-query residual complexity this feature vector has no term for, not by candidate
count alone). A 4x/8x/30x sweep of the bias against the live routing-outcome metric below (not just the
ns-error metric) showed diminishing returns fast -- 5.9%->7.3% of a broader current-trunk
misroute sample fixed for a doubling of the false-positive rate on already-correct `StreamedSelect`
picks -- so the median (lowest false-positive rate, still measurably useful) was kept rather than
chasing the sweep.

**Flip-query validation.** Reproduced the ORIGINAL flip population exactly as the diagnostic round's
own `flip_finder_f3f4a017.py` does (BEFORE=`97dc30c8`, AFTER=`f3f4a017`, same seed/sample window): 114
queries found this run (consistent with the diagnostic round's own ~120, sampling noise). Replayed
against this round's FIX build (current `costcell/trunk` tip + the patch above):

```
of 114 reproduced f3f4a017 flip queries:
  now correctly pick GatheredScan (fixed):        50  (44%)
  still (wrongly) pick StreamedSelect (unchanged): 64  (56%)
  pick something else:                              0
```

**Regret matrix** (`bench_regret_matrix.py --seconds 300 --mode realistic --seed 0`, isolated release
wheels, baseline = unfixed `costcell/trunk` tip `4e101d7f` vs fix = this round's patch on top). The two
300s windows sampled different absolute query counts (121,724 vs 108,533 multi-plan queries -- system
load from other concurrent work on this box, not a code-speed effect; rates/shares below are the fair
comparison, not raw `n`):

```
StreamedSelect -> GatheredScan      n            share of traffic   mean regret   SHARE   -> ~ms
baseline (unfixed)                2,407   1.98% of 121,724 sampled    23.00us      53%    ~55.4ms
fix                                1,995   1.84% of 108,533 sampled    24.33us      56%    ~48.5ms
```

~7% fewer misroutes as a share of traffic, ~12% less absolute regret-ms attributed to this specific
transition. Total pool regret (all transitions) 104.3ms -> 86.2ms (mean/query 0.86us -> 0.79us), roughly
consistent in direction with the targeted slice, not dramatically larger -- no sign the fix disturbed
other transitions. (No dedicated same-build latency canary was run this round on top of this -- the
regret figures come from forced per-plan trial minimums, not wall-clock query timing, which is less
exposed to the sampling-count variance noted above, but a canary would still be the stronger claim; flag
this as the one gap in this round's own validation rigor.)

**Regression guards.**

- `#852` (`GatheredScan` vs `PrintingCompose` ordering, `bench_pairwise_ordering.py --seconds 300
  --mode realistic --seed 0`): overall 88% -> 88%, unchanged. By acquire: `[plane]` 83% -> 82%,
  `[printing_compose]` 91% -> 92% -- both within noise, no real shift. Clean.
- Round 28's `scan_units` feature-accuracy fix (`bench_feature_accuracy.py --seconds 120 --mode
  realistic --seed 0`): pooled `scan_units` median 1.00 -> 1.00, identical distribution shape in both
  builds -- expected, since this round's patch touches only `stream_scan_units`, never `scan_units`
  itself. Clean.

**Correctness gates.** `cargo test --release` (`card_engine`): 176/176 passed. `cargo test` (debug):
177/177 passed. `cargo clippy --all-targets -- -D warnings`: clean. Blast radius: `card_engine/src/lib.rs`
(the `printing_compose` acquire branch only) plus this doc; `cost.rs`, `estimator.rs`, `filter.rs`
untouched.

**Verdict.** Real, positive, but partial. On the population this round diagnosed and targeted directly
(the reproduced f3f4a017 flip set), 44% now route correctly again. On the broader regret matrix, the
`StreamedSelect -> GatheredScan` transition's regret is down ~7-12%, not back to `main`'s pre-regression
baseline. The residual is NOT well-explained by `cost.rs`'s rates (chunk 2's stated scope) -- it traces
to the acquire-time `result_total` estimate itself being unreliable near the `STREAM_MIN_MATCHES`
threshold for cross-index-range-leaf Ands (this round's own concrete example: real total 853, estimate
1,983, off by 2.3x), which is the SAME "separate, uninvestigated `domain_cards` bug for multi-range-index
Ands" this doc's own Round 1 section flagged as "the natural next target" and never chased. A future
round fixing that upstream cardinality estimate would likely close more of this residual than any
`cost.rs` rate refit; chunk 2 (the rate refit) still looks worth doing on its own merits but should not
be expected to finish closing this specific misroute on its own.

### Round 31

**The gap Round 30 flagged but couldn't close.** Round 30 fit `STREAM_SMALL_TOTAL_REDO_BIAS` against
`ns_finish` minus the existing floor's own contribution -- a wall-clock RESIDUAL, converted to
`stream_scan_units` units via the untouched `STREAM_SCAN_PER_ROW_NS` rate -- because no structural
counter existed for the redo pass's real work. `push_card_matches` (`lib.rs:6186`) already computes
and returns a `u32` "examined" count per call, mirroring `card_match_count`'s own `(c, examined)`
pattern -- both calls inside `run_query_streamed`'s `total <= *STREAM_MIN_MATCHES` branch's second
loop (~line 13940) simply discarded it as a bare statement.

**Step 1: the counter.** Added `PhaseStats::redo_examined: u64`, a new field zero everywhere except
this one branch (following the `set_printings`/`perm_steps` precedent: doc-declared scope, zeroed
explicitly at the other two exits of `run_query_streamed` -- the empty/past-the-end return and the
permutation walk). The small-total loop now accumulates `push_card_matches`'s return value into a
local (`n_redo_examined`) and passes it to the `publish` closure, which now takes a fourth parameter
alongside `perm_steps`. The permutation walk's OWN `push_card_matches` call (after `'walk: for cid in
walk.iter()...`) is deliberately left uninstrumented: that branch runs above `STREAM_MIN_MATCHES`,
already prices to `limit`, and its own per-step cost already flows into `ns_loop`/`ns_finish` via the
walk's wall-clock timing, the same population `perm_steps` was already calibrated against -- this
round's scope is specifically the small-total branch's previously-unpriced second pass. Surfaced to
Python exactly like `printings_examined`/`perm_steps`: a new `d.set_item("redo_examined", ...)` line in
`plan_trial_to_pydict`, and a matching entry in `scripts/costbench.py`'s `PLAN_KEYS` schema assertion.

**Free, confirmed directly, not assumed.** `push_card_matches` already computed this value on every
call in this loop; capturing it is a return-value read, not a new pass or a new computation -- no
counter, no extra field write, nothing added to what the loop already does. Confirmed both ways: (a)
by inspection -- the diff is exactly "capture the return instead of discarding it" -- and (b) directly:
temporarily reverting the capture to a bare statement (matching pre-round code) makes the new
regression test fail on its very first assertion (`redo_examined > 0`), and restoring it passes again,
with `cargo test --release` timing unaffected in either direction (the change is a single local
accumulate plus one extra `u64` in an already-stack-allocated struct).

**Regression test** (`card_engine/src/tests.rs`,
`redo_examined_counts_only_the_small_total_redo_pass`): one synthetic corpus, two disjoint match groups
(500 cards under `STREAM_MIN_MATCHES`, 2,000 over it), asserting `redo_examined > 0` and
`>= matches_pushed` on the small-total exit (`perm_steps == 0`), `== 0` on `GatheredScan` for the
identical query, and `== 0` on the walk exit (`perm_steps > 0`) for the large group. Verified to
actually catch a revert: reverting the capture to a bare statement fails the test's first assertion
with `redo_examined read 0`, exactly the Round 30 gap this round closes.

**Step 2: the refit.** Sampled `unique=card` `printing_compose` rows from `bench_regret_matrix.py
--mode realistic`'s own corpus (isolated release wheel, `--seed 13`, hash-of-query
held-in/held-out split: 2,916/3,006), gated on the same real-dispatch confirmation Round 30 used
(`perm_steps == 0`, `matches_pushed > 0`) PLUS a guard Round 30's own gate missed: `page_offset <
matches_pushed`, ruling out the OTHER `perm_steps == 0` exit (`page_offset >= total` returns before the
redo loop ever runs but still reports the counting pass's `matches_pushed`) -- without it, 816/6,747
rows silently poisoned the fit with a real redo pass that never happened.

`redo_candidates` mirrors the acquire branch's own logic exactly: the acquire-time `matches` estimate
when it's at/under `STREAM_MIN_MATCHES`, else capped at the page `limit`. Two real summary statistics
of `real_redo_examined / redo_candidates` over the calib half, and they disagree:

```
median (per-row ratio)                         1.0    p10=0.15  p90=10.2
candidate-weighted mean (sum/sum)               2.237
```

Held-out total absolute error on the real `redo_examined` counter itself (the POINTWISE metric):

```
old (1.32, Round 30's wall-clock fit)          2.467e6
median (1.0)                                   2.380e6   <- best pointwise fit
weighted mean (2.237)                          2.752e6
p75 (3.831)                                    3.336e6
```

By pointwise error alone, the median (1.0) wins -- a real, ground-truth-validated improvement over
1.32. But this population's ratio is heavily right-skewed (p10 0.15, p90 10.2: most rows sit near or
under 1.0, but a long tail runs into double digits), and the flip-query population this bias exists to
fix draws disproportionately from that tail -- a query only flips to the wrong plan when its real redo
cost was under-priced, which is exactly what the tail rows are. Checked directly rather than assumed:
replaying the same reproduced f3f4a017 flip set (below) against both candidates, the median
ACTIVELY REGRESSES queries Round 30's own 1.32 already fixed correctly, gaining nothing back. This is
the same false-positive/false-negative asymmetry Round 30's own 4x/8x/30x bias sweep found against its
noisier wall-clock-derived distribution -- resolved here against a real ground-truth counter instead of
a guessed multiplier. The ratio is flat (~1.0-1.3) across every candidate-count bucket (0-50, 50-150,
150-400, 400-1024), so the skew is in per-query residual complexity, not candidate count -- a flat
linear bias remains the right shape, matching Round 30's own conclusion.

**Fix.** `STREAM_SMALL_TOTAL_REDO_BIAS` set to **2.237** (the candidate-weighted mean), not the
pointwise-optimal 1.0 -- kept because it is the real, structurally-grounded statistic that does not
regress the live routing outcome, following the same "live outcome over pointwise ns-error" precedent
Round 30 itself set with its own bias sweep.

**Flip-query validation.** Reproduced the ORIGINAL flip population exactly as `flip_finder_f3f4a017.py`
does (BEFORE=`97dc30c8`, AFTER=`f3f4a017`, same seed/sample window), then replayed the SAME reproduced
list against three FIX builds in one script (removing the sampling-window noise a separately-run
validation would carry): Round 30's own tip (`9668dfa4`, bias 1.32), this round's pointwise-optimal
median (1.0), and this round's shipped weighted-mean (2.237).

```
of 118 reproduced f3f4a017 flip queries:
  round30 (bias=1.32):            fixed 52   still wrong 66
  round31 median (bias=1.0):      fixed 43   still wrong 75   (regresses 9 of round30's 52, gains 0)
  round31 weighted-mean (2.237):  fixed 64   still wrong 54   (regresses 0 of round30's 52, gains 12)
```

The shipped bias (2.237) regresses none of Round 30's 52 correct fixes and closes 12 more -- 64/118
(54%) now route correctly, up from Round 30's own 52/118 (44%) on this exact reproduced population (the
114/50 figure in Round 30's own doc entry came from a separate sampling run; both are the same
population modulo the classification-timing noise this whole method carries, already flagged in Round
30's own verdict).

**Regret matrix** (`bench_regret_matrix.py --seconds 300 --mode realistic --seed 0`, isolated release
wheels with `routed-phases`, before = `costcell/trunk` tip `9668dfa4` i.e. Round 30's own shipped fix,
after = this round's patch):

```
StreamedSelect -> GatheredScan      n            share of traffic   mean regret   SHARE   -> ~ms
before (Round 30's fix)           2,363   1.87% of 126,203 sampled     23.01us      49%    ~54.8ms
after (Round 31's refit)          2,129   1.73% of 123,143 sampled      9.80us      25%    ~20.7ms
```

Mean regret on this transition drops by 57% (23.01us -> 9.80us) and its SHARE of all lost time nearly
halves (49% -> 25%) -- ~54.8ms -> ~20.7ms attributed, a **62% reduction**, dwarfing Round 30's own
55.4ms -> 48.5ms (~12%). Total POOL regret (every transition) also drops, 111.9ms -> 82.7ms (mean/query
0.89us -> 0.67us) -- consistent in direction with the targeted slice, not an isolated artifact.

One nearby transition moved the other way and is worth naming rather than burying: `PrintingCompose ->
StreamedSelect` (compose picked, but StreamedSelect was really best) grew from 12% to 24% share (mean
34.28us -> 40.61us, n 396 -> 483, ~13.4ms -> ~19.8ms, +6.4ms) -- a real, expected side effect of raising
`stream_scan_units`: making StreamedSelect look pricier tips a few close compose-vs-stream calls the
other way when StreamedSelect actually was faster. Every other transition moved by less than 2 points of
SHARE in either direction. The target slice's ~34ms improvement outweighs this ~6ms give-back by 5:1,
and the total-pool number (111.9ms -> 82.7ms, -29.2ms net) confirms the net effect across the whole
matrix is a real improvement, not a wash.

**Regression guards.**

- `#852` (`GatheredScan` vs `PrintingCompose` ordering, `bench_pairwise_ordering.py --seconds 300
  --mode realistic --seed 0`): overall 88% -> 88%, unchanged. By acquire: `[plane]` 83% -> 83%,
  `[printing_compose]` 91% -> 91% -- identical in both builds, no shift at all. This round's own
  target pair, `GatheredScan` vs `StreamedSelect`, also held steady (97% -> 97% overall, 92% -> 92%
  `[printing_compose]`, 99% -> 99% `[candidates]`) -- the ordering `stream_scan_units` exists to get
  right did not regress even though its predicted GAP shrank (gap meas/pred 1.08 -> 0.55 overall):
  the model now predicts a LARGER gap than measured on this pair (conservative, not wrong-signed),
  and argmin correctness -- which side of the gap wins -- is what this guard actually checks. Clean.
- Round 28's `scan_units` feature-accuracy fix (`bench_feature_accuracy.py --seconds 120 --mode
  realistic --seed 0`): pooled `scan_units` median 1.00 -> 1.00, identical distribution in both
  builds -- expected, since this round's patch touches only `stream_scan_units`, never `scan_units`
  itself. Clean.
- Round 30's own fix: the flip-query check above IS this guard -- 0 of the 52 queries Round 30 fixed
  regressed under this round's refit.

**Correctness gates.** `cargo test --release` (`card_engine`): 178/178 passed (177 + this round's new
regression test). `cargo test` (debug): 179/179 passed. `cargo clippy --all-targets -- -D warnings`:
clean. Blast radius: `card_engine/src/lib.rs` (the new counter, its plumbing, and the
`printing_compose` acquire branch's redo-bias constant), `card_engine/src/tests.rs` (one new
regression test), `scripts/costbench.py` (the `PLAN_KEYS` schema entry for the new field), this doc.
`cost.rs` untouched, per this round's own scope.

**Verdict.** Real, significantly larger, and better-grounded than Round 30's own fix. Cumulatively
(Round 30 + Round 31 together), the `StreamedSelect -> GatheredScan` transition's attributed regret
goes 55.4ms (Round 30's own "before") -> 48.5ms (Round 30's fix, ~12% closed) -> ~20.7ms (this round,
~62% closed relative to Round 30's own before-state) -- five times the closure Round 30's wall-clock-fit
bias achieved, using the SAME feature-level lever, just fit against real structural ground truth instead
of a noisy residual. On the reproduced flip-query population this ledger entry has tracked since Round
30: 44% (52/118) -> 54% (64/118) correctly routed, with zero regression of Round 30's own fixes.

It is not fully closed. 46% of the reproduced flip population (54/118) still wrongly picks
`StreamedSelect`, one nearby transition (`PrintingCompose -> StreamedSelect`) grew by ~6.4ms as a real
side effect of raising `stream_scan_units` (a 5:1 trade against the ~34ms gained, not free), and Round
30's own diagnosed DEEPER root cause -- the acquire-time `result_total` cardinality estimate itself
being unreliable for cross-index-range-leaf `And`s near the `STREAM_MIN_MATCHES` threshold (a
`domain_cards` estimation bug, the same "natural next target" this doc's own Round 1 section flagged
and no round has yet chased) -- is completely untouched by this round. This round improved WHAT the
bias is fit against (real counter vs. wall-clock residual) and refit the constant accordingly; it did
not touch `redo_candidates`' own input (the acquire-time estimate that feeds it), which is where the
residual almost certainly still lives.

On the parent punch-list's chunk 2 (`cost.rs` rate refit, `STREAM_SCAN_PER_ROW_NS` itself): this
round's own data argues against urgency there, not for it. The real ratio read flat across every
candidate-count bucket (no saturation, no shape mismatch a rate change would fix), and a feature-level
fix alone -- with no `cost.rs` change at all -- closed 5x more of this regression than Round 30's own
attempt. A rate refit was never tested directly this round and remains formally open, but the
evidence so far suggests the acquire-time cardinality estimate (not the per-unit rate) is the more
promising next target, exactly as Round 30's own verdict already concluded.

### Round 32

**A different term than Rounds 30/31** (`walks_permutation`, the branch taken when `total >
STREAM_MIN_MATCHES`, as against Rounds 30/31's small-total gather), flagged by `cost.rs`'s own
`perm_steps` comment: the estimate (`page_span * n_cards / matches`, capped at `n_cards`) assumes
matches spread uniformly across the WHOLE corpus, but the real executor (`exec_streamed_select`)
starts and ends the walk at `walk_bounds`'s segment -- the slice the filter's own interval on the SORT
COLUMN admits, which the comment's own regrade table already showed matters (`unbounded` p90 6.43 vs
`sort-column bound` p90 5.31) without ever explaining why the bounded variant was never shipped, or
distinguishing it from the third, explicitly-rejected variant (a realized `inv_perm` span, correctly
declined for costing 0.51ns/matching card -- a real per-candidate hot-path cost this effort's
pre-computation constraint forbids).

**Why it was never shipped: not infeasible, just never circled back to.** Read `walk_bounds` and its
caller (`exec_streamed_select`, `lib.rs:10707`) and the acquire pipeline in full before assuming
either way. `walk_bounds` is already a cheap, existing function: two binary searches over the sort
permutation (O(log n_cards), nothing per candidate), early-returning the WHOLE permutation with a
single branch when the filter's bound is unbounded -- the common case, since most queries do not
filter on the same field they order by. Its input, `QueryParams::sort_bound`, is derived once per
query by `sort_col_bound` (a pure `FilterExpr` walk) at the PyO3 boundary (`bind_and_split_filter`,
`lib.rs:14360`) and attached via `with_sort_bound` BEFORE `run_query_routed` -- and therefore before
`acquire_plan_features` -- ever runs (confirmed at all three call sites: `run_query`, `explain`,
`explain_analyze`, `lib.rs:15009/15103/15152`). So the exact inputs `walk_bounds` needs
(`sort_col`, `descending`, `sort_bound`) were ALREADY sitting on `ctx`/`params` at acquire time, for
free, the whole time this effort has been running. The gap was purely that no `PlanFeatures` field
carried the segment length and no acquire branch ever called `walk_bounds` a second time to get it --
the loop-phase-measurement campaign that shipped the EXECUTOR-side bound (see
`docs/issues/done/local-engine-loop-phase-measurement.md`) used the regrade only to VALIDATE that
change, and the natural follow-up (teach the COST MODEL the same bound) was never picked up across 31
subsequent rounds. No correctness subtlety, no missing precomputed index, no rejected-and-forgotten
attempt -- just an open thread.

**Fix.** Added `cost::PlanFeatures::perm_walk_span: u32` (`cost.rs`) and a new `perm_walk_span(ctx,
params)` helper (`lib.rs`, right above `mk_plan_feats`) that calls the SAME `walk_bounds` the executor
calls, over the SAME `(sort_col, descending, sort_bound)` triple -- not a second path that could
silently disagree with what dispatch actually walks. Falls back to `n_cards` when this
`(sort_col, descending)` pair has no permutation at all (`StreamedSelect` is inapplicable there and
never reads the field, but `mk_plan_feats` sets it uniformly across all five acquire branches, since
the shared feats have to cost a competing `StreamedSelect` honestly regardless of which branch
produced them -- the same reasoning `scatter_printings`/`compose_paging` already follow). Wired into
`perm_steps`'s formula in place of `n_cards`: `(page_span * perm_walk_span / matches).min(perm_walk_span)`.
Exposed to Python via `acquire_facts_to_pydict` for grading. Self-check: the added work is one
`Option` lookup plus an early-return branch for the (dominant) unbounded case, and O(log n_cards) two
probes for the bounded case -- the same style of cheap acquire-time lookup `CardRangePopcount`'s own
range-index binary search already relies on a few branches up in the same function; no per-candidate
or per-printing cost, confirmed by the same-build canary below.

**Regression test** (`card_engine/src/tests.rs`,
`acquire_perm_walk_span_matches_the_sort_column_bound`): a small synthetic corpus (8,500 non-matching
cards sorting ahead of 1,500 matching ones under `cmc asc`, the same anti-correlated shape as the
existing dispatch-level `streamed_walk_bounds_itself_by_the_sort_column_predicate` test), asserting
`acquire_plan_features`'s returned `perm_walk_span` equals the matching segment (1,500) for both
directions under a `cmc>=5` bound, and equals the whole corpus (10,000) for the unbounded control
(ordered by `edhrec`, which the filter says nothing about). Verified to actually catch a revert:
temporarily hard-coding `perm_walk_span` back to `ctx.n_cards()` unconditionally fails the bounded
assertion with `left: 10000, right: 1500`; restoring the fix passes again.

**Held-out validation against CURRENT traffic**, not the stale comment (whose numbers predate this
whole 31-round effort). Sampled `uniform`-mode traffic through `explain_analyze` (isolated release
wheel, 180s, seed 0), keeping every `StreamedSelect` row whose realized `perm_steps` counter is
nonzero (the walking population the comment's table itself used), hash-of-query calibration/held-out
split -- nothing here is FIT, both formulas are fixed, so the split is a consistency check rather than
an overfitting guard:

```
14,217 walking StreamedSelect rows (calibration 6,657 / held-out 7,560)

                       p10     median   p90     mean |log ratio|
CALIBRATION  old (n_cards)        0.152   1.003   5.596        1.046
             new (perm_walk_span) 0.176   1.012   5.675        1.015
HELD-OUT     old (n_cards)        0.145   0.995   5.786        1.021
             new (perm_walk_span) 0.172   1.000   5.811        0.988
POOLED       old (n_cards)        0.148   0.999   5.688        1.033
             new (perm_walk_span) 0.173   1.000   5.764        1.001
```

A real, if modest, improvement that holds on BOTH halves independently (mean |log ratio| -- the
metric that treats over- and under-estimation symmetrically, which is what an argmin comparison
actually needs -- drops ~3% pooled, ~3% on calibration, ~3% on held-out). The raw percentile shape
barely moves at the tail on THIS traffic mix (p90 5.69 -> 5.76, essentially flat, not the 6.43 -> 5.31
the stale comment reported): the correlation this fix targets -- a filter that constrains the SAME
field the query orders by (`cmc>=6 order=cmc`) -- is a designed-cell phenomenon
(`scripts/bench_walk_span.py`'s own CLUSTERED-vs-BROAD framing), not a common shape under random
`uniform` sampling, so most walking rows in this population see `perm_walk_span == n_cards` (the
fallback) and are unaffected either way. The p10/mean-log movement is exactly the minority of rows
where the two formulas DO diverge, moving in the right direction.

**`StreamedSelect/candidates` cost-model-agreement, before/after** (`bench_cost_model_agreement.py`,
isolated release wheels, 180s, seed 0): **unchanged**, median 0.59 both builds (n=16,484 baseline,
n=15,440 fix -- different sampled counts from independent 180s windows, not a code-speed effect).
Split further by realized `perm_steps` within just this acquire branch (own script, same protocol,
150s):

```
                                    baseline              fix
walking (perm_steps > 0)      n=1,398  median=0.853  n=1,380  median=0.852
small-total (perm_steps == 0) n=11,227 median=0.587  n=11,104 median=0.587
```

Both sub-populations flat. The `candidates` acquire branch's own walking rows are only ~11% of its
`StreamedSelect` traffic here, and -- per the held-out result above -- most of those still see
`perm_walk_span == n_cards` under uniform sampling, so this specific pooled cell does not move
measurably even though the underlying mechanism is real (confirmed by the held-out check, which pools
across every acquire branch, not just `candidates`). Honest result: a real, validated fix with a
negligible visible effect on this specific cell under this traffic mix -- not the cell this round
closes.

**Regression guards**, isolated release wheels, `--mode realistic --seed 0`:

- `#852` (`bench_pairwise_ordering.py`, 180s): `GatheredScan vs PrintingCompose` overall 88% (n=18,431)
  -> 89% (n=20,781); `GatheredScan vs StreamedSelect` overall 97% -> 97%,
  `[candidates]` 99% -> 99%. Both within noise of independent-window sampling variance, no shift.
- Round 30/31's own territory (`bench_regret_matrix.py`, 150s): `StreamedSelect -> GatheredScan`
  n 1,060 (70% share, 20.62µs median regret, 84.2ms total) -> 1,047 (69% share, 20.83µs median,
  82.4ms total) -- flat, as expected: this round's term (`walks_permutation`) is a different branch
  from Rounds 30/31's (the small-total gather), and confirmed rather than assumed unaffected.
- Round 28's `scan_units` feature accuracy: not re-run this round -- this fix adds a wholly separate
  `PlanFeatures` field (`perm_walk_span`) consumed only by `StreamedSelect`'s `perm_steps` term, and
  touches neither `scan_units` nor `stream_scan_units`'s computation, so there is no code path by
  which it could move that cell.

**Correctness gates.** `cargo test --release` (`card_engine`): 179/179 passed (178 + this round's new
regression test). `cargo test` (debug): 180/180 passed. `cargo clippy --all-targets -- -D warnings`:
clean. Blast radius: `card_engine/src/cost.rs` (`PlanFeatures::perm_walk_span`, the `perm_steps`
formula), `card_engine/src/lib.rs` (the new `perm_walk_span` helper, wired into `mk_plan_feats`, plus
its `acquire_facts_to_pydict` exposure), `card_engine/src/tests.rs` (the six hand-built `PlanFeatures`
literals updated to compile, plus one new regression test), this doc. No other `cost.rs` rate
constants touched.

**Verdict.** Real, validated, narrow. The sort-column bound was never shipped to the cost model
because nobody had circled back to it, not because it was hard or unsafe -- every input it needs was
already free at acquire time, and the fix is a strict generalization of the existing formula (it
collapses to the old behavior whenever the filter says nothing about the sort column or no
permutation exists). Held out against current traffic, it measurably tightens the estimate on the
population it targets (mean |log ratio| improves ~3% on both calibration and held-out halves) without
moving `StreamedSelect/candidates`'s pooled cost-model-agreement cell, because that specific
correlation (filter bounds the same field the query orders by) is rare under random/uniform traffic --
a designed-cell phenomenon, not a common production shape. No regression on `#852`, on Rounds 30/31's
own territory, or on Round 28's `scan_units` cell (unreachable by this change). Shipped as a
strict-generalization correctness fix rather than for its measured routing impact, which is real but
small on this traffic mix.

## Confirmation runs

Round 1 (match-density depth proxy, kept):

- `bench_regret_matrix.py --seconds 120 --mode uniform`: no anomalous transition — regret concentrates
  where it already did (`printing_compose` 96% of share, `StreamedSelect -> GatheredScan` /
  `GatheredScan -> PrintingCompose` the largest picked/best mismatches), nothing resembling the
  historical 23.6x acquire-time blowup.
- `bench_query_latency_ab.py --mode realistic --sample 2000 --seed 1`, baseline vs modified: `+0.4us`
  mean, 95% CI `[+0.2, +0.6]`, "B is SLOWER". A same-build canary (baseline vs baseline, same
  protocol, nothing changed) produced `-0.3us`, CI `[-0.4, -0.2]`, "B is FASTER" — i.e. a swing of the
  same sign and magnitude with zero code difference, matching this script's own documented
  non-interleaved-run drift artifact. The real diff is not distinguishable from that noise floor, so
  read as no detectable latency regression, not confirmed-safe by a wide margin.

Round 9 (`GATHER_FIXED_COST_ZERO_MATCH_NS`, kept):

- `bench_regret_matrix.py --seconds 60 --seed 0`, baseline vs modified: total regret 27.6ms both
  builds (18,181 vs 18,349 multi-plan queries, wall-clock-budget variance); no new `picked -> best`
  mismatch row — including for the RANGE_ACQUIRES gate-precision risk this round found and checked
  directly (see Round 9 above).
- `bench_query_latency_ab.py --mode realistic --sample 800 --seed 1`, interleaved A1/B1/A2, baseline
  vs modified: `-0.3us` mean, 95% CI `[-0.5, -0.1]`, "B is FASTER". Same-build canary (A1 vs A2):
  `-0.2us`, CI `[-0.4, -0.1]`, also "B is FASTER" — a swing of the same sign and comparable magnitude
  with zero code difference. The real diff is not distinguishable from the canary's noise floor, so
  read as no detectable latency effect either way — expected, since this is a routing-accuracy fix
  for a rare zero-match slice, not a hot-path rate change.
- `bench_cost_model_agreement.py --seconds 300 --seed 0`, baseline vs modified: `GatheredScan/candidates`
  n=38,435→38,889, median 0.57→0.77, p10 0.25→0.47, p90 0.89→1.98, within-25% 11%→30% (still FAIL by
  the median bar, 0.77 < 0.8, but the largest single-round movement of this cell since Round 0).
  `GatheredScan/printing_compose`, `/printing_range_scan`, `/card_range_popcount` all unchanged within
  noise (see Round 9 above for the exact before/after). 12/17 acquire-branch cells inside [0.8, 1.25]
  both builds; by-unique table improves 9/12 → 10/12 (`GatheredScan/card` flips FAIL 0.69 → PASS 0.80).

Round 28 (`Mode::Card`-scope both broad-guard scan-units scales, kept): see the full "Round 28"
narrative above for the bisection, mechanism, before/after numbers (`main` 1.00, unfixed tip 0.70,
fixed 0.94), and confirmation-pass results (regret matrix, cost-model agreement, latency A/B with
canary, pairwise ordering) — all inline there rather than duplicated here.
