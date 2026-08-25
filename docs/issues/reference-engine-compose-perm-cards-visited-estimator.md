# `Perm`'s `cards_visited` estimator: the named candidate fails, checkpoints pass, ANDs get a floor

[local-engine-p3-p4-joint-refit-vs-compose.md](local-engine-p3-p4-joint-refit-vs-compose.md) named
step 1 of "if someone wants to try this again" as building the `cards_visited` estimator
[local-engine-compose-build-rates.md](local-engine-compose-build-rates.md) left "explicitly
ungraded": `printings_walked / printings_per_card`. That candidate is now graded, and fails. A second
candidate — reusing this session's `WalkCheckpoints` machinery instead of a flat ratio — passes, but
only for the narrow single-bare-leaf population `WalkCheckpoints` covers at all. A third extends that
to `And`s of several such leaves, as a lower bound rather than a point estimate, and measurably tightens
the spread there too. None of the three is wired into `plan_cost` yet; this is still step 1 (features
before rates), not step 2.

## Candidate 1: `printings_walked / printings_per_card` — fails

Implemented as `cost::walk_cards_visited`, exposed (not charged) via `acquire_facts_to_pydict`, and
graded by a new `scripts/bench_feature_accuracy.py` table against the executed `cards_visited`
counter, Perm rows only:

| distinct-on | n | p50 | p90/p10 |
| --- | --: | --: | --: |
| card | 1,625 | 1.46 | 7.6 |
| artwork | 1,249 | 1.33 | 8.9 |
| printing | 1,023 | 0.96 | 14.7 |

Outside the [0.8, 1.25] agreement band at the median for two of three modes, and the spread is wide
everywhere. Expected: this candidate is `printings_walked` rescaled by a constant, so it inherits
`printings_walked`'s own EDHREC-order clumping error undivided — the exact failure mode
`WalkCheckpoints` was built to fix for `printings_walked` itself, just not yet extended to this
quantity.

## Candidate 2: `WalkCheckpoints`'s card-rank companion — passes, on its own turf

`WalkCheckpoints::positions[i]` already stores, per value and EDHREC direction, the printings-prefix
value at which cumulative matches cross a checkpoint. The card whose prefix crossed it has a rank in
that direction's permutation (`pos` in `build_walk_checkpoints`) that was computed and then discarded
— it IS `walk_grouped_page`'s own `cards_visited` counter at that point. Storing it costs a second
`u32` per checkpoint, no second build pass:

```rust
struct WalkCheckpoints {
    positions: [u32; 5],       // printings-prefix, existing
    card_positions: [u32; 5],  // NEW: card rank at the same checkpoint
    len: u8,
}
```

`ArchivedWalkCheckpoints::cards_visited_for(k)` interpolates it exactly the way `walk_length_for(k)`
already interpolates `positions`; both now share one `interpolate` helper. `cost::walk_cards_visited`
prefers this (`walked_cards_hint`, threaded through `acquire_plan_features` alongside the existing
`walked_hint`) and falls back to candidate 1 when there is no checkpoint.

Graded with a dedicated harness (`scripts/bench_compose_perm_cards_visited.py`) rather than
`bench_feature_accuracy.py`'s generic sampler — see below for why — over every subtypes/keywords/
oracle_tags value in the corpus, both EDHREC directions, 7 page depths bracketing the checkpoint
table, against the executed counter:

| population | n | p10 | p50 | p90 |
| --- | --: | --: | --: | --: |
| all | 457 | 0.84 | **1.00** | 1.09 |
| `card_subtypes` | 263 | 0.84 | 1.00 | 1.09 |
| `card_keywords` | 68 | 0.82 | 1.00 | 1.13 |
| `card_oracle_tags` | 126 | 0.81 | 1.00 | 1.11 |

p50 exactly 1.00, p10-p90 inside the agreement band on all three fields. p99/p100 run out to 3.7/9.0
— a handful of tail points not investigated further here (plausibly a checkpoint-density edge case at
very deep offsets); the median and bulk band are what this doc is claiming.

## Candidate 3: `And`-of-leaves lower bound — tightens the spread, doesn't close it

`WalkCheckpoints` only fires for a *bare* leaf, so any `And` of two eligible leaves (`otag:X
keyword:Y`) fell all the way back to candidate 1's flat ratio, with no checkpoint involved at all.
But each conjunct's own checkpoint is still informative: an `And`'s matches are a subset of every
conjunct's own matches, so the walk cannot have found `k` And-matches at rank `P` unless every
conjunct has *independently* found at least `k` matches by `P` too. That makes `max` over conjuncts'
own `walk_length_for(k)` / `cards_visited_for(k)` a genuine lower bound on the And's answer — not
tight (the intersection is sparser than any single leaf), but free, since it's the same per-leaf
tables with no new build step, and one-directional: a lower bound can only raise an under-counting
fallback toward the truth, never push a value further wrong.

`edhrec_walk_checkpoints_for` generalized to `edhrec_walk_checkpoints_in`, which collects every
top-level `And` conjunct's table (or the bare filter's own, unchanged, when there's exactly one — the
single-leaf case above is untouched by this). Two or more: `feats.walked_hint =
cost::printings_walked(&feats).max(printings_floor)`, and the same for `walked_cards_hint` /
`cards_floor` — called with the hints still `None`, so the fallback formula, not this branch's own
result, is what gets maxed against.

Graded on 207 real two-leaf `And`s (`--mode and-pair` in the same harness, restricted to the 60
most-frequent value per field — ANDing two rare tags almost always intersects to nothing and declines
before `Perm` ever runs), same build, same points, before vs. after this change:

| | p10 | p50 | p90 | p90/p10 |
| --- | --: | --: | --: | --: |
| before (flat fallback only) | 0.047 | 0.330 (UNDER-COUNTS) | 1.87 | 39.7x |
| after (with the And floor) | 0.270 | **0.897 (OK)** | 3.67 | **13.6x** |

Median moved from a 3x systematic under-count to right at 1.0; the p90/p10 spread tightened ~3x by
raising the bad low tail. p90 grew in absolute terms rather than shrinking: the floor is a `max` of
two leaves' own checkpoint values, and each of those already carries candidate 2's own small tail
noise (up to ~9x at p100, per that grading) — a `max` of two noisy things is noisier than either
alone. The single worst row (p100 = 31.9) is identical before and after: that one was already
over-counted by the fallback itself, which no floor can touch.

## Why a dedicated harness, not `bench_feature_accuracy.py`

Running the existing harness before and after wiring in `card_positions` produced **byte-identical
percentiles** on the `orderby=edhrec` / `printing` / `Perm` slice (p50 0.89, p90 3.68, both runs). Not
a coincidence: `edhrec_walk_checkpoints_for` requires a *bare* `CollectionCmp` leaf
(`t:X`/`keyword:X`/`otag:X` alone, not ANDed with anything), and the realistic/uniform query mix
`client/query_sampler.py` generates for `orderby=edhrec` traffic almost never produces that exact
shape — so `walked_cards_hint` was `None` for every sampled row in both runs, and candidate 1's
fallback formula is all either run ever measured. A direct probe (`otag:triggered-ability`,
`unique=printing`, `orderby=edhrec`) confirmed the checkpoint mechanism does fire and diverges sharply
from the flat ratio when it is reachable — the first ~9 cards in ascending EDHREC order are
reprint-heavy staples (Sol Ring, Lightning Bolt-tier cards), so 9 cards visited can already carry 653
printings examined, an average 73/card against a corpus mean of 3.1. That is real clumping, not a bug,
and it is exactly what a flat ratio cannot see.

The general lesson: **the same clumping that broke `printings_walked` under a flat rate breaks any
downstream ratio built from it, and the fix has to be validated on the population it actually covers**
— a generic random sampler that rarely reaches that population will silently report "no change"
whether the fix is right or wrong.

## The per-card rate: a kernel bench, not yet wired in

`walk_grouped_page`'s `Mode::Printing` loop body, reproduced directly (`compose_walk_kernel_costs`)
with `pbits` forced all-zero so nothing ever matches and no sort/emit work runs — the same isolation
`gather_artwork_kernel_costs` uses, just for the opposite reason (there, everything matches and
residual cost is removed; here, nothing does, and match-handling cost is removed). What's left is
exactly `printings_walked`'s bit-test cost and the candidate `cards_visited` term's per-step overhead,
over six EDHREC-order card ranges chosen to vary printings/card sharply (the same clumping this doc
measured directly: ~73/card at the very top of ascending order against a corpus mean of ~3), so a
two-term no-intercept OLS can separate the rates instead of hitting
`local-engine-compose-build-rates.md`'s multicollinearity problem on natural query data:

| run | a: ns/card (candidate `COMPOSE_WALK_CARD_STEP_NS`) | b: ns/printing (candidate `COMPOSE_WALK_STEP_NS` re-fit) |
| --: | --: | --: |
| 1 | 1.054 | 0.327 |
| 2 | 1.226 | 0.351 |
| 3 | 1.124 | 0.317 |
| 4 | 1.117 | 0.347 |
| 5 | 1.316 | 0.336 |
| **mean** | **1.17** | **0.336** |

The per-printing rate is tight (±5% across runs) and lands right next to two independent priors:
`local-engine-compose-build-rates.md`'s own regression (0.3135) and `OrderbyWalk`'s hand-fit rate
(0.3057) — three different methods converging on ~0.31-0.35 is what makes this one believable. The
per-card rate is noisier (±13%) and comes in at **~1.17 ns/card, not ~1.89 ns/card** — that doc's
number was fit against `printings_walked`'s *old* uniform-rate estimate, not this checkpoint-corrected
one, and a kernel isolating the walk directly is a different method from a regression over natural
queries, so the two numbers disagreeing by ~40% is a real discrepancy to carry forward, not noise to
average away.

## Reconciling 1.17 vs. 1.89: the natural-query prior wins, not the kernel

Two hypotheses for why the kernel (1.17) undershoots `local-engine-compose-build-rates.md`'s
regression (1.89), tested directly against real traffic rather than argued from first principles —
`scripts/bench_compose_walk_rate_regression.py`, `QuerySampler("uniform")`, `explain_analyze` (which
runs `PrintingCompose` and reports its counters regardless of whether `plan_cost` picked it — no
special "force" mechanism needed, that is what `explain_analyze` is *for*), filtered to rows where
`paging_taken == "Perm"`:

**Hypothesis 1 — build-cost confound.** `PrintingCompose` reports one undivided `ns_loop` span (build
+ page together), so a regression omitting the build columns could have `cards_visited` soak up
whatever build cost correlates with it. Tested by fitting the *same* population two ways, once with
just `(cards_visited, printings_examined, page_rows)` and once adding every build column the acquire
exposes (`broadcast_printings`, `scatter_printings`, `project_printings`, `popcount_words`,
`collection_broadcast_printings` — the last newly exposed for this check). **Refuted**: the naive fit
is useless on its own (R² 0.01, wrong-signed `printings_examined`) because an unweighted absolute-`ns`
objective lets a few expensive rows dominate — but once also weighted for RELATIVE error (dividing
every row by its own realized `ns` before fitting, the same fix `local-engine-compose-build-rates.md`
applied to its own fit and for the same reason: this population spans single-digit-µs to
hundreds-of-µs queries), the controlled fit lands at **median pred/meas 0.86, R² 0.46-0.54**, with
most build coefficients close to their independently-known values (`broadcast_printings` 1.99-2.08,
`project_printings` 2.01-2.03, both near the shipped 1.93) — and `cards_visited` at **1.81-2.03
ns/card, `printings_examined` at 0.31-0.34 ns/printing**, across two seeds. Controlling for build cost
did not move `cards_visited` toward the kernel's 1.17 — if anything it *confirmed* the natural-query
prior more precisely (0.31-0.32 lands right on `local-engine-compose-build-rates.md`'s own 0.3135).

**Hypothesis 2 — match-handling riding on `cards_visited`.** A card that actually matches also pays
`group_best`/`prefer_score`/sort work scaled by `matches_pushed`, not `cards_visited`; if the two
correlate across the sample, that cost could be leaking into the `cards_visited` coefficient. Tested
by adding realized `matches_pushed` as a further column. **Also refuted**: `cards_visited` barely
moved (1.86 → 1.90 on one seed, 2.02 → 2.02 on the other).

**Conclusion: trust ~1.9 ns/card and ~0.32 ns/printing, not the kernel's 1.17 / 0.34.** Two independent
confounds were tested and neither explains the gap, while the natural-query number reproduces cleanly
across two seeds and lines up with an independent prior fit in a completely different session. The
likelier explanation is a weakness in the KERNEL's own isolation: `pbits` forced all-zero makes every
`is_set` branch predictably false, which removes real branch-misprediction cost a genuinely mixed
match pattern pays — a synthetic microbenchmark being *too clean* is at least as plausible a failure
mode as a regression being confounded, and this session didn't have a way to test the misprediction
hypothesis directly (no perf-counter access in this harness). The kernel result is not deleted from
this doc because a refuted hypothesis and a corrected number are both worth keeping; the number to
carry forward is **~1.9 ns/card, ~0.32 ns/printing**, not the kernel's own line.

**Still not wired into `plan_cost`.** Doing so for real means touching the *shared*
`COMPOSE_WALK_STEP_NS`, which also prices `OrderbyWalk` — a plan this kernel says nothing about, since
`OrderbyWalk`'s own `cards_visited` (`resolutions`, not permutation steps) is a different quantity
this term was never meant to describe. Shipping a lower `COMPOSE_WALK_STEP_NS` alongside a new
`COMPOSE_WALK_CARD_STEP_NS` for `Perm` only, without re-checking `OrderbyWalk`'s own accuracy, is
exactly the "changed a rate that affects the whole argmin without checking regret" shape every
reverted attempt in this doc's parent shares.

## What this doesn't answer

- **Coverage.** `WalkCheckpoints` is unique=printing/EDHREC only, and the `And` floor only reaches
  top-level conjuncts on the three eligible fields — `Or`, nested `And`s, a mix with a non-eligible
  field, non-EDHREC sort, and card/artwork mode all still fall straight to candidate 1's flat ratio,
  unmeasured here because there is no realized-counter ground truth outside the checkpoint population
  to check them against. `Or` in particular needs the OPPOSITE composition (a union matches more
  often than any child, so it takes *fewer* cards, not more — a `min`-composed upper bound, not a
  `max`-composed lower one) and pushing an estimate down is the dangerous direction, so it isn't
  attempted here even as a follow-on.
- **The rate.** Reconciled to ~1.9 ns/card, ~0.32 ns/printing (natural-query regression, corroborating
  `local-engine-compose-build-rates.md`'s own prior; the kernel's lower 1.17/0.34 is the outlier — see
  above), but still not wired into `plan_cost`.
- **The 3-way refit.** Still blocked on both of the above, per
  `local-engine-p3-p4-joint-refit-vs-compose.md`'s own gating: refitting `PrintingCompose` against
  `GatheredScan`/`StreamedSelect` before its own feature shape is trustworthy just ships coefficients
  that compensate for the next gap, the same mistake `COMPOSE_POPCOUNT_PER_WORD_NS` was already making
  before this session started.

## A possibly better root fix

[local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md)
prototypes replacing (for `Mode::Card`, so far) `Perm`'s own walk with the scatter+popcount-skip
technique `PlanePopcountOrder`/`CardRangePopcount` already use, instead of estimating the walk's cost
more accurately. Measured, not just argued: a real crossover where the new approach wins by up to
2x+ at deep offsets and loses by up to 50x at shallow ones. If it pans out fully, it plausibly makes
most of the estimator work in this doc unnecessary — worth reading before sinking more time into
coverage extensions here.

## Status

`card_positions` on `WalkCheckpoints`, `cards_visited_for`, `edhrec_walk_checkpoints_in`, and
`cost::walk_cards_visited` are all implemented and passing `cargo test --release` (156/156, including
the new `compose_walk_kernel_costs`) and `cargo clippy --all-targets -- -D warnings` clean. **Not
called by `plan_cost`.** Note for whoever runs `compose_walk_kernel_costs` next: it needs
`ARCHIVE_FORMAT_VERSION` bumped past the missed bump this session's `card_positions` field should
have triggered — bumped here to `2026082304`, so a `real.store` built before that point must be
rebuilt (the header check will refuse it, not silently misread it). Four new/extended tools:
`bench_feature_accuracy.py` (candidate 1's grading, kept as a permanent regression check even though
it failed), `bench_compose_perm_cards_visited.py` (candidates 2 and 3's targeted grading, `--mode
single` / `--mode and-pair`), `compose_walk_kernel_costs` (the per-card/per-printing kernel fit,
`cargo test --release compose_walk_kernel_costs -- --ignored --nocapture`, now superseded by the
reconciliation below), and `bench_compose_walk_rate_regression.py` (the natural-query reconciliation,
`--mode uniform` by default).

Next: **~1.9 ns/card, ~0.32 ns/printing is the number to wire in, not the kernel's own 1.17/0.34** —
and when it does go in, `OrderbyWalk`'s own accuracy needs re-checking in the same pass, since it
shares `COMPOSE_WALK_STEP_NS` with `Perm` and neither the kernel nor the reconciliation regression says
anything about its `cards_visited` shape (`resolutions`, a different quantity). The relative-weighted
controlled fit's remaining wrong-signed coefficient (`popcount_words`, small and negative both seeds)
is worth a look before pasting anything, and a branch-misprediction explanation for why the kernel
undershot was never directly tested (no perf-counter access in this harness) — plausible, not proven.
Coverage gaps (nested `And`s, a same-shape `Or` upper bound used only as a diagnostic given the
under-pricing risk, non-EDHREC sort) are still open too.
