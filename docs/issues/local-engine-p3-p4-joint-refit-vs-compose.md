# A correct P3+P4 joint refit still regresses routing, because PrintingCompose didn't move

After fixing a real feature bug (`scan_units`'s card-mode overcharge — see
[local-engine-card-residual-pass-rate.md](local-engine-card-residual-pass-rate.md) for its unfixed
sibling), `GatheredScan` and `StreamedSelect` were refit jointly on the corrected data — the exact
prerequisite [bench_streamed_loop.rs](../../card_engine/src/bench_streamed_loop.rs)'s own conclusion
asked for after its five earlier refit attempts all regressed. It still regressed, through a different
mechanism than any of those five. Implemented, measured, reverted.

## What was different this time

The five prior attempts (`bench_streamed_loop.rs`'s module docs) all failed because of **P3-vs-P4
compensating error**: the shipped rates were jointly tuned so that P4's over-estimate and P3's
over-estimate cancelled in their comparison, and refitting one or both broke that cancellation without
fixing what caused it. This attempt came after fixing the actual feature bug behind
`GatheredScan`/`candidates`'s over-prediction, refit both arms' shape/per-unit-rate terms together
(holding the two `*_FIXED_COST_NS` constants and two already-flagged noise-floor constants unchanged —
see the commit for which and why), and the P3-vs-P4 relationship specifically improved:
`plan_cost_model_matches_gold` went 97.7% → 98.9%.

## What regressed instead

Every plan that competes against `GatheredScan`/`StreamedSelect` in the same argmin, not just each
other. `PrintingCompose` was deliberately left unrefit — its `Perm` arm is missing a `cards_visited`
feature entirely (`local-engine-compose-build-rates.md`), so its rates cannot be safely refit until
that's built. Making P3/P4 cheaper without touching `PrintingCompose` made them win against it wherever
`PrintingCompose` was actually correct:

| transition | before (n, miss%, share) | after (n, miss%, share) |
| --- | --- | --- |
| `PrintingCompose -> StreamedSelect` | minor cell | **309, 99%, 32%** |
| `PrintingCompose -> GatheredScan` | minor cell | **93, 99%, 6%** |
| `printing_compose` acquire branch, total share | ~47-58% | **72%** |
| mean regret per query (uniform sample) | 0.50 µs | **0.56 µs** |

The mechanism is exactly [local-engine-compose-build-rates.md](local-engine-compose-build-rates.md)'s
finding in reverse: that doc made `PrintingCompose` cheaper in isolation and watched it over-win against
`GatheredScan`/`StreamedSelect`; this made the other two cheaper in isolation and watched them over-win
against `PrintingCompose`. Same failure, opposite direction, same root cause — refitting one side of an
argmin without the other.

## The generalization

**A joint refit is only as joint as every plan that competes in the same decision, not just the two you
happened to fix a feature for.** Fixing `GatheredScan`+`StreamedSelect`'s shared feature bug and
refitting them together was necessary but not sufficient — `PrintingCompose` sits in the same argmin for
every `printing_compose`-acquired query (a plurality of traffic; see its 47-72% share across every
sweep this session), and any rate change to the plans it competes against needs it in the same
refit-and-gate cycle. It cannot be, until its own feature gap closes first.

## If someone wants to try this again

1. Build the `cards_visited` estimator `local-engine-compose-build-rates.md` names as missing (it names
   `printings_walked / printings_per_card` as one candidate, explicitly ungraded). **Done, partially**
   — see [reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md):
   that named candidate fails grading, but a second one (reusing `WalkCheckpoints`) passes for the
   bare-collection-leaf/EDHREC/printing-mode slice of `Perm` traffic. Not wired into `plan_cost`, and
   the rest of `Perm`'s traffic still has no validated estimator.
2. Wire it into `PrintingCompose`'s `Perm` arm as a real `plan_cost` feature, not just a diagnostic
   column.
3. Grade it against the realized `cards_visited` counter the way `scan_units` is graded against
   `printings_examined`.
4. Only then refit `GatheredScan`, `StreamedSelect`, AND `PrintingCompose` together, gated on the full
   regret-matrix transition breakdown (not just the aggregate total, and not just the pair you fixed a
   feature for) — the aggregate total or a single pair's improvement is not sufficient evidence, as this
   attempt's own `plan_cost_model_matches_gold` improvement (97.7% -> 98.9%) demonstrates: that metric
   moved the right way while total routing regret got 12% worse.

## Status

Reverted. `card_engine/src/cost.rs` is unchanged from before this attempt. The fitted values (for
whoever picks this up once `PrintingCompose` has its feature fix) are recorded in
`bench_streamed_loop.rs`'s and `bench_gather_loop.rs`'s module docs' history tables, not repeated here.
