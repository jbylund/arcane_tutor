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

## Iteration ledger

| # | Idea | Outcome | GS/card within-25% | Other cells | Notes |
|---|------|---------|--------------------|-------------|-------|
| 0 | (baseline, `engine-cost-model-cleanup` @ `97dc30c8`) | — | 16% | — | n=35,074, median 0.67, p10 0.25, p90 2.75 |
| 1 | match-density depth proxy | kept | 16% → 17% (noisy, uncontrolled) | none, within run-to-run noise | paired-diff (controlled): 946 impr / 544 regr, 29.6M → 9.86M abs `scan_units` error; `BIAS` refit 2.1 → 0.7 |
| 2 | independence-product `domain_cards` for 2+ different-index range leaves | rejected at self-check | n/a (no code shipped) | n/a | printing-space variant: 38 impr / 496 regr, 17.3M → 18.1M abs error (worse); card-space variant: 0/1500 changed (mathematically incapable of firing) — see Round 2 below |
| 3 | second clustering-bias constant (`COMPOSE_RANGE_AND_CLUSTER_BIAS`) for the same shape | kept | n/a (see Round 3 below — noisy at this cell's grain) | none, within run-to-run noise | held-out paired-diff (controlled): 433 impr / 117 regr, 8.60M → 8.02M abs `scan_units` error; new bias 1.1 against `COMPOSE_CARD_ESTIMATE_BIAS`'s 1.78 |
| 4 | downward `scan_units` scale (`COMPOSE_RANGE_AND_BROAD_SCAN_SCALE`) for the `range_too_broad_to_narrow`-fired subset of the same shape | kept | n/a (see Round 4 below — noisy at this cell's grain) | none, within run-to-run noise | held-out paired-diff (controlled): 166 impr / 15 regr / 0 tied, 5.40M → 1.90M abs `scan_units` error; new scale 0.7; `eval_domain` left untouched (measured exact, 0 error) |

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
