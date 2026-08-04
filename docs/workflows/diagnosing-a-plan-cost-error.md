# Diagnosing a Plan Cost Error

How to find out *why* one physical plan's cost arm is wrong, as opposed to *that* it is wrong.
[reference-cost-model-measurement.md](../issues/reference-cost-model-measurement.md) says which tool answers
which question; [performance-pr-workflow.md](./performance-pr-workflow.md) is the process for shipping the
fix. This is the diagnosis in between.

Every step below exists because skipping it produced a wrong answer at least once. The worked example
throughout is `StreamedSelect` (P3) against `GatheredScan` (P4) and `PrintingCompose` on legality queries,
from `local-engine-loop-phase-measurement.md`.

## Where you are trying to end up

**Both scan arms predicting their own real time, and the ordering correct.** Those are two goals, they move
independently, and only one of them is currently met.

Ordering-correct-by-cancellation is a real trap and the engine has been in it: `cost.rs`'s own header
records that P3/P4 rates "are demonstrably wrong while the PRODUCTS they form are roughly right — which is
the signature of compensating error", and that a rate 2× high against an estimate 2× low routes correctly
and breaks the moment you fix either alone. That is a local optimum you cannot build on, because the next
change has no ground truth to check itself against.

So track absolute agreement per arm *and* regret, always. Absolute agreement
(`bench_cost_error_percentiles.py`, estimate/real, uniform mode, 79,718 plan-rows):

| plan | p10 | p50 | p90 | spread |
| --- | --: | --: | --: | --: |
| CardRangePopcount | 0.84 | 1.05 | 1.30 | **1.6** |
| StreamedSelect | 0.73 | 1.09 | 1.74 | 2.4 |
| GatheredScan | 0.68 | **1.28** | 2.75 | 4.0 |
| PrintingCompose | 0.66 | 1.07 | 3.08 | 4.7 |

P4 is still ~1.3× over at the median and compose's spread is the worst in the engine. Neither is finished.

**And that these two goals move independently is not a theoretical caveat.** The two fixes described
throughout this doc took total regret down **25%** and moved this table by almost nothing — P3's spread
2.6 → 2.4, P4's unchanged. Both fixes are confined to the `printing_compose` acquire, which is 4–10% of
plan-rows, so a pooled per-arm view cannot see them at all. If you are optimising routing, this table is
not your scoreboard; if you are trying to make an arm predict its own time, regret is not yours.

One reading to attribute rather than trust: sliced to the cell that was worked,
`StreamedSelect [printing_compose] / printing` now shows p50 1.04 with a **26.4 spread** (p90 13.90), and
`/ card` 24.6. A long over-costing tail on the acquire whose median is fine. No before/after was captured for
that cell, so whether the tier gate created it or merely revealed it is open — and it is the obvious place to
point step 11 next.

## The recipe

**1. Pick the cell from regret, not from accuracy.** They disagree, sharply. P3's absolute error reaches p99
47× and it barely appears as a regret source; the `plane` acquire has p50 errors of 1.2–1.3 and a **1% miss
rate**. An argmin sees only *differences*, so a term wrong for every plan cancels. `bench_regret_matrix.py`
sliced by acquire × unique is what ranks the work — and **re-run it after every landed fix**, because the
ranking moves under you. `printing_compose / artwork` was 32% of all lost time when the worked example below
started and is **6%** now; `candidates / artwork` is 32%.

Read the SHARE column knowing it is frequency × severity. `candidates` currently holds 78% of lost time at a
mean of 1.69 µs against `printing_compose`'s 21% at 1.60 — i.e. the leading acquire is not the worse one per
query, it is the one with 3.5× the queries. And **the loss is all tail**: p90 is 0.00 for every acquire, so
the median query has no regret at all and only p99 (36–64 µs) is worth attacking. A mean is the wrong summary
to optimise against here.

**2. On one query in that cell, get predicted AND measured for every plan.** `explain_analyze` runs each plan
for real, so it is one call, and it says which side of the comparison is broken:

    f:gladiator / artwork    predicted   measured   pred/meas
    PrintingCompose             178.7      182.2      0.98    <- accurate
    StreamedSelect              704.0      102.5      6.87    <- the problem

We had assumed compose was over-picked because compose was under-costed. It was not; P3 was over-costed 7×.

**3. Decompose the bad prediction into its terms by hand.** `scan_units × STREAM_SCAN_PER_ROW_NS` is
88,026 × 5.97 = **525 µs of a 704 µs prediction**, against a 91 µs measured loop. Now one term is suspect
rather than a whole arm.

`explain` flattens the whole feature vector into its `acquire` dict, so a throwaway Python replica of the one
arm is a few lines. **Assert the replica reproduces `predicted_ns` exactly before you attribute anything
to it** — a term breakdown from a replica that has silently drifted is worse than no breakdown, and this is
the same failure `fit_cost_model`'s mirror check exists to catch (it went two revisions reporting
coefficients from a drifted mirror).

**4. Grade the suspect feature against its realized counter — for BOTH plans, on the same query.** This is
the step that does the work:

    scan_units charged (shared)   88,026        88,026
    printings_examined realized   54,213 (P4)    5,876 (P3)
    ratio                           1.62          14.98

One per-query number cannot be right for both. This is what separates a **feature** error from a **rate**
error, a distinction no refit can make — and the reason six refits of this surface failed before anyone
looked. It only works because the executors publish realized counters into `PhaseStats`; if the counter you
need does not exist, adding it is the first task, not an aside.

**5. Ask whether the router and the executor are asking the same question.** A feature this wrong is rarely
bad arithmetic; it is usually a condition the executor evaluates and the router does not. `prepare_candidates`
held the only copy of `all_match_known`, so the compose acquire — which never calls it — charged
`verify_cost_tier` on every legality query alike, and the two dead terms that bought were **92–94% of P3's
predicted cost**. The fix was to extract the predicate (`plane_leaves_nothing_to_verify`) and call it from
both layers, not to adjust a number.

Two checks worth making routinely, because both failure modes are silent:

- **Every acquire branch that builds `PlanFeatures` sets every gated field.** `mk_plan_feats` supplies
  defaults, so a branch nobody taught is quietly wrong rather than a compile error.
- **A condition both layers need is one function, not two copies.**
  `compose_paging_prediction_matches_the_branch_taken` is the existing pattern for pinning that agreement, and
  its doc says why: the same drift already happened once between `cost.rs` and its Python mirror.

**6. Sweep for scope before fixing anything.** Ten predicates × three modes. Legality read 0.10–0.26;
`border:black`, `r:mythic` and `watermark:*` read exactly 1.00. A blanket "compose's `scan_units` is wrong"
fix would have corrected four queries and broken the rest.

**7. Name the mechanism, or do not believe the finding.** P3's `card_match_count` answers from span
arithmetic whenever `card_pass` resolves a card outright; P4's `push_card_matches` must walk the span to push
every match. Legality is the one printing-varying attribute that resolves at card level, for non-divergent
cards. Without a mechanism you have found a coincidence.

**8. Check whether the win is routed or latent.** Sweep `unique`. In card mode those same queries route to
`PlanePopcountOrder` (2.46 µs against P3's 12.38), so a card-mode-only measurement optimises a plan nobody
runs. Print the routed plan per cell so a latent win reads as one.

**9. Verify the fix moved the DECISION, not just the number.** Correcting the feature took the predicted
pick/best ratio from 0.22 to 0.63–0.69 and flipped nothing: 16 mispicks survived. "The estimate got better"
and "the router now picks correctly" are separate measurements, and so is "the arm now predicts its own
time" — see the table at the top.

**10. If total regret ROSE, find out whether you removed a compensating error before reverting.** A correct
fix can make routing worse, and the intermediate number will tell you to throw it away. Run
`bench_pairwise_ordering.py` on the pair you targeted *and* on the others:

| pair, `[printing_compose]` | before | after the tier gate |
| --- | --: | --: |
| `PrintingCompose vs StreamedSelect` (targeted) | 80% ordered right | **96%** |
| `GatheredScan vs StreamedSelect` (untouched) | 69%, gap 0.89 | 69%, gap 0.90 |

The targeted pair improved and no other pair moved, yet total regret went **81.7 → 129.7 ms**. That pattern
means one thing: the fix let a correctly-cheap plan into the argmin far more often, and a *different* pair
cannot rank it. Nothing regressed — an existing error stopped being masked. Fixing what it exposed (P4's span
feature, 1.76× over) then took regret to **61.2 ms, −25% on baseline**. Reverting on the 129.7 ms would have
discarded a correct change and left the real defect hidden, which is the trap this step exists for.

Two things make the reading trustworthy. **Regret TOTAL is a sum over however many queries the budget
reached**, so it is not comparable across runs — compare the mean, and measure your own baseline in the same
session rather than against a number in a doc. The 48.7 ms and 55.0 ms figures still quoted in the earlier
sections of `local-engine-loop-phase-measurement.md` were taken at unrecorded settings and are **not** on the
same scale as the 81.7 → 61.2 ms above; that is why a fresh paired baseline was measured rather than compared
against them. And a cost-only change
needs **no new row-identity run**: `force_plan_differential_agreement` already proves every plan returns the
same rows, which is exactly what makes re-routing safe.

**11. If the fix does not move the ordering, split the population before touching a rate.** A pair can be
right on average and bimodal underneath, and the summary statistic that hides this is the one that looks most
reassuring. `GatheredScan vs StreamedSelect [printing_compose]` reads a **gap ratio of 0.91** — the model
sizes the difference almost exactly right — at **69% ordered right**. That invites "it is variance, no
constant will fix it". Splitting the 5,085 non-tie pairs on the regime flag says otherwise:

    right, tier 0 (card-invariant)   409    P3 wins 100% measured, 100% predicted
    right, tier > 0 (real residual) 3,100   P3 wins   5% measured,   5% predicted
    wrong, tier > 0                 1,576   P3 wins  98% measured,   2% predicted

Every wrong pair is in one regime, and the wrong group is **homogeneous** — the model is not scattered around
the truth, it is confidently inverted on one identifiable class (broad residuals where both plans scan the
whole corpus). A mean gap ratio of 0.91 is what two opposed sub-populations look like after averaging.

## Four traps that cost the most time

**A corpus-wide share applied to a selected population is wrong exactly where it matters.** The first attempt
at the fix above priced "how much of the residual is really printing-dependent" as the divergent fraction of
the corpus — `legal_divergent / n_cards`, ~1.76%. It is the natural move and it is invalid whenever the filter
*correlates with the thing being averaged*: for `f:oldschool` the candidates largely **are** the divergent
cards, so a global share under-charges precisely the queries whose residual is real. Measured, it traded 408
mispicks for 118 that were four times worse (mean 54.27 → 206.24 µs) for a net 3.4%. The replacement was a
**boolean the engine already derives from data** (`plane_expr_is_existential` against `divergent_formats`), not
a better fraction. Prefer an exact predicate the engine can answer over any estimated share, and if you must
estimate one, condition it on the candidate set rather than the corpus.

**Cross-build timing cannot resolve a small effect, and will hand you the wrong sign.** On identical cells
`ns_loop` wandered 43.00 / 45.38 / 47.17 µs across runs of builds that never touched that phase — ±9%, both
directions. `bench_plan_execution_ab.py` called one change 2% *slower* while its own acquire control, which
the change cannot touch, moved 1.9% the same way. Use **one binary with a runtime toggle**, interleaved
subprocesses, and **equal-length env values** — an env var present in one arm only shifts process memory
layout enough to move sub-100 µs queries a consistent ~15%. `scripts/bench_walk_span.py` is the pattern.

**Kernel measurements are warm-cache; levels must come from traffic.** The residual floor measured 2.45
ns/card on a built design while traffic fit the same column at 8.19 against a shipped 9.05 — and that is the
third time this has been caught. **Shape from a built design, levels from traffic.** A design tells you which
terms exist, which are degenerate and how two plans differ; it does not tell you what a constant should
equal.

**A pooled fit endorses the rates that are wrong on a sub-population, so it cannot be your check.** On the
class in step 11, `STREAM_SCAN_PER_ROW_NS` 5.97 and `GATHER_SCAN_PER_ROW_NS` 2.06 are 2.9× apart for what is
nominally the same walk-a-printing-and-test work, and P3's higher rate cancels its lower per-card rate exactly
where both features are maximal. `fit_cost_model.py` fits **both** and pronounces them fine — 6.04 at ratio
1.01 and 2.53 at 1.23. So a refit will not surface this and cannot fix it; it will confirm the status quo. Fit
the split populations separately, or the fitter launders the error into an endorsement. Corollary for
`--counters`: it grades a feature against a counter *pooled the same way*, so a feature that is right at the
median and inverted on a quarter of rows passes.
