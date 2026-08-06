# Compose's popcount rate is 4.3x over, and correcting it alone makes routing worse

`COMPOSE_POPCOUNT_PER_WORD_NS = 1.07` is measurably wrong. It is also load-bearing: the other compose
rates were fitted with the error present, so they compensate for it, and changing it in isolation
moves nothing in the right direction. Implemented, measured two ways, reverted.

This doc exists so the next person to measure this rate finds the answer instead of re-deriving it,
and because the measurement produced two findings that outlive the reverted change.

## Why this term and not the others

Of the four compose build terms, this is the only one whose FEATURE is exact rather than estimated:

| term | feature | exact? |
| --- | --- | --- |
| `popcount_words` | `n_printings/64`, `n_cards/64` or `n_artworks/64` | **yes** — precisely the words the executor sums |
| `scatter_printings` | range-slice `k` | yes for a bare range |
| `broadcast_printings` | legality bits scaled by `n_printings/n_cards` | no |
| `project_printings` | `printing_matches` | no |

So a `pred/meas` gap on `popcount_words` can be attributed to the rate without a feature confound.
Everywhere else in this engine the rule has been "features before rates"; this is the one place it
inverts, which is why it looked like the tractable place to start.

The clean population is a **plane-composable filter under `unique=printing`** (`border:black`,
`r:mythic`, and `And`/`Or` of them). There `broadcast`, `scatter` and `project` are all 0, so
`popcount_words * rate` is the whole build charge.

## Measured

Fitted against realized counters (`popcount_words`, `printings_examined`, `limit`), weighted by
1/realized so RELATIVE error is the objective — an argmin compares plans, and an unweighted fit over
cells spanning 375 ns to 133 µs optimizes the expensive ones and ignores the rest. Six corpus sizes
0.5x-5x built by replication:

| population | n | ns/popcount word |
| --- | --: | --: |
| all OrderbyWalk cells | 936 | 0.2547 |
| the unclumped subset | 286 | **0.2487** |
| shipped | | **1.0700** |

**The agreement between those two rows is the evidence, not the value.** A "rate" fitted on this
population could easily be absorbing clumping instead: `orderby=rarity` ascending with an `r:uncommon`
filter grinds through every common before its first match, so `printings_examined` runs ~100x the page.
Splitting on that (`examined < 3 * page` against the rest) moves the per-entry walk rate **2.5x**, from
0.35 to 0.88 — and leaves the popcount rate at 0.25 either way. A number that survives a split that
large is a rate.

With the rates fitted and realized counters substituted, the arm is well specified: `pred/meas` p50
1.01, p90 1.10, flat 0.97-1.04 across the whole 10x axis. **Every bit of the remaining routing error on
this arm is the `printings_examined` estimator**, i.e. clumping — which is
[a paging-branch choice](./local-engine-compose-paging-cost-based.md), not a rate.

## Why it was reverted

Two independent instruments, both against the shipped 1.07 as baseline:

**Regret**, paired 120 s uniform traffic:

| seed | before | after |
| --- | --: | --: |
| 7 | 1.38 µs mean | 1.40 µs |
| 11 | 1.37 µs mean | 1.38 µs |

No slice improved, and `PrintingCompose -> GatheredScan` grew 46 → 50 rows: compose got cheaper, so it
won more often, including where it should not have. It did not recover the `GatheredScan ->
PrintingCompose` cases (375 rows, unchanged) because those lose by far more than the ~1.2 µs this
removes.

**Wall time**, paired 1,500 uniform queries: total +1.3%, p50 +2.5%, 234 queries >5% slower against 35
faster. Same direction, three runs, never better.

The mechanism is compensation. `COMPOSE_GATHER_CARD_PASS_NS` was raised to 13.22 on 2026-08-03 and
`COMPOSE_BUILD_PER_PRINTING_NS` fitted at 0.0835 — both on samples where the popcount term was already
over-charging by ~0.8 ns/word. Removing the over-charge without refitting them leaves compose
under-charged overall, and under-charging is what over-picks a plan. `COMPOSE_BUILD_PER_PRINTING_NS`'s
own doc warns about exactly this shape one level down: "not a refit".

## The methodology finding, which matters more than the constant

**Total and mean wall time have a ~9% run-to-run noise floor on this harness**, so no change worth ~1%
can be gated on them. Measured directly: the same build, run twice, 2,000 queries each, per-query
minimum over 5 rotating passes — total 186.7 ms then 203.9 ms, a 9.2% difference, with canary drift of
only 0.9-1.9%. 1,051 of 2,000 queries moved more than 5%.

That invalidates a class of measurement, including the +1.3% wall-time reading above — it is inside the
noise, so the honest statement about this change is "no win, possibly a small loss", not "1.3% slower".
Three runs agreeing on direction is what carries the conclusion, not any single total.

What does work at that precision:

- **Split the population by whether the change can touch it** and read the control. The eur/tix index
  showed 0.386 on the 56 queries it affects and **0.991** on the 1,944 it cannot, against a 9% total
  drift. The control ratio is what makes the win believable.
- **Per-query paired ratios**, not aggregates. A distribution of same-query ratios survives broad drift;
  a total does not.
- **Effects concentrated in the tail** are readable where broad shifts are not. 12x on 56 queries is
  visible through anything; 1% spread over all of them is not.

## If someone wants to fix this properly

It is a joint refit of the compose arm, not a constant change: `popcount_words` to ~0.25, and
`COMPOSE_GATHER_CARD_PASS_NS` / `COMPOSE_BUILD_PER_PRINTING_NS` / `COMPOSE_GATHER_*` re-fitted on the
same sample so the compensation unwinds together. `fit_cost_model.py` already fits the whole
`PrintingCompose` vector; the missing piece is a sample that covers all three paging branches with
realized counters, which is now possible for two of them and blocked on clumping for `Gather`.

**A second finding to fold in when that happens: `Perm` is missing a per-CARD feature.** Fitting
`(popcount_words, printings_examined)` against realized time describes `OrderbyWalk` at R² 0.9955 and
`Perm` not at all — `Perm` comes out with a **-1,126 ns intercept**, which is a model reporting that its
columns are wrong, not a fixed cost. Admitting `cards_visited` as its own column moves the intercept to
**-12 ns**, R² to 0.9825, and the per-printing rate from 1.4450 to **0.3135** — against `OrderbyWalk`'s
0.3057. So `COMPOSE_WALK_STEP_NS` really is one rate for one operation, and the apparent 4.7x
disagreement between the branches was `walk_grouped_page`'s per-card work (1.89 ns/card) having nowhere
to go. `plan_cost`'s `Perm` arm has no cards term at all today.

That also explains an older observation in
[local-engine-compose-walk-features.md](done/local-engine-compose-walk-features.md): `printings_walked /
cards_visited` graded 4.53 while `/printings_examined` graded 1.17. The model needs both columns, not a
choice between them.

## Status

Nothing shipped. The rate measurements are on the production corpus across 0.5x-5x; the revert is
justified by two instruments on two seeds. Not attempted: the joint refit, and the `Perm` per-card
feature (which needs an estimator for `cards_visited`, plausibly `printings_walked / printings_per_card`,
ungraded).
