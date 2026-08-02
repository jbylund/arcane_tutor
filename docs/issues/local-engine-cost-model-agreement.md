# Cost-model: agreement 8/16 cells to 15/16, and why routing did not move

`cost::plan_cost` was accurate enough to rank plans correctly most of the time while being badly
wrong in absolute terms on several arms — a state that holds until something competes closely, and
then mis-routes. This is the record of measuring that gap and closing most of it.

Bar, set before the work: **every (plan, acquire) cell's measured/predicted median inside
[0.8, 1.25]**, measured by [`scripts/bench_cost_model_agreement.py`](../../scripts/bench_cost_model_agreement.py).

Measured baseline-to-branch on the same sampler (`92ecebf`, before any cost change, vs the branch tip):

| | agreement cells in [0.8, 1.25] | n-weighted \|ln(median)\| | routing regret |
| --- | --: | --: | --- |
| uniform | **8/16 → 15/16** | **0.3501 → 0.1104** | no detectable difference |
| realistic | **10/16 → 12/16** | 0.2432 → 0.2012 | no detectable difference |

Routing, paired over ~1,870 queries per mode: realistic +0.018 µs CI [−0.316, +0.362]; uniform
−0.517 µs CI [−1.323, +0.391]. **Both span zero.**

### Absolute accuracy improved a lot; routing did not move at all

This is the headline finding, and it is not a disappointment — it is the thing to know before doing
more of this work. `cost.rs` said why in its own module docs and it took a long time to believe:

> The per-card verify tier is added to BOTH the gather and stream per-card terms, so it largely
> cancels in their argmin — cardinality and plan structure do the deciding.

An argmin depends only on DIFFERENCES between plans. Most of what was wrong here was **common-mode**:
shared by every plan that reads it, so fixing it improves every absolute number and changes no
decision. Three successive reworkings of the per-card residual term (multiplicative → additive →
floor) improved n-weighted agreement monotonically and moved routing by −0.003 µs, CI
[−0.206, +0.214].

**A mid-work claim of a 12x routing win was measured wrong** and is corrected here. That A/B compared
the mode-split commit against the commit immediately before it, not against the baseline. The first
cost commit zeroed compose's verify tier for all three modes, which *introduced* that regression; the
mode-split commit fixed it. The pair nets to zero, which is what the baseline comparison above shows.

Use [`scripts/bench_pairwise_ordering.py`](../../scripts/bench_pairwise_ordering.py) to target routing
work: it reports, per plan pair, how often the order is right and what being wrong costs. Its own
limitation, measured: pairwise regret OVERSTATES routing loss, because argmin is over all plans. On
the worst pair, compose beat StreamedSelect in 708 mis-ordered cases but beat the best of all plans
in only 189 — a third plan was usually the right answer regardless.

### The measurement, which took three tries to get right

The first two attempts at the routing number were both wrong, in opposite directions, and the
failures are worth keeping:

1. **Three seeds of `--sample 400`, unpaired.** Reported a 59% improvement and a 710 µs worst-case
   regret. Both were noise: three repeats of the *identical* engine and seed gave max regret of
   38 / 41 / 91 µs, and the mean swung 3x run to run (0.26 then 0.82). Regret is heavy-tailed — most
   queries contribute exactly zero — so its mean converges far too slowly for this.
2. **Interleaved blocks, still unpaired, still the old generator.** Correctly refused to claim the
   noise was signal, and concluded "no detectable difference". That was also wrong, because the
   generator drew thresholds from hardcoded value lists (`year:2019`–`2024` on a 1993–2026 corpus)
   and never produced bounded ranges at all.
3. **Paired, with corpus-drawn thresholds** ([`scripts/query_sampler.py`](../../scripts/query_sampler.py)).
   Found a 12x difference the first two could not see.

The harness was validated in both directions before being believed: on a **null** (same engine
twice) it reports CI [−0.035, +0.389], spanning zero and symmetric per-query; on the **positive
control** above the effect traces to its mechanism exactly — artwork saves 82.9 µs/query, card
35.0, printing 0.7, which is precisely the printing/non-printing split the change makes.

Use `--source distribution --out A.jsonl` then `--compare A.jsonl B.jsonl`. Never compare two
headline means.

## Features first, rates second

The ordering is not stylistic. A feature that miscounts by 2.5x cannot be repaired by any rate, and
a least-squares fit will bury the error in whichever coefficient correlates with it — so the fit
comes out plausible and the model stays wrong. Executor counters (`cards_visited`,
`printings_scanned`, `matches_pushed`, added in the preceding commit) exist to catch exactly this:
each is checked against the feature meant to predict it before any rate is touched.

### 1. CardRangePopcount costed the materializing alternatives as if the narrowing survived

`range_narrowed` returns an enumerable printing list only below `MAX_NARROW_FRACTION`; past that it
degrades to a printing-space bitmap, which cannot yield card ids, so `prep.card_ids()` falls back to
`0..n_cards` and the scan walks the whole corpus. The branch charged `card_est` either way.

| | model said | counters measured |
| --- | --: | --: |
| `eval_domain` | 12,450 | `cards_visited` **31,508** |
| `scan_units` | 12,450 | `printings_scanned` **97,206** |
| `matches` | 12,450 | `matches_pushed` 12,450 ✓ |

Switching the branch on that predicate took `GatheredScan/card_range_popcount` from **3.20 to 1.01**.
The sibling `PrintingRangeScan` branch already assumed the unnarrowed regime unconditionally, which
is why *its* cells were passing at 0.99/0.92 all along — the two branches disagreed, and only one
was right.

### 2. The verify tier rode `scan_units`, but `card_pass` runs once per card

Both scan arms charged `scan_units * (SCAN_PER_ROW + tier_ns)`. The loop calls `filter.card_pass`
once per `cid`; only the cheaper printing-dependent residual is rechecked per row. Charging the full
per-card verify cost on every row is invisible in card mode (`scan_units ≈ eval_domain`) and
overcharges printing/artwork by the whole printings-per-card ratio.

Moving it to `eval_domain` made `GatheredScan` mode-consistent — card/printing/artwork
1.36/1.34/1.24, previously spread — which converted a mode-dependent error, unfixable by rates, into
a uniform scale error that a refit could absorb.

### 3. PrintingCompose charged a verify tier it never pays — in one mode of three

Compose applicability means every leaf is index-composable in printing space, so `narrow_rec`
returns `tight`, which is the `residual_exact` behind `all_match_known`. The branch passed
`verify_cost_tier(filter)` regardless, on 100% of compose queries.

But that reasoning only holds for **printing** mode, where the composed printing set is the answer
outright. In card/artwork mode the same filter is inherently printing-*dependent* — the plan still
has to choose which printing to show — so `card_pass` returns `Tri::PrintingDep`, not `Tri::True`.
Measured match-loop ns per card, on rows the model called tier-free:

| unique | P3 | P4 |
| --- | --: | --: |
| printing / compose | 5.0 | 8.1 |
| card / compose | 28.2 | 24.0 |
| artwork / compose | 25.6 | 29.0 |
| card / plane (true `all_match` baseline) | 3.4 | 7.9 |

Zeroing it in all three modes was the first attempt and over-corrected; splitting by mode is the fix.

### 3b. A plan that will decline must not win the argmin

Making the materializing plans correctly more expensive on card/artwork compose tipped the argmin to
PrintingCompose, which then *declined at runtime* on sparse card-mode queries (`type:goblin`). The
router paid the detour and ran something else anyway. `ComposePaging` gains a `Decline` variant.

The three decline sites are not alike: the `Perm` branch's `total <= STREAM_MIN_MATCHES` is a
**correctness** guard ("ordering ties differently"), while the gather branch's two are **cost**
heuristics. Removing the cost gates and letting the model decide — the obvious next thought — was
tried and made routing worse on all three seeds tested; `COMPOSE_GATHER_MAX_CARD_FRACTION`'s
hand-calibrated 0.85 still beats `plan_cost` at that decision. Revisit once the compose arm agrees.

### 4. StreamedSelect has no per-scanned-row cost at all

P3 only *counts* matches: `card_match_count` is O(1) offset arithmetic under `all_match`, and the
artwork-group count is a build-time constant. P4 must *push* every matching row, so `scan_units`
genuinely drives it. Charging P3 per row put its printing/artwork bucket at 0.43 while card mode sat
at 0.84; every fit, including an unregularised one, drove the rate to exactly 0.00.

The term is gone. Its flat setup cost became an O(`n_cards`) rate instead, for the counts buffer the
loop resizes and clears on every query. Re-tested after fix 3 landed (the earlier evidence predated
it): adding the term back still makes agreement worse, 0.74 → 0.70.

## Fitting the rates

[`scripts/fit_cost_model.py`](../../scripts/fit_cost_model.py) regresses measured time on exactly the
vector `plan_cost` consumes. Three choices were forced by measurement, each after the obvious
alternative failed:

- **Log-ratio objective, not relative error.** `sum (Xc/y - 1)²` is asymmetric — over-prediction is
  unbounded, under-prediction saturates at 1 — so the minimiser shrinks every per-unit rate to zero
  and keeps only the intercept. It did exactly that, and produced *worse* agreement than the
  constants it replaced.
- **Ridge-anchored to the previous values.** Several columns barely vary on one corpus: the P3 floor
  is literally `n_cards` or 0, `page_span` is usually just `limit`. Unregularised, the fit traded
  freely against the intercept and produced a 42 µs fixed cost.
- **Weighted by sample frequency.** Deduplicating to one row per distinct shape fits a *different*
  distribution than the bar scores — it gives a rare expensive shape the same say as a common cheap
  one. That fit read 0.99 on shapes while the sampled distribution sat at 0.62–0.85.

Coefficients are stable to <3% across independent seeds.

## What is still wrong

| cell | median | note |
| --- | --: | --- |
| `StreamedSelect/printing_compose` | 1.31 | p10 0.02 / p90 2.13 — heterogeneous, not a scale error |
| `PrintingCompose/card_range_popcount` | 1.33 | |
| `GatheredScan/plane` | 0.79 | marginal (bar is 0.80) |
| `CardRangePopcount/card_range_popcount` | 0.78 | marginal |

Two of the four miss by ≤0.02 and would move on almost any nudge; they are not worth a targeted
constant, which would be fitting the bar rather than the machine.

`StreamedSelect/printing_compose` is the real one, and it is **not** a rate problem: P3's error
distribution is heavy-tailed enough that the log-LS objective no longer tracks its median. A refit
lands at median 0.74 where the shipped constants give ~1.0, so refitting it actively hurts.

It is also **not** the emission walk, which was the obvious suspect. With `emit_steps` counted and
`ns_finish` timed separately, the walk behaves exactly as the inverse-selectivity argument predicts
— `emit_steps` tracks `n_cards · page_span / matches` with a median ratio of 0.83 — but emission is
only **~3% of P3's runtime** (median `ns_finish` 1.38 µs against `ns_loop` 51.21 µs). Neither plan
can stop early, because both need an exact `total`: P4 pushes every match and P3 sums per-card
counts over every candidate. Only the popcount plans escape that, by taking `total` from a bitmap.
A walk term was tried and rejected on measurement (22% → 15% within 25%). The remaining error is in
P3's match phase.

## `verify_cost_tier` is fine; the CONJUNCTION model is what is missing

The refit introduced `*_VERIFY_TIER_SCALE` (2.87 / 2.65) — a multiplier on `verify_cost_tier`'s
output — and an earlier version of this document claimed that meant the tier constants were "1.7-5x
low". **That was wrong**, and the correction is the useful part.

`bench_verify_cost` (`cargo test --release bench_verify_cost -- --ignored --nocapture`) times the
real `FilterExpr::matches()` path per node against the real archive. It is the right instrument, and
it VALIDATES the constants:

| constant | claims | measured |
| --- | --: | --- |
| `MASK_COMPARE_NS100` | 4.0 ns | 2.08–3.60 (median 2.8) |
| `SET_LOOKUP_NS100` | 9.0 ns | 2.12–8.69 (median 5.3) |
| `TEXT_SCAN_NS100` | 23.0 ns | **21.5** |
| `REGEX_MACHINERY_NS100` | 50 ns | 45.8–47.5 |

Each is at or slightly above measured per-node cost — deliberately conservative, not miscalibrated.
The earlier "1.7-5x low" figure came from an end-to-end subtraction that swept up everything else a
residual costs per card (tri-walk setup, the reused residual vec, the branch, the per-printing loop)
and attributed it to the node. Two separate confounds, both since fixed: the multi-predicate version
also let `max`-over-children inflate whichever class won the max, which is why its `SET_LOOKUP`
estimate (5.2x) disagreed with the single-predicate one (2.6x).

So the multiplier was wrong in FORM. There is no per-node error to scale away; there is an
unmodelled fixed per-card overhead. `*_VERIFY_TIER_SCALE` is now
`STREAM_RESIDUAL_WALK_NS` / `GATHER_RESIDUAL_WALK_NS`, added rather than multiplied. Fit on
single-predicate queries, one additive constant per plan fits *both* cheap tiers (16.7 / 17.6 ns for
P3, 10.8 / 11.4 for P4) where the multiplicative form needed a different factor per tier — that
agreement is the evidence for the form.

Scored honestly, it is a modest win and only on the axis that matters. Total |ln(median)| across
cells: **n-weighted** (by query volume) 0.212 → 0.189 uniform and 0.256 → 0.236 realistic;
**unweighted** it is marginally worse (0.169 → 0.178), because unweighted counts an n=416 cell the
same as an n=75,044 one. The raw pass-count went 12/16 → 10/16 on that arbitrary boundary, which is
why it is not the number to steer by.

### The real gap: conjunctions

`verify_cost_tier` takes `max` over an And's children. Neither `max` nor `sum` is right, because the
walk sorts children cheapest-first and short-circuits:

```
cost(And) = c₁ + p₁·c₂ + p₁p₂·c₃ + …        (children in EVALUATION order, pᵢ = pass rate)
```

A cheap *selective* child means the expensive one almost never runs (cost → c₁); a cheap
*non-selective* child means it always does (cost → c₁ + c₂). The spread is data-dependent, so no
structural rule and no scalar can span it — which is exactly why the single-predicate calibration
above is honest about single nodes and silent about conjunctions.

Useful bounds fall straight out, and they are cheap:

- **lower** = `min(children)` — cheapest-first ordering always pays at least the cheapest
- **upper** = `sum(children)`
- today's `max(children)` sits between them for no stated reason

The bounds are also a cheap escape hatch: when `min` and `sum` are within a few percent there is no
point estimating selectivity at all. Where they are far apart, `estimator::estimate_cardinality`
already returns lo/hi per subtree (the compose decline gate uses it), so per-child pass rates are
available without new machinery. `Or` is symmetric — it short-circuits on TRUE, so `reach *= (1 − p)`.

**Blast radius.** `verify_cost_tier` is not cost-model private: it also orders And children for
short-circuit evaluation (`children.sort_by_key(...)`, filter.rs) and gates `memoize_pays`. A
selectivity-aware cost changes evaluation order, so validate ordering and memoize decisions
separately from agreement — and note that text predicates mostly never reach the residual path at
all (they are memoized through their indexes first), so `TEXT_SCAN_NS100`'s real consumers are those
two callers, not the residual tier.

There is still no counter for verify invocations or verify time. `residual_matches` runs millions of
times per query, so it needs a feature gate rather than an unconditional increment — but
`bench_verify_cost` measures the per-node cost directly and is the better instrument regardless.

## Where the remaining error is, and that it is reachable

[`scripts/bench_cost_error_attribution.py`](../../scripts/bench_cost_error_attribution.py) splits each
cell's error three ways by substituting realized executor counters for estimated features (isolates
cardinality error), refitting coefficients on those (isolates coefficient error), and calling what
survives both the model's shape. Over 267,938 plan-rows realistic and 178,430 uniform:

| plan / acquire | mean \|ln\| | median | features | coeffs | form floor |
| --- | --: | --: | --: | --: | --: |
| G / candidates | 0.305 | 0.251 | −0.002 | +0.029 | **0.330** |
| G / printing_compose | 0.947 | 0.475 | +0.242 | −0.007 | **0.656** |
| G / printing_range_scan | 0.982 | 0.188 | **+0.447** | +0.239 | 0.291 |
| S / candidates | 0.316 | 0.109 | +0.019 | +0.036 | **0.427** |
| S / printing_compose | 0.790 | 0.271 | +0.054 | +0.081 | **0.692** |

Three conclusions:

1. **Cardinality is now basically right** (±0.04) except `printing_range_scan`, the only
   features-dominated cell, and `printing_compose`.
2. **Coefficients are barely identifiable.** Refitting buys 0.02–0.14 while producing vectors that
   bear no resemblance to the shipped ones (`GatheredScan` fixed 1154 vs 237, push 0.00 vs 3.30).
   Wildly different vectors, near-identical error — not worth chasing.
3. **The model's shape is the ceiling**, and it is worth ~0.33–0.69.

Read the mean *and* the median. `printing_range_scan` has the worst mean (0.98) and one of the better
medians (0.19): its problem is a tail. `printing_compose` has no tail flag and a median of 0.27–0.48,
so its TYPICAL query is mis-costed — that is where the room is.

### The floor is real shape error, not noise

Grouping rows by IDENTICAL realized features, the spread that remains inside a group cannot be
predicted from those features by any model. It is only **0.03–0.18** against floors of 0.33–0.69, so
the floor is a wrong-FUNCTION problem, not noise.

That argument needs the groups to hold DIFFERENT queries, not one query sampled repeatedly — otherwise
the spread is just run-to-run timing noise and says nothing about whether the features suffice. Checked:
14–42% of groups do, and splitting the spread by which kind of group it came from is what makes the
number mean something:

| cell | same query repeated | different queries, same features | form floor |
| --- | --: | --: | --: |
| G / candidates | 0.052 | **0.062** | 0.330 |
| S / candidates | 0.044 | **0.030** | 0.427 |
| S / printing_compose | 0.041 | **0.038** | ~0.68 |
| **G / printing_compose** | 0.053 | **0.184** | 0.656 |

For three of the four, different queries sharing realized features run in the same time as the same
query repeated. Those features DO determine runtime, and the floor is reachable down to ~0.03–0.06 —
81% for G/candidates, 93% for S/candidates.

`GatheredScan/printing_compose` is the exception and the interesting one: 0.184 against a 0.053 noise
floor. Different filters with identical counters take measurably different times, which is a MISSING
FEATURE, not a wrong function. Its 0.656 floor splits roughly into 0.18 missing-feature and 0.47
wrong-function. `StreamedSelect/printing_compose` shows no such signal (0.038), so whatever is missing
is specific to P4 on compose acquires — and that is the one place the conjunction hypothesis below has
direct evidence behind it rather than plausibility.

One hypothesis has already been tried and REJECTED. Per-printing cost is flat on narrowed scans and
roughly doubles on full-corpus sweeps (`candidates` 6.4 → 4.9 ns across a 1000x range, against
`printing_compose` 5.3 → 9.9), which looked like scan locality. Adding a `scan_units × swept-fraction`
column changed the floor by 0.000 on every cell — within a cell the fraction barely varies, so it
carries no information the existing `scan_units` term lacks.

The leading remaining candidate is the CONJUNCTION problem: `verify_cost_tier` takes `max` over an
And's children, so two queries with the same `tier` can have very different true residual cost. That
is a systematic, feature-level miss — precisely the "deterministic but unmodelled" signature above —
and the fix shape is written up earlier in this document (`c₁ + p₁c₂ + p₁p₂c₃`, bounded by
`min`/`sum`, with pass rates from `estimator::estimate_cardinality`).

## Operating-space `matches`: reverted twice, then shipped on principle

`candidate_feats` passes the candidate CARD count as `matches` in every mode, but the result total is
in the plan's operating space. Measured `matches_pushed / matches`: **2.41 for printing**, 1.15 for
artwork, 1.00 for card.

Both are exactly summable from data already in hand — `scan_units` is the printing count under the
candidates (offset deltas, computed one line above) and `indexes.artwork_groups` holds each card's
distinct-artwork count. And the count is exact only when the narrowing is tight, which splits cleanly:

| | `matches_pushed / matches` | log err |
| --- | --: | --: |
| `all_match_known` | **1.00** | 0.000 |
| residual survives | 0.38–0.56 | 0.59–0.98 |

Discounting the untight case by the measured pass rate (0.40 printing, 0.53 artwork — artwork higher
because a group survives if ANY of its printings does) brings the feature to **1.00 tight and
0.91–1.00 with a residual**, from 2.41-under.

**SHIPPED on the third attempt**, carrying a measured routing regression of +0.298 µs,
CI [+0.017, +0.574], deliberately. The feature is provably correct and the regression is a
COMPENSATING error — rates and gates were fitted against a `matches` 2.4x too small in printing mode.
An approximation kept as a hedge against a separate error only makes that error harder to find, and
reverting this twice left the model looking healthier than it was. The compensator is identified:
`matches` feeds `GATHER_PUSH_PER_MATCH`, `page_span`, and structurally P3's small-total floor gate
`matches <= STREAM_MIN_MATCHES`, worth 25.5 µs when it fires — floor eligibility is now 81% card /
73% printing / 77% artwork, printing lowest because its total is correctly larger. Refitting those
three against the corrected feature is the outstanding work; a flat rate change will not do it, since
`matches` moved for printing and artwork only.

The two earlier reverts, and the reason each looked justified at the time: agreement got WORSE, 15/16 → 13/16
cells and n-weighted |ln| 0.1104 → 0.1425. `GATHER_PUSH_PER_MATCH` (3.30) and
`STREAM_EMIT_PER_MATCH` were fitted against the 2.4x-too-small `matches` and had absorbed the error;
making the feature exact breaks that compensation. The attribution had already predicted it — on
REALIZED features it fits `push` to 0.00 against a shipped 3.30.

### Second attempt, after fixing the blocker — still reverted

The blocker was real and is now fixed: `fit_cost_model.py` had drifted and is
[synced and self-checking](../../scripts/fit_cost_model.py). With a trustworthy fitter, the feature was
reapplied and four cost forms tried. None beats the baseline:

| variant | uniform | realistic |
| --- | --: | --: |
| baseline, no matches fix | **0.1104** | 0.2012 |
| + matches, flat `PUSH` 3.30 | 0.1500 | — |
| + matches, flat `PUSH` 2.47 (swept) | 0.1225 | — |
| + matches, hard shed split at `page_span + GATHER_PRUNE_CHUNK` | 0.1461 | — |
| + matches, log-retained + swept rates | 0.1359 | **0.1833** |

The log-retained form deserves recording, because the MECHANISM is real. `GatherSelect::absorb`
tightens `cutoff` at every prune, so the i-th match survives with probability ≈ `k/i` and the expected
number RETAINED across `n` matches is the harmonic sum ≈ `k · ln(n/k)`. Every match is appended and
compared; only that many are kept, moved during compaction and quickselected. So the gather is
genuinely sublinear in `matches`, and charging one flat rate over-costs exactly the high-match queries.

**It earns nothing anyway.** Swept on an open geometric grid with `PUSH = 0` included, the optimum IS
`PUSH = 0` and the retained term's gain is `+0.0000`: a pure flat per-match cost fits identically. Two
earlier sweeps had to be redone because they read a grid BOUNDARY as an optimum — `ABSORB` at 1.5, then
`PUSH` at 0.35. Always check the optimum is interior.

Worse, the flat rate does not generalise: its optimum moved from **2.608** under limits 1–500 to
**5.498** under limits 10–2560 on the same corpus. It is absorbing page-span-dependent cost that none of
the three forms describes, so no single value is right. That is the reason for reverting, not the
headline scores.

Note also that `limit` is the ONLY thing separating a per-match cost from a per-retained-match one, since
`retained` depends on `page_span` and the flat term does not. Sweeps for this need a wide geometric limit
range (1,133 distinct `page_span` values here); the sampler's three limits leave the two collinear.

The remaining gap after that is unnarrowed ARTWORK queries (16% of artwork candidate-acquire queries),
where no corpus artwork total is stored and summing `artwork_groups` over every card is O(n_cards) in
acquire. Storing one `u32` at build time closes it, for an `ARCHIVE_FORMAT_VERSION` bump; the error it
leaves is ~1.15x on ~5% of queries, so it is genuinely marginal on its own.

## The percentile view, and the two defects it found

[`scripts/bench_cost_error_percentiles.py`](../../scripts/bench_cost_error_percentiles.py) prints
estimate/real at p1/p10/p20/p50/p70/p90/p99 per plan. Every other view here reports a median, and a
median is structurally blind to a tail — which is where two real defects were hiding.

| plan | p10 | p50 | p90 | p99 | p90/p10 |
| --- | --: | --: | --: | --: | --: |
| GatheredScan | 0.42 | 0.95 | 1.86 | 5.77 | 4.5 |
| StreamedSelect | 0.42 | 1.07 | **21.26** | **59.74** | 50.4 |
| PrintingCompose | 0.64 | 0.96 | **inf** | **inf** | inf |
| PlanePopcountOrder | 0.33 | 0.92 | 1.11 | 1.18 | 3.4 |
| CardRangePopcount | 0.99 | **1.20** | 1.43 | **1.4** | 1.4 |
| PrintingRangeScan | 0.28 | 0.86 | 1.49 | 2.27 | 5.3 |

Read the SHAPE, not just p50. A tight spread (`CardRangePopcount`, 1.4) is a plain rate error worth
recalibrating. A wide one means the error depends on something unmodelled. A healthy middle with a bad
tail is a specific query class, not a general defect — and that is invisible to every other harness here.

Three things came out of it, all now fixed:

1. **`CardRangePopcount`, a uniform 1.20.** Only one of its five constants is not shared with
   `PlanePopcountOrder` (which is slightly UNDER-costed, so the shared ones cannot move). Retuning that
   one from 1.22 to 0.93 puts its whole p10–p90 inside the bar: 0.84 / 1.00 / 1.23.
2. **`StreamedSelect`'s p90 of 21x** — the small-total floor charged on queries that return at
   `total == 0` before reaching the gather. Zero-match queries take 0.62 µs and were estimated at
   ~35 µs. Fixed; tail p90 21.3 → 8.5, and it produced this work's first cleanly-attributable routing
   win, −0.166 µs CI [−0.341, −0.007].
3. **`PrintingCompose`'s literal `inf`** — `ComposePaging::Decline` on 20% of rows that actually RUN.
   The zero-total band (82% of them false) is fixed the same way, by mirroring an early return.

**Two of the three are the same defect class**: the cost model mirrored a branch, but an EARLIER return
reached first. Worth grepping for a third.

**And the metrics disagree, informatively.** Fix 2 improved routing significantly while making the
median-based agreement score slightly WORSE (0.1104 → 0.1273), because a per-cell median cannot see a
56x error on 4% of rows. Agreement alone is not sufficient; use it with the percentile view and a paired
routing A/B.

## `build_artwork_base` was rebuilt on every artwork query

Chasing compose's artwork arm (estimate/real 0.81, over-picked 38:1) found a constant ~11-12 µs
shortfall — unchanged across prediction bands while the estimate grew 14x, and absent from printing and
card. A fixed offset in one mode is not something a result-shaped term can absorb.

The cause was `printing_compose_fastpath` calling `build_artwork_base` per query: an O(n_cards) prefix
sum over `artwork_groups`, an input fixed at load. It was first PRICED (0.36 ns/card, which fixed the
estimate to 1.00) and then DELETED — precomputed into the archive as a stored index and read as a slice.
Deleting work beats modelling it, and the estimate stays at 1.00 without the term, which confirms the
offset was that pass.

It also unblocks two existing workarounds: the compose gather gate uses `cards.len()` as a conservative
stand-in for the artwork domain "because getting it exactly means building artwork_base here", and
`candidate_feats` falls back to the card count for unnarrowed artwork queries for want of a corpus
artwork total. `artwork_base.last()` is that total, now free.

## Sparse compose should gather, not decline — blocked on this arm

Extracted to its own doc: **[local-engine-sparse-compose-gather.md](local-engine-sparse-compose-gather.md)**.

One line, verified byte-identical over 127,640 queries, and blocked twice on routing (+0.445 µs, then
+0.377 µs after compose's artwork arm was corrected — which falsified the theory that mode-specific
mispricing was the blocker). It is listed here because its prerequisite is this document's subject:
the compose `Gather` arm cannot price the path, reading p10 0.14 on the population the change exposes.

It is also the sharpest illustration of a trap worth remembering: **a declining plan accumulates no
trials**, so those queries were absent from every measurement taken here. Enabling the path introduced
a population nothing had ever measured.

## The top target, found by both matrices agreeing — and what it exposed

Both matrices independently named one cell, which is the strongest signal this work produced:

- **cost matrix**: `StreamedSelect [printing_compose] / printing` at p50 **0.62**, p10 **0.09**,
  p90/p10 spread **142** — the widest cell in the engine.
- **regret matrix**: `StreamedSelect -> PrintingCompose` at **210 µs mean** over 124 queries, **21% of
  all routing regret**.

They are one defect: **97%** of that transition is that exact cell, and **70%** of all loss within the
cell is that transition. P3 priced at 62% of its real cost wins the argmin, and compose — which was
faster — loses.

### The costing fix

The compose acquire branch was passing verify tier `0` for printing mode, on the theory that compose
applicability implies a `tight` narrowing and therefore `all_match_known`. It does not. Against a plane
acquire, where the narrowing provably proves every candidate matches and P3's loop costs **5.11
ns/card**, compose-printing costs **23–42 ns/card** and **5–11 ns per printing scanned**. That is a loop
testing every printing, not the O(1) count `card_match_count` does under all_match.

With the tier at 0 the arm charged neither the residual floor nor its gated per-row term, so tens of
thousands of printings cost nothing. Charging it in every mode: p50 0.62 → **1.10**, spread 142 → 31.4,
and routing **−0.695 µs, CI [−1.466, −0.079]** — the largest routing win in this work.

The original evidence for the carve-out was `ns_loop / eval_domain` ≈ the all_match baseline. That
denominator is the ESTIMATE, which over-counts by up to 2x here; against the measured `cards_visited`
the ratio is 5–8x the baseline. **A feature error was hiding a costing error** — the fourth time on this
branch.

### The larger prize: P3 should not re-derive membership at all

Costing the work correctly is not the same as the work being necessary, and here it is not.
`into_card_space` drops tightness on projection — "some printing matches" does not imply every printing
of that card matches. That is right for card mode and needlessly lossy for **printing** mode, where the
plan operates in printing space and the composed `pbits` are already exact membership. P3 then
re-derives it by evaluating the full residual per printing.

Measured across 3,054 compose-acquired printing queries: **1,213 ms** of match-loop time over **145
million printings scanned**, at 6.98 ns each. An O(1) bit test would be ~145 ms — roughly **8x less**,
on the highest-regret query class in the engine.

`exec_card_range_popcount` already solves exactly this: it threads `range_pbits` alongside the card
bitmap so the shown printing can be membership-tested in O(1), for the same reason ("the shown printing
must actually be in range, not just belong to a card that has some in-range printing"). The compose path
needs the same treatment — carry printing-space membership through `PreparedCandidates` for
printing-mode compose, and have the materializing executors bit-test instead of re-verifying.

## Reproducing, and the rest of the toolkit

```bash
.venv/bin/python scripts/bench_regret_matrix.py --seconds 180          # what is worth fixing, by SHARE
.venv/bin/python scripts/bench_cost_error_percentiles.py --seconds 180 # the shape of that cell's error
.venv/bin/python scripts/bench_cost_error_attribution.py --seconds 400 # features / coefficients / shape
.venv/bin/python scripts/bench_cost_model_agreement.py --seconds 300   # the bar
.venv/bin/python scripts/fit_cost_model.py --seconds 300               # the rates (self-checks its mirror)
.venv/bin/python scripts/bench_plan_misselection.py --source distribution --out A.jsonl
.venv/bin/python scripts/bench_plan_misselection.py --compare A.jsonl B.jsonl   # the only real verdict
```

Which tool answers which question, how to read the two matrices, and the measurement traps this work
paid for one at a time:
**[reference-cost-model-measurement.md](reference-cost-model-measurement.md)**.

Related: [plan mis-selection](local-engine-plan-misselection.md),
[candidate materialization](local-engine-candidate-materialize.md).
