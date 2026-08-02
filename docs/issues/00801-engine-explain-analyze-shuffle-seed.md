# Seed `explain_analyze`'s participant shuffle per query, not per call

[#801](https://github.com/jbylund/sylvan_librarian/issues/801). Found in review of
[#797](https://github.com/jbylund/sylvan_librarian/pull/797) and deliberately left out of it — that
PR's scope is instrumentation, and this is a measurement-methodology change to the harness that
consumes it.

## The defect

`explain_analyze` runs `n + 2` participants per round — every applicable plan, plus the acquire step
and the routed path — in a shuffled order, seeded from a constant:

```rust
const PARTICIPANT_SHUFFLE_SEED: u64 = 745_002;
// ...
let mut rng = rand::rngs::SmallRng::seed_from_u64(PARTICIPANT_SHUFFLE_SEED);
```

The RNG is constructed inside the function. Every call with the same participant count `n` therefore
draws the *identical sequence of permutations*, round for round, forever.

For a single query that is the intended property, and it is load-bearing: an A/B of two builds has
to compare the same execution order or ordering drift swamps the difference being measured.

For a sweep it is a bias that does not average out. `scripts/bench_cost_model_agreement.py` pools
thousands of queries into per-(plan, acquire branch) cells; every one of those samples reuses the
same orderings, so drawing more queries does not help.

## Why it is the same shape as the bug the shuffle fixed

#797 replaced a cyclic rotation with a shuffle, and the argument was that rotation only moves the cut
point — cyclic adjacency is fixed, so every participant keeps the same immediate predecessor round
after round, and rotation balances only which one goes first.

A per-call constant seed reintroduces that one level up. The bias attaches to **rank position** (the
index into the cost-sorted `estimates`), not to plan identity — but rank correlates with plan
identity, because the sort key is predicted cost and a given plan's cost arm puts it in similar
positions across similar queries. So it survives into a table keyed by plan.

`explain_analyze`'s doc says the residual is "zero-mean and shrinks with rounds." True within one
query. The sweep is where the number is actually read, and there it shrinks with nothing.

## Fix

Mix a per-query value into the seed:

```rust
let mut rng = SmallRng::seed_from_u64(PARTICIPANT_SHUFFLE_SEED ^ query_shape_hash);
```

Same query and same build still produce the same order, so the A/B property is untouched; different
queries decorrelate.

The cheap source is already in hand at the construction site — `explain` has just returned
`AcquireFacts`, whose `feats` is an all-integer `PlanFeatures` (`eval_domain`, `n_cards`, `matches`,
`n_printings`, `scan_units`, `residual_tier_ns100`, `limit`, `offset`, the three compose build terms,
`popcount_words`, and the `ComposePaging` enum). Hash that plus `QueryParams`' six small fields.

Notably this avoids hashing `FilterExpr`, which derives only `Clone` today
([filter.rs](../../card_engine/src/filter.rs)) — adding `Hash` to it would mean covering
`regex::Regex`, which is not worth it for a seed.

Two different queries that happen to produce an identical feature vector will collide and share an
ordering. That is fine: they are the same query as far as this harness is concerned.

## Rejected

- **Reseed from entropy per call.** Loses the reproducibility the constant exists for, which is the
  one property the current design gets right.
- **Raise `num_trials`.** Does not touch it. The bias is common-mode across samples, not noise
  within them — this is exactly the case where more data does not converge.
- **A position-balanced design** (Latin square: each participant occupies each slot equally over
  `n + 2` rounds). Strictly better statistically, and it is what the rotation was reaching for. It
  needs `num_trials` to be a multiple of `n + 2`, which `explain_analyze` cannot assume — `n` varies
  per query and `num_trials` is a caller argument. Worth revisiting only if the seed fix proves
  insufficient.

## Verification

The claim to test is that ordering no longer contributes a fixed component, so the check is a
comparison of *two sweeps* rather than an absolute number:

1. Run `bench_cost_model_agreement.py` twice at the same budget with two different base seeds, on
   today's code. Record the per-(plan, acquire) medians.
2. Repeat after the change.
3. The two sweeps should agree with each other *better* after than before. If they already agree
   today, the bias is smaller than the run-to-run spread and this is not worth shipping — record
   that and close it.

Follow the usual discipline for this repo's benchmarks (idle machine, equal-length env values,
canary queries); see [performance-pr-workflow](../workflows/performance-pr-workflow.md).

## Caveat for held numbers

Changing the seed derivation makes `trials_ns` collected before it incomparable with `trials_ns`
collected after — the same caveat #797 already carries for the rotation-to-shuffle change. Any
calibration fit in flight against pre-change numbers needs re-running.
