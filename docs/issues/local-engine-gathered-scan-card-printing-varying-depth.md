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

## Iteration ledger

| # | Idea | Outcome | GS/card within-25% | Other cells | Notes |
|---|------|---------|--------------------|-------------|-------|
| 0 | (baseline, `engine-cost-model-cleanup` @ `97dc30c8`) | — | 16% | — | n=35,074, median 0.67, p10 0.25, p90 2.75 |
| 1 | match-density depth proxy | kept | 16% → 17% (noisy, uncontrolled) | none, within run-to-run noise | paired-diff (controlled): 946 impr / 544 regr, 29.6M → 9.86M abs `scan_units` error; `BIAS` refit 2.1 → 0.7 |
| 2 | independence-product `domain_cards` for 2+ different-index range leaves | rejected at self-check | n/a (no code shipped) | n/a | printing-space variant: 38 impr / 496 regr, 17.3M → 18.1M abs error (worse); card-space variant: 0/1500 changed (mathematically incapable of firing) — see Round 2 below |

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
