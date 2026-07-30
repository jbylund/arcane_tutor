# The decline fallback never reconsidered a non-materializing plan, at up to 260 µs (fixed)

When a `Prep::Range`-acquired query's chosen fast path declines at runtime, dispatch re-chooses
among **materializing plans only** — so `PrintingCompose`, which is applicable and often 30–70x
faster, is never reconsidered. Measured worst case: a bare `year:` or `usd:` range at
`unique=printing` paying 266 µs where compose needs 2–4 µs.

Everything else the router does is fine. Across 249 wild-operator queries the mean regret is
0.14 µs and the worst is 14.1 µs.

## The mechanism

[`run_query_routed`'s `Prep::Range` dispatch arm](../../card_engine/src/lib.rs) runs the chosen
fast path, and on `None`:

```rust
let prep = prepare_candidates(ctx, params, filter, plane);
let feats = candidate_feats(ctx, params, &prep, filter);
let plan = choose(filter, &feats, true);   // materializing_only = true
```

`PhysicalPlan::materializing()` excludes `PrintingRangeScan`, `PrintingCompose` and
`CardRangePopcount`. So the fallback can only land on `StreamedSelect` or `GatheredScan`, even when
the *other* non-materializing fast path was applicable, would not have declined, and is an order of
magnitude cheaper.

`usd>20` at `unique=printing`, from `explain_analyze`:

| plan | predicted | measured |
| ---- | --------: | -------: |
| PrintingRangeScan | 15.0 µs | **declined** ← picked |
| PrintingCompose | 129.6 µs | **2.4 µs** |
| StreamedSelect | 578.7 µs | 109.1 µs |
| GatheredScan | 738.1 µs | 130.7 µs |

`routed_ns` is 111.0 µs — it tracks `StreamedSelect`, confirming the fallback took a materializing
plan while a 2.4 µs option sat unused. `usd>5` is worse: routed 267.7 µs against compose's 3.8 µs.

Note the cost model also over-predicts `PrintingCompose` badly here (129.6 predicted against 2.4
measured, ratio 0.02), so even a fallback that *could* pick compose would rank it last. Both need
fixing, but the exclusion is the hard blocker.

## Sizing

`bench_plan_misselection.py` scores regret as `routed_ns − best measured plan` — what the engine
actually pays against the best available.

| source | multi-plan | mean regret | max regret | regret >5 µs |
| ------ | ---------: | ----------: | ---------: | -----------: |
| wild-operators | 249 | 0.14 µs | 14.1 µs | 2 |
| random | 250 | 1.79 µs | **265.0 µs** | 5 |

The 100 µs-class misses are all the decline fallback on bare ranges at `unique=printing`
(`year:2022`, `year:2024`, `year:2019`, `usd>20`, `usd>5`). Below that sits a 13–33 µs
`StreamedSelect`/`GatheredScan` cluster on candidate acquires, a separate and much smaller problem.

Neither corpus is authoritative — the wild one samples linked URLs (bot-dominated, per its own
README), the generated one a domain-informed guess at the search surface. Read them as a range.

## Scanning for mis-*costing* rather than mis-selection

`--calibration` reports each plan's measured/predicted ratio across the corpus. This is the more
useful scan: a plan costed 30x wrong is picked correctly right up until it competes closely with
something, so mis-costing is the leading indicator and mis-selection only the symptom.

200 generated queries. `net` subtracts the measured acquire from materializing plans, because
`measured` includes acquire and `predicted` does not.

| plan | n | raw ratio | net | p25 | p75 | worst |
| ---- | --: | --------: | --: | --: | --: | ----: |
| GatheredScan | 200 | 6.47 | **0.84** | 1.89 | 27.66 | 0.01 |
| PlanePopcountOrder | 36 | 2.44 | **2.44** | 1.01 | 5.02 | 8.69 |
| StreamedSelect | 200 | 0.69 | **0.58** | 0.18 | 0.90 | 0.00 |
| PrintingCompose | 9 | 0.47 | **0.47** | 0.11 | 0.92 | 0.02 |
| CardRangePopcount | 1 | 0.72 | 0.72 | – | – | 0.72 |

Ratio >1 means under-costed (the model thinks it is cheaper than it is, so it gets over-picked); <1
over-costed and under-picked.

**Netting acquire inverts the headline.** Raw, `GatheredScan` looks under-costed by 6.5x — which
would be the single biggest cost-model defect in the engine. Net of acquire it is 0.84, i.e. fine:
the whole apparent error was the unpriced acquire step, which `GatheredScan` pays like every
materializing plan. Anyone reading the raw column alone would go and "fix" a calibrated arm.

What survives netting is two arms:

- `PlanePopcountOrder` under-costed a median 2.4x, worst 8.7x. It materializes nothing, so raw and
  net agree — this one is real and unexplained.
- `PrintingCompose` over-costed a median 2x and up to **50x** (`set:mkm set:neo`, ratio 0.02). This
  is the same defect the decline fallback runs into: compose is ranked last, so even a fallback that
  *could* choose it would not.

## Why this stayed hidden through three earlier passes

Each produced a confidently wrong answer, and each is now closed off.

**Scoring the picked plan instead of the routed path.** The harness required the picked plan to have
*run*, so every decline was skipped — which is exactly the failing case. Regret is now
`routed − best`. This is what raised the worst case from 94 µs to 265 µs.

**Reconstructing the pick.** Before `PlanEstimate::picked` existed, the harness derived the router's
choice as `argmin(predicted)` over plans that ran, inventing a 12% mis-selection rate out of a real
1% and a tidy causal story that was entirely artifact.

**A mislabelled acquire.** `explain` reported `count_source: "range"` for compose-acquired queries,
because `Prep::Range` is a misnomer covering three acquires. One pass went at the wrong branch.

Also worth keeping: `routed ≈ forced-picked` on non-declining queries, which validates that forcing
a plan does not distort timing.

## Both fixed

**Root cause of the 2x–50x spread: two populations, not one miscalibrated constant.** Compose is
~1.5x over-costed generally, and was **33x** over-costed only when costed off the
`PrintingRangeScan` acquire. That branch sets `eval_domain = n_cards` and `scan_units = n_printings`
(correct for costing P3/P4, which is what its comment says it is for) and left `compose_paging` at
`mk_plan_feats`' `Gather` default — and `Gather` is the one branch of compose's cost whose page term
is `O(eval_domain)`. So compose was charged a full-corpus gather it would never run:
31,508 × 2.87 + 97,206 × 0.36 ≈ 125 µs against 2.4 µs measured. `CardRangePopcount` escaped it only
because its `eval_domain` is the small card estimate.

`compose_paging_for` is now shared by every branch that costs compose, not just compose's own.

**The exclusion needed fixing too, and the cost fix was its prerequisite.** Correcting the cost
alone left `routed` at 106.5 µs, because `PrintingRangeScan` (15.0) still ranked ahead of compose
(16.3), still declined, and the fallback still could not reach compose. `declined_sibling_fastpath`
now tries the declining plan's non-materializing sibling first, gated on the model so it can never
be slower than the fallback it replaces on the model's own terms.

| measurement | before | after |
| ----------- | -----: | ----: |
| `usd>20` printing, routed | 111.0 µs | **2.4 µs** |
| `usd>5` printing, routed | 267.7 µs | **4.2 µs** |
| random sweep, max regret | 265.0 µs | **61.3 µs** |
| random sweep, mean regret | 1.79 µs | 1.46 µs |
| compose ratio from `printing_range_scan` | 0.03 | **0.55** |

The 100 µs-class decline-fallback category is gone. `usd>50`, where *both* fast paths decline, still
correctly falls through to materializing.

## What is left

The remaining 26–34 µs misses are the pre-existing `StreamedSelect`/`GatheredScan` discrimination
and a `PrintingCompose → StreamedSelect` pair (`type:aura`, `type:wizard`) — a separate problem from
this one, and small enough to leave alone until something shows it matters.

Two calibration findings from the 2,500-query scan remain unaddressed, both pre-existing:

- `PlanePopcountOrder` under-costed a median 1.61x, p90 6.16 (n=157). It materializes nothing, so
  netting acquire is a no-op — this one is real and unexplained.
- `StreamedSelect`/`GatheredScan` costed off the `card_range_popcount` acquire are under-costed
  ~2.4x (n=13). Thin sample; re-measure before acting.

## What to do

1. Re-measure `PlanePopcountOrder`'s 1.61x under-costing on a larger sample and find the term it is
   missing. It is the largest remaining calibration gap and it is not explained by acquire.
2. Leave the 26–34 µs `StreamedSelect`/`GatheredScan` cluster alone until something shows it matters.
3. `PrintingCompose` is still ~1.5x over-costed generally (0.69 from its own acquire). Harmless at
   that size, and tightening it risks the ranking that now works.

## Method notes

- `min` of 15 trials after 3 warmups, per plan, plans rotated each round so none benefits
  systematically from accumulated allocator or cache state (`explain_analyze`'s discipline).
- Every plan runs from a fresh `filter.clone()`, so each pays the one-time
  `memoize_text_predicates` cost identically — a fair head-to-head, not real query latency.
- Queries where the best measured plan *is* the picked plan are excluded from the table: `routed −
  best` there is routing overhead plus noise, not a wrong choice.
- Run-to-run, misses under ~5 µs flip; only the larger ones are stable. Quote those.
- Store built from `benchmarks/bitplanes/corpus.jsonl` (31,508 cards, 97,206 printings).
