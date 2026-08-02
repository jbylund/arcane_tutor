# Benchmark toolkit audit: 42 scripts, and what shape targeting would collapse

`scripts/` holds 65 Python files, 42 of them `bench_*` / `study_*` / `census_*`. This is a keep /
merge / delete pass over all 42, plus the one API change that makes most of the deletions possible.

Companion to [reference-cost-model-measurement.md](reference-cost-model-measurement.md) (which tool
answers which question) and [the performance PR workflow](../workflows/performance-pr-workflow.md)
(when in a PR's life to reach for each).

## Method, and its one blind spot

Classified by where a script is referenced from: a doc outside `docs/issues/done/` means live; only
`done/` means the investigation that spawned it has shipped; neither means unreferenced.

**That signal is unreliable for anything recent.** `bench_plane_popcount_cost.py` and
`census_candidate_materialize.py` both landed 2026-08-02 in #816 and have no doc reference yet —
and `bench_plane_popcount_cost` has an active worktree against it. New is not dead. Dates were
checked for every zero-reference script before recommending anything.

| bucket | n |
| --- | --- |
| Live — referenced from an open doc | 14 |
| Closed — referenced only from `done/` | 20 |
| Unreferenced, ≥3 weeks old | 6 |
| Unreferenced, landed this week | 2 |
| **total** | **42** |

(`survey_queries.py`, `query_sampler.py`, `costbench.py` and `build_wild_corpus.py` are shared
infrastructure rather than benchmarks, and are outside this count.)

## The structural finding: targeting belongs in the sampler

Roughly 20 of these scripts exist because they need **one query shape**: a bare range under
`unique=printing`, a compose leaf, a negated range, a two-sided bound. Each hand-rolls a `CONFIGS`
list or a private generator, then re-implements the same measurement loop around it.

`query_sampler.py` already has the hard part. `predicate(family, rng)` is public, there are 13
families, and thresholds are drawn at a uniformly-sampled **quantile of the real column**. What it
lacks is any way to constrain a draw. `query()` always takes 1–3 predicates from the full weighted
table; `unique()` and `orderby()` always draw from theirs.

A shape filter — restrict families, pin the predicate count, restrict distinct-on and orderby —
would let a targeted benchmark be a flag rather than a file:

```python
Shape(families={"range"}, predicates=1, unique={"printing"}, orderby={"edhrec"})
```

### What that does and does not replace

It replaces the **generated** half. `bench_printing_range`'s `target` group is exactly the shape
above; `bench_range_bits`' is the same under `unique=card`.

It does **not** replace the curated half, and that half is the science:

- **Selectivity anchors.** `subtype=Human` is in `bench_collection_eq_gt` because Human is the most
  common subtype at 10,567 postings — chosen so a full scan costs something real.
- **Matched algebraic pairs.** `-usd<0.25 usd<5` against `usd>=0.25 usd<5` in
  `bench_negated_range_narrowing`. A sampler cannot generate a pair that must come out equal.
- **Controls chosen to be unaffected.** The `control` groups are picked by knowing what the change
  touches. That is a human judgement about the diff.
- **Negation.** No family produces `-set:dmu`. Several benchmarks turn on exactly that.

So the realistic outcome is not deletion but compression: a targeted benchmark becomes a short
curated `CONFIGS` plus a shape spec, sharing one runner. Call it ~110 lines down to ~30.

### Where it pays most

Three **cost-model** harnesses hand-roll a range generator, and all three sample selectivity in a way
that cannot answer the question they were built to ask:

| harness | how it picks a range threshold |
| --- | --- |
| `bench_card_range_estimate` | `rng.choice(...)` over a hardcoded value list |
| `bench_cost_model_agreement` | log-uniform over hardcoded bounds — `usd (0.05, 400)`, `cn (1, 500)` |
| `query_sampler` | uniformly-drawn **quantile of the actual column** |

`query_sampler.py`'s own docstring makes the case against the first two: a cost model is a function
of selectivity, and a benchmark that samples a handful of arbitrary points cannot say whether the
model is right. Shape targeting is what lets those two adopt it without losing their range-only
focus.

## The query-universe defect, separately

[reference-cost-model-measurement.md](reference-cost-model-measurement.md) states that *"All of them
draw queries from one universe, `query_sampler.py`."* Two of seven do.

| harness | source | verdict |
| --- | --- | --- |
| `fit_cost_model`, `bench_cost_error_attribution` | `QuerySampler` | correct |
| `bench_plan_misselection` | wild corpus (default) / `random_query` (opt-in) | **fixed** — see below |
| `bench_plane_popcount_cost` | `random_query` | **wrong universe** |
| `census_candidate_materialize` | `random_query` + wild corpus | wild slice is deliberate |
| `bench_cost_model_agreement` | private `sample_query` | hardcoded predicate lists |
| `bench_card_range_estimate` | private `random_range_query` | range-only is deliberate, values are not |

An earlier draft of this doc called `bench_plan_misselection` the worst case, on the grounds that
the headline regret number came from `random_query`. That was wrong. Its default source is
`--source wild-operators`, the real Scryfall traffic slice, which is the *right* universe for a
regret number — regret is what users actually lose. Only the opt-in `--source random` used the load
generator.

That opt-in path now draws from `QuerySampler`. Same synthetic role, corpus-derived values,
quantile-placed thresholds, and a real spread of distinct-on and orderby instead of a fixed
`edhrec`. It changes what the source can find, measured at `--sample 150 --seed 0`:

| | `random_query` | `QuerySampler` |
| --- | --- | --- |
| multi-plan queries | 150 | 103 |
| mis-selected | 2 (1%) | 5 (5%) |
| mean regret | 0.66 µs | 3.32 µs |
| max regret | 72.0 µs | 162.1 µs |

Five times the mis-selection rate over a smaller denominator, because the sampler produces more
selective queries where fewer plans apply. The mis-selections it surfaces are shapes the old
generator could not emit — `c:r usd>=0.47 usd<=0.63` and `cn>=13 cn<=108 name:of`, both bounded
ranges, the shape `query_sampler`'s header singles out as missing from every older generator.

## Per-script disposition

**Keep — the cost-model toolkit (10).** Each answers a structurally distinct question; see the
reference doc's table. Two merges are defensible:

- `bench_cost_model_agreement` (441 lines) reports an absolute measured/predicted median.
  `bench_cost_error_percentiles` (97 lines) reports the same ratio at nine percentiles — the median
  is one of its columns. The agreement harness's paging/decline/phase-share reports are the part
  that is genuinely its own; those should move rather than be lost.
- `bench_plan_misselection`'s argmin check overlaps `bench_regret_matrix`'s transition slice.

**Keep — the other four live ones.** `bench_verify_order`, plus the range-acquire cluster below.

`bench_bitplanes` is classified closed (all four references are in `done/`) but **must not be
deleted**: `load_engine` lives in it and 20 scripts import it from there. Extract that into
`costbench` first, then the remaining 200 lines can be judged on their own.

**Delete — closed investigations (19).** Referenced only from `docs/issues/done/`, and the workflow
doc already treats the targeted set as disposable per-investigation work:

`bench_arith_tuple_postings`, `bench_border_planes`, `bench_collection_compose`,
`bench_collection_eq_gt`, `bench_compose_orderby_range_walk`, `bench_compose_permutation_fallback`,
`bench_cost_guards`, `bench_guard_validation`, `bench_legality_banned_restricted`,
`bench_legality_divergent`, `bench_memo_crossover`, `bench_negated_range_narrowing`,
`bench_oracle_word_index`, `bench_permuted_order`, `bench_printing_planes`, `bench_printing_range`,
`bench_produces_planes`, `bench_rarity_planes`, `bench_tag_postings_compose`.

Git history is the archive; a closed benchmark is recoverable and a stale one is a trap. Before
deleting any, harvest its curated `CONFIGS` — the selectivity anchors and control groups are the
expensive part and should survive as shape-spec fixtures.

**Delete — unreferenced and old (6).** `bench_devotion`, `bench_name_order`, `bench_random`,
`bench_range_bits`, `bench_price_range_targeted`, `bench_text_memo`. All ≥3 weeks old with no doc
reference in either state.

**Leave alone — too new to judge (2).** `bench_plane_popcount_cost` (active worktree),
`census_candidate_materialize`. Both from #816 this week. Revisit once their work lands.

**Range-acquire cluster (4) — merge candidate.** `bench_card_range_estimate`,
`bench_range_estimate_scan`, `study_range_slice_cardinality`, `study_range_slice_layouts` all probe
the same acquire. Three are live. Worth one pass to see whether they are one tool with three
reports.

## Net

42 → about 17, and the reduction comes mostly from deleting finished work rather than from clever
sharing. That ordering matters: **triage before consolidation.** Two of the three harnesses queued
for a query-source fix turned out to be unreferenced, and fixing them first would have been work
spent on scripts that may not survive.

## Sequence

1. ~~Extract `load_engine` from `bench_bitplanes` into `costbench`~~ — done, 35 call sites.
2. ~~Add the shape filter to `QuerySampler`~~ — done, as `Shape`, with unit tests.
3. ~~Switch `bench_plan_misselection`'s `random` source~~ — done, delta recorded above.
4. ~~Correct the reference doc's "one universe" claim~~ — done.
5. Port `bench_card_range_estimate` and `bench_cost_model_agreement` off their private range
   generators onto `Shape(families={"range"}, predicates=1, ...)`.
6. Harvest curated `CONFIGS` into fixtures, then delete the 25 closed/unreferenced scripts.
7. Revisit the range-acquire cluster and the two agreement/percentile merges.
