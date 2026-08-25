# Use a sigma safety margin to choose between Perm's walk and the three-phase fallback

`walk_grouped_page`'s cost for `Perm` paging depends on how deep into the sort permutation it has to
walk before a page fills, and how much EDHREC-order clumping inflates that depth beyond what match
density alone would predict — a quantity `reference-engine-compose-perm-cards-visited-estimator.md`
found genuinely hard to estimate well. The three-phase popcount-skip walk
(`local-engine-compose-perm-popcount-skip-prototype.md`) sidesteps that by being insensitive to
clumping entirely, at a real but bounded cost. This doc is about the decision between the two: which
one to run, for a given query, without knowing in advance how bad the clumping will be.

**Status: a strong candidate found and measured, not yet built.** This doc exists so the finding and
the remaining work aren't only living in one session's context — see
[branch and pushed state](#branch) below.

## The finding

Built two families of decision rule on top of two exact, closed-form anchors —
`worst_case_bound` (every non-match clumped before the k-th match, an unconditional ceiling) and
`uniform_mean`/`nhg_variance` (the no-clumping expectation and its variance, closed-form negative
hypergeometric) — and measured them against 2,705 real `Mode::Card` `Perm` queries
(`QuerySampler("uniform")`, single predicate, offsets swept 0-25,000, `scripts/bench_compose_card_
visited_safety_bound.py`):

- **`sigma(knob)`** = `uniform_mean + knob * sqrt(nhg_variance)` — a statistical margin over the
  no-clumping model.
- **`blend(knob)`** = linear interpolation between `uniform_mean` and `worst_case_bound`.

Graded by real latency (using the now-real `walk_ns`, not a model — see the cost-model doc below for
why that took a real instrumentation fix), pooled across the offset sweep:

```
policy                %diverted    p50     p90      p99      max
always_walk (today)      0%      24.4us  252.4us  500.2us  635.8us
always_three_phase      100%      93.9us  152.3us  164.6us  164.7us
no gate, worst_case      77%      62.8us  124.1us  159.5us  201.2us
no gate, blend(0.6)      69%      50.8us  120.6us  159.6us  221.0us
no gate, sigma(2-4)      36%      19.2us  118.1us  160.8us  236.3us
abort at 2x 3phase       21%      21.1us  229.8us  459.1us  460.3us
```

`sigma` (knob 2.0-4.0 — the knob barely matters in this range) diverts about half as many queries as
`worst_case`/`blend` while matching or beating them through p99, and its p50 is the best of any
protective policy. It tracks `oracle` (the best any policy could do with hindsight) almost exactly
through p99, diverging only at the single worst row in the sample — and even there, the losing case
is a mundane deep-offset/high-selectivity query, not a filter/sort-correlation pathology. Full
derivation, the two closed-form anchors, and why a pure statistical margin was expected to fail but
didn't dominate the *latency* tail here: see the differential dead ends below.

**Real residual risk, stated plainly**: `sigma`'s margin comes from a model that assumes matches are
placed independently of sort position. Filter/sort-correlated outliers (`f:tlr` sorted by `cmc`,
`frame:extendedart` sorted by `cubecobra`) are real, measured, and violate that assumption by
construction — they just happen to have modest absolute cost in this corpus, so they don't show up
as the latency tail's dominant driver *yet*. That's a property of this corpus's size and this
sample's query mix, not a proof the risk is gone. Re-check this if the corpus grows a lot, or against
real production traffic's actual query shapes rather than a uniform sampler.

## Dead ends worth not re-deriving

- **A pure worst-case bound** is exact and safe but overcosts the walk by 100-800x at shallow
  offsets, which would reject `Perm` almost everywhere — not because clumping is that bad, but
  because the bound has to price in a risk that empirically never manifests there.
- **Gating by offset** (skip the check below some threshold, always walk) looked promising until the
  clumping outliers turned out to sit at shallow offsets too (`frame:extendedart` at offset 0) — a
  gate protects exactly the wrong population.
- **A runtime abort budget** (let the walk run, bail into three-phase past a cost threshold) has no
  a-priori tax on the fast majority by construction, but needs its budget anchored to the
  alternative's own cost (not `k` — a `k`-scaled budget can never trip at deep offsets) and an
  absolute ceiling on top (an unbounded multiplier can make the capped outcome worse than not
  aborting at all). Even fixed, it controls p90/p99 worse than `sigma` does.

## What shipping this looks like, in independently-landable chunks

1. **Fix the cost model first, separately**: [local-engine-compose-walk-cost-model-miscalibrated.md](local-engine-compose-walk-cost-model-miscalibrated.md).
   Unrelated in scope to this doc's own decision rule — found as a side effect, ships on its own.
2. **Promote the three-phase walk out of `#[cfg(test)]`**, Card mode first. Zero behavior change:
   nothing calls it yet. Correctness is already covered (360-case differential test against
   `walk_grouped_page`, passing).
3. **Port `sigma_bound`/`uniform_mean`/`nhg_variance` into Rust.** Direct translation of closed-form
   math already Monte-Carlo-verified in the Python harness. Also zero behavior change on its own.
4. **Get real, same-process rates for both sides** before trusting a live comparison — the Python
   analysis's three-phase cost was itself a kernel-bench model, the same category of mistake the
   walk side had to get fixed (see the cost-model doc). Likely means running the promoted walk in a
   shadow mode (compute both, keep the real one's result, log what the other would have cost) rather
   than trusting either model blind.
5. **Wire the decision as a local branch inside `printing_compose_fastpath`'s `Perm` arm** — not a
   new top-level `PhysicalPlan`. `matches`/`n_cards` are already computed there for free before
   `Perm` is chosen; this mirrors the existing `walk_col = walk_possible && orderby_walk_beats_
   gather(...)` pattern already in that function.
5a. **Keep the acquire-side predictor honest.** `compose_paging_with_total` predicts `Perm`
   unconditionally today; if `Perm` sometimes silently substitutes three-phase internally, that
   prediction (and the `PagingTaken` label) needs to know. `compose_paging_prediction_matches_the_
   branch_taken` already exists to guard exactly this kind of drift and will need extending, not
   left to catch it after the fact.
6. **Pick the knob as a real, env-overridable constant** (`guard_env`-style, matching
   `STREAM_MIN_MATCHES`'s convention) — 2.0-4.0 per the measurement above.
7. **Validate on real traffic before flipping it live** — a paired same-build A/B
   (`bench_query_latency_ab.py`/`bench_plan_execution_ab.py`'s existing methodology). This offline
   analysis alone surfaced two real confounds (the compose/walk timing bundle, an acquire-vs-exact-M
   mismatch) before it settled — a shadow run on real traffic is worth more than trusting the model
   further.

Steps 2, 3, and 6 are mechanical and low-risk, and don't depend on the others — good candidates to
land first and separately. Step 4 is the one that actually decides whether 5 is safe to build.

## Branch and pushed state

This work (the popcount-skip prototypes, the `ComposePageWork` timing split, the safety-bound
analysis harness, and this doc) lives on `engine-compose-cards-visited-estimator`, pushed to origin.
Nothing in this doc is wired into the fastpath — the branch is prototypes, measurement code, and
write-ups only, safe to build on incrementally per the chunks above.

## Related

- [reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)
  — why estimating the walk's own depth turned out to be the harder path than this doc's approach.
- [local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md)
  — the three-phase walk this decides against, and its own crossover measurements.
- [reference-engine-compose-popcount-skip-topk-select.md](reference-engine-compose-popcount-skip-topk-select.md)
  — the one-phase-selector detour this decision-rule work grew out of; measured and rejected.
- [00730-engine-popcount-skip-walk.md](00730-engine-popcount-skip-walk.md) — the filed issue this
  whole arc implements.
- [local-engine-compose-walk-cost-model-miscalibrated.md](local-engine-compose-walk-cost-model-miscalibrated.md)
  — the independent cost-model finding this work surfaced.
- `scripts/bench_compose_card_visited_safety_bound.py` — the harness all the numbers in this doc
  come from.
