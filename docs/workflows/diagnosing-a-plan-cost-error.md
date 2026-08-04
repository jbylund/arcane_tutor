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

So track absolute agreement per arm *and* regret, always. Absolute agreement today
(`bench_cost_error_percentiles.py`, estimate/real, uniform mode):

| plan | p10 | p50 | p90 | spread |
| --- | --: | --: | --: | --: |
| CardRangePopcount | 0.84 | 1.04 | 1.29 | **1.5** |
| StreamedSelect | 0.74 | 1.09 | 1.90 | 2.6 |
| GatheredScan | 0.68 | **1.28** | 2.67 | 3.9 |
| PrintingCompose | 0.62 | 1.06 | 2.99 | 4.8 |

P4 is still ~1.3× over at the median and compose's spread is the worst in the engine. Neither is finished.

## The recipe

**1. Pick the cell from regret, not from accuracy.** They disagree, sharply. P3's absolute error reaches p99
47× and it barely appears as a regret source; the `plane` acquire has p50 errors of 1.2–1.3 and a **1% miss
rate**. An argmin sees only *differences*, so a term wrong for every plan cancels. `bench_regret_matrix.py`
sliced by acquire × unique is what ranks the work — `printing_compose / artwork` at 32% of all lost time.

**2. On one query in that cell, get predicted AND measured for every plan.** `explain_analyze` runs each plan
for real, so it is one call, and it says which side of the comparison is broken:

    f:gladiator / artwork    predicted   measured   pred/meas
    PrintingCompose             178.7      182.2      0.98    <- accurate
    StreamedSelect              704.0      102.5      6.87    <- the problem

We had assumed compose was over-picked because compose was under-costed. It was not; P3 was over-costed 7×.

**3. Decompose the bad prediction into its terms by hand.** `scan_units × STREAM_SCAN_PER_ROW_NS` is
88,026 × 5.97 = **525 µs of a 704 µs prediction**, against a 91 µs measured loop. Now one term is suspect
rather than a whole arm.

**4. Grade the suspect feature against its realized counter — for BOTH plans, on the same query.** This is
the step that does the work:

    scan_units charged (shared)   88,026        88,026
    printings_examined realized   54,213 (P4)    5,876 (P3)
    ratio                           1.62          14.98

One per-query number cannot be right for both. This is what separates a **feature** error from a **rate**
error, a distinction no refit can make — and the reason six refits of this surface failed before anyone
looked. It only works because the executors publish realized counters into `PhaseStats`; if the counter you
need does not exist, adding it is the first task, not an aside.

**5. Sweep for scope before fixing anything.** Ten predicates × three modes. Legality read 0.10–0.26;
`border:black`, `r:mythic` and `watermark:*` read exactly 1.00. A blanket "compose's `scan_units` is wrong"
fix would have corrected four queries and broken the rest.

**6. Name the mechanism, or do not believe the finding.** P3's `card_match_count` answers from span
arithmetic whenever `card_pass` resolves a card outright; P4's `push_card_matches` must walk the span to push
every match. Legality is the one printing-varying attribute that resolves at card level, for non-divergent
cards. Without a mechanism you have found a coincidence.

**7. Check whether the win is routed or latent.** Sweep `unique`. In card mode those same queries route to
`PlanePopcountOrder` (2.46 µs against P3's 12.38), so a card-mode-only measurement optimises a plan nobody
runs. Print the routed plan per cell so a latent win reads as one.

**8. Verify the fix moved the DECISION, not just the number.** Correcting the feature took the predicted
pick/best ratio from 0.22 to 0.63–0.69 and flipped nothing: 16 mispicks survived. "The estimate got better"
and "the router now picks correctly" are separate measurements, and so is "the arm now predicts its own
time" — see the table at the top.

## Two traps that cost the most time

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
