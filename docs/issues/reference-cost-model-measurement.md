# Measuring the cost model: which tool answers which question

Eight harnesses, built while doing the work in
[local-engine-cost-model-agreement.md](local-engine-cost-model-agreement.md). They exist because each
answers a question the others structurally cannot, and reaching for the wrong one wastes days — three
successive reworkings of one term improved the metric being watched and moved routing by
`-0.003 µs, CI [-0.206, +0.214]`.

This is the tool-picking reference. For *when in a PR's life* to reach for each one, see
[the performance PR workflow](../workflows/performance-pr-workflow.md), which organizes the same
tools into four layers: features, plan costs, plan execution, end-user latency.

Pick by question:

| question | tool |
| --- | --- |
| Do the FEATURES match what the executor did? | [`bench_feature_accuracy.py`](../../scripts/bench_feature_accuracy.py) |
| Is this plan's absolute cost right? | [`bench_cost_model_agreement.py`](../../scripts/bench_cost_model_agreement.py) |
| What SHAPE is the error — uniform, or a tail? | [`bench_cost_error_percentiles.py`](../../scripts/bench_cost_error_percentiles.py) |
| Is it the features, the coefficients, or the arm's shape? | [`bench_cost_error_attribution.py`](../../scripts/bench_cost_error_attribution.py) |
| What should the constants be? | [`fit_cost_model.py`](../../scripts/fit_cost_model.py) |
| Does the model ORDER two plans correctly? | [`bench_pairwise_ordering.py`](../../scripts/bench_pairwise_ordering.py) |
| Where does routing actually lose time? | [`bench_regret_matrix.py`](../../scripts/bench_regret_matrix.py) |
| Did this PLAN's executor get faster, picked or not? | [`bench_plan_execution_ab.py`](../../scripts/bench_plan_execution_ab.py) `--compare` |
| Did a change help end to end? | [`bench_query_latency_ab.py`](../../scripts/bench_query_latency_ab.py) vs `main` |

All of them are built on [`scripts/costbench.py`](../../scripts/costbench.py), which owns the
sampling loop, the nearest-rank percentiles, the paired-bootstrap comparison, and — the one that
matters for comparing numbers ACROSS these tools — the single definition of a plan's own cost,
`plan_self_ns`: min-of-trials, less `ns_prepare`, except under a range acquire, dropping the row when
the subtraction overshoots. Three harnesses used to disagree about that rule, one of them netting
`acquire_ns` (a different participant entirely), so their columns were never comparable.

`costbench.predicted_ns` is the other shared screen: `cost::plan_cost` returns `f64::INFINITY` for a
declining compose, which no `predicted_ns <= 0` guard catches, and any ratio built from it silently
poisons the percentile cell it lands in.

The end-to-end answer for the whole cost-model stack is recorded in
[local-engine-cost-model-stack-result.md](local-engine-cost-model-stack-result.md) — including the
bands, because a mean over this distribution understates the tail and a p99 overstates it.

Most of them draw queries from one universe, [`query_sampler.py`](../../scripts/query_sampler.py), in
one of two weightings. Diagnostics default to `uniform` because their job is to FIND errors and
uniform reaches the rare tails; latency defaults to `realistic` because there the question is what
users wait for. Both matter: artwork is 5% of realistic traffic and carries **50% of all routing
regret**.

Constrain the draw with `Shape` rather than writing a generator — `Shape(families={"range"},
predicates=1, unique={"printing"})` gives a bare printing-space range while keeping corpus-derived
values and quantile-placed thresholds. Two harnesses still hand-roll a range generator off hardcoded
values (`bench_card_range_estimate`, `bench_cost_model_agreement`) and should move; see
[local-benchmark-toolkit-audit.md](local-benchmark-toolkit-audit.md). Two draw from real traffic on
purpose: `bench_plan_misselection --source wild-operators` and `census_candidate_materialize`, where
the question is what users actually lose.

## The two matrices, and why both

They disagree, and the disagreement is the point. An estimate can be off 100x on a plan that never
wins anyway, and right to 5% where the margin decides every query.

### The cost matrix — estimate/real at p1/p10/p20/p50/p70/p90/p99

Sliced by plan, by `plan / distinct-on`, by `plan [acquire]`, and by all three. **Read the shape, not
p50:**

- **tight spread** (p90/p10 ≈ 1) → a plain rate error, recalibrate. `CardRangePopcount` was a uniform
  1.20 with spread 1.4; one constant fixed it to 1.00.
- **wide spread** → the error depends on something unmodelled; a constant will not help.
- **healthy middle, bad tail** → a specific query class, not a general defect. `printing_range_scan`
  has the worst mean in the engine (0.98) and one of the better medians (0.19).

**Always read mean AND median.** They diverge sharply, and the mean invites being read as "typical"
when it is a tail.

**Slice by distinct-on.** Errors CANCEL when pooled, and the cancellation hid the largest routing
defect in the engine: compose reads 0.81 on artwork and 1.15 on printing, pooling to "roughly fine",
while those two drove OPPOSITE routing errors.

### The regret matrix — where being wrong costs time

Regret is `routed_ns - best_measured_ns`, so a correct pick contributes 0. Measured against
`routed_ns`, not the picked plan's own trial, because those differ exactly when the picked plan
DECLINES and dispatch re-chooses among materializing plans only.

Regret is mostly zeros, so **read SHARE** — the fraction of all lost time a slice accounts for, which
is frequency times severity and is what ranks work. It found compose carrying **75%** of all lost
time, and one transition (`StreamedSelect -> PrintingCompose`) losing a mean of 190 µs over 150
queries.

**Split compose-like regret by DIRECTION before acting.** "75% of lost time" is compatible with two
opposite fixes, and here it was both at once: over-picked 38:1 in artwork, under-picked 10:1 in
printing. A change that made compose uniformly cheaper was therefore right for one mode and wrong for
the other, and lost overall.

## Traps, each one paid for

- **Never compare two headline means.** Regret and latency are heavy-tailed. Write per-query rows with
  `--out` and use `--compare`, which pairs over the same queries and gives a bootstrap CI. At
  `--sample 400` the same engine and seed produced 0.26 and 0.82 µs on two runs.
- **Interleave A/B/A/B.** All-of-A-then-all-of-B maps machine drift onto the comparison. A sequential
  run showed a ~3% median slowdown spread evenly across acquire branches the change never touched.
- **A cross-process A/B needs far more trials than a within-call comparison, and breadth is not a
  substitute.** `min` over the trials is a floor estimator whose distance above the floor depends on
  the interference that run saw, and that error is common-mode across every query in a run — so
  pairing and a wide sample do not cancel it. Both A/B harnesses false-positived on same-build,
  same-seed pairs at 7 trials: the plan-execution one called every plan "SLOWER" by 4–9% with
  "faster on 0"; the latency one called `-1.0 µs, CI [-1.6, -0.5]` over 388 paired queries. Both are
  clean at 30. Prefix-min convergence, absolute, is much gentler — 0.4–1.4% above the floor at k=7,
  converged by k=30 — which is why the within-call diagnostics keep the cheaper default. The table
  is in [`costbench.py`](../../scripts/costbench.py).
- **Run the canary.** Compare a build against ITSELF before believing any cross-build number.
  `bench_plan_execution_ab.py` also keeps acquire as a CONTROL and prints an adjusted column when it
  moves; if that fires, raise `--trials` before reading anything else.
- **A grid optimum on the boundary is not an optimum.** Two sweeps had to be redone for this. Use
  geometric grids open at both ends, and check the optimum is interior.
- **Fixing a feature can make agreement worse**, because coefficients were compensating. Expect it,
  and refit in the same change.
- **Verify the fitter mirrors the engine.** `fit_cost_model.py` reimplements `cost.rs` in Python and
  had silently drifted for two revisions; it now checks itself against `predicted_ns` and refuses to
  fit below 99% agreement.
- **A plan that DECLINES accumulates no trials**, so it is absent from every measurement here. Enabling
  a declining path introduces a population nothing has ever measured. The decline is not free —
  `DeclineSparseExact` fires after a full compose — and its cost is in `declined_ns`, which
  `bench_cost_model_agreement.py` reports in its own section rather than in the measured/predicted
  table.
- **Agreement is not sufficient.** One fix improved routing significantly (`-0.166 µs`,
  CI `[-0.341, -0.007]`) while making median agreement WORSE — a per-cell median cannot see a 56x error
  on 4% of rows.

## A working order

1. **Regret matrix** — what is worth fixing at all, by SHARE, and in which direction.
2. **Cost matrix** — the shape of that cell's error, sliced by distinct-on.
3. **Attribution** — features, coefficients, or the arm's shape. Do not skip: a miscounted feature
   cannot be repaired by any rate, and a fit will bury it in whichever term correlates.
4. **Fix**, features before rates.
5. **Paired A/B** — the only thing that says whether it worked.
