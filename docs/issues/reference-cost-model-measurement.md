# Measuring the cost model: which tool answers which question

Seven harnesses, built while doing the work in
[local-engine-cost-model-agreement.md](local-engine-cost-model-agreement.md). They exist because each
answers a question the others structurally cannot, and reaching for the wrong one wastes days — three
successive reworkings of one term improved the metric being watched and moved routing by
`-0.003 µs, CI [-0.206, +0.214]`.

Pick by question:

| question | tool |
| --- | --- |
| Is this plan's absolute cost right? | [`bench_cost_model_agreement.py`](../../scripts/bench_cost_model_agreement.py) |
| What SHAPE is the error — uniform, or a tail? | [`bench_cost_error_percentiles.py`](../../scripts/bench_cost_error_percentiles.py) |
| Is it the features, the coefficients, or the arm's shape? | [`bench_cost_error_attribution.py`](../../scripts/bench_cost_error_attribution.py) |
| What should the constants be? | [`fit_cost_model.py`](../../scripts/fit_cost_model.py) |
| Does the model ORDER two plans correctly? | [`bench_pairwise_ordering.py`](../../scripts/bench_pairwise_ordering.py) |
| Where does routing actually lose time? | [`bench_regret_matrix.py`](../../scripts/bench_regret_matrix.py) |
| Did a change help end to end? | [`bench_plan_misselection.py`](../../scripts/bench_plan_misselection.py) `--compare`, or [`bench_query_latency_ab.py`](../../scripts/bench_query_latency_ab.py) vs `main` |

The end-to-end answer for the whole cost-model stack is recorded in
[local-engine-cost-model-stack-result.md](local-engine-cost-model-stack-result.md) — including the
bands, because a mean over this distribution understates the tail and a p99 overstates it.

All of them draw queries from one universe, [`query_sampler.py`](../../scripts/query_sampler.py), in
one of two weightings. Diagnostics default to `uniform` because their job is to FIND errors and
uniform reaches the rare tails; latency defaults to `realistic` because there the question is what
users wait for. Both matter: artwork is 5% of realistic traffic and carries **50% of all routing
regret**.

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
- **A grid optimum on the boundary is not an optimum.** Two sweeps had to be redone for this. Use
  geometric grids open at both ends, and check the optimum is interior.
- **Fixing a feature can make agreement worse**, because coefficients were compensating. Expect it,
  and refit in the same change.
- **Verify the fitter mirrors the engine.** `fit_cost_model.py` reimplements `cost.rs` in Python and
  had silently drifted for two revisions; it now checks itself against `predicted_ns` and refuses to
  fit below 99% agreement.
- **A plan that DECLINES accumulates no trials**, so it is absent from every measurement here. Enabling
  a declining path introduces a population nothing has ever measured.
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
