# StreamedSelect's 52 µs small-total floor is predicted from candidate cards, not matches

[#802](https://github.com/jbylund/sylvan_librarian/issues/802). Found in review of
[#797](https://github.com/jbylund/sylvan_librarian/pull/797) and deliberately left out of it — that
PR's scope is instrumentation, and this is a cost-model defect in the thing it instruments.

## The defect

`run_query_streamed` has two emit branches. The cost model predicts which one runs; the executor
decides again at run time; the two read different quantities.

The model ([cost.rs:320](../../card_engine/src/cost.rs#L320)):

```rust
let floor = if u64::from(f.matches) <= *super::STREAM_MIN_MATCHES as u64 {
    n_cards * STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS
} else { 0.0 };
```

The executor ([lib.rs:7030](../../card_engine/src/lib.rs#L7030)):

```rust
if total <= *STREAM_MIN_MATCHES { /* gather over 0..n_cards, then quickselect */ }
```

`total` is the summed per-card match count — **result rows**. `f.matches` is not. For a candidate
acquire, [`candidate_feats`](../../card_engine/src/lib.rs#L6443) builds the vector as
`mk_plan_feats(ctx, params, count, count, scan, tier)` where `count` is `candidate_cards.len()` —
**cards**. Two different quantities, one threshold, no check that the branch predicted is the branch
taken.

At `STREAM_MIN_MATCHES = 1_024` ([lib.rs:4398](../../card_engine/src/lib.rs#L4398)) and 1.65 ns/card,
the floor is **~52 µs on the 31,508-card corpus** — the single largest term in P3's arm. Getting the
branch wrong is a 52 µs error in whichever direction it goes.

## Both directions are reachable

**Over-cost, and this is the wide one.** Printing and artwork mode emit multiple rows per card.
#797's own `explain_analyze` output for `t:creature` reports 17,317 candidate cards against 45,976
printings scanned — 2.65 rows/card, against a corpus-wide 97,206/31,508 = 3.09. So a printing-mode
query holding roughly **331 to 1,024 candidate cards** is charged the floor and then takes the
permutation walk, which never pays it. Artwork mode has the same shape over a narrower band, since
groups-per-card sits between 1 and printings-per-card.

**Under-cost.** Card mode with an inexact narrowing has `total < candidates` — the `card_pass`
`continue` rejects candidates the narrowing could not exclude. A query above 1,024 candidates whose
real total lands at or below it takes the gather with no floor charged.

## Evidence it is live

The calibration sweep in
[`local-engine-plan-misselection.md`](./local-engine-plan-misselection.md) (not yet landed; branch
`engine-plane-popcount-cost`) puts `StreamedSelect`'s acquire-netted measured/predicted at **0.58
median, 0.18 p25** — systematically over-costed, which is the direction a spurious 52 µs floor
produces. That doc's item 3 currently defers it as a "13–33 µs `StreamedSelect`/`GatheredScan`
cluster ... until something shows it matters". This is a mechanism for it, and the branch report
below is what would show whether it does.

## Why it stayed hidden

The constant's own doc asserts the equivalence that fails:

> Only added when `matches <= STREAM_MIN_MATCHES`, the **exact condition** that routes P3 into that
> gather branch.

It is the exact condition in card mode, where `total ≈ candidates`. And card mode is what it was fit
on — the doc names the fit queries as `cmc>=15` / `o:annihilator` / `cmc==7`, all `card`, all
~52 µs = 31,508 × 1.65. The fit was clean because the mode it was fit in is the one mode where the
two quantities coincide.

`cost.rs` already documents the identical mistake once, in the same arm: `STREAM_CARD_PASS_NS`'s doc
records that the old lumped `STREAM_MATCH_PHASE_PER_CARD_NS` "was fit on CARD mode ... which the
lumped constant under-priced ~2× at ratio ~3.09". Same mode, same ratio, same arm, second instance.

## What to change

1. **Report the branch actually taken** — an `emit_branch` field on `PhaseStats`, published by
   `run_query_streamed` alongside the counters. This is the gap #797 closed for `PrintingCompose`
   (`compose_paging` predicted, `paging_taken` observed) applied one plan over, and it reuses that
   machinery wholesale. Cheap, and it is what decides whether steps 2 and 3 are worth doing.
2. **Predict from the quantity the executor tests.** The model needs estimated result rows, not
   candidate cards. `params.mode` and `scan_units` already carry what is needed to scale one into
   the other; no new feature is required.
3. **Re-fit the two emit constants, afterwards.** Both look mis-keyed on inspection and neither can
   be fit honestly while the branch prediction is wrong:
   - `STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS` is documented as "no filter work", but the gather branch
     re-runs `card_pass` and rescans `start..end` per matching card. One `n_cards` constant is
     absorbing three quantities that scale differently.
   - `STREAM_EMIT_PER_MATCH_NS` charges the walk on `matches`, but the walk terminates at
     `page.len() == limit` ([lib.rs:7065](../../card_engine/src/lib.rs#L7065)). The work is
     `page_span / match_rate` — which `plan_cost` already computes as `printings_walked` for the two
     printing-space plans and P3's arm simply does not use.

## Not this

- **Folding the emit-phase printing rescan into `printings_scanned`.** That counter is the
  match-loop term `scan_units` predicts; both materializing executors realize it identically, and
  #797's `materializing_plans_agree_on_the_counters_they_share` exists to keep it that way.
  `GatheredScan` has no second pass, so merging emit work in would make the two plans stop counting
  one quantity and break that test correctly. Emit work belongs in emit terms. The only change
  `printings_scanned` needs is a doc note that it is first-pass only.
- **Re-fitting the constants without fixing the prediction.** A constant fit across a mis-predicted
  branch averages two regimes and will not settle — which is the failure mode #797 was built to make
  visible rather than to keep re-fitting through.
- **Instrumenting this alongside the four fast paths.**
  [`local-engine-instrument-fast-paths.md`](./local-engine-instrument-fast-paths.md) (lands with
  #797) is scoped to plans that report nothing, and its prerequisite is per-acquire-branch feature
  semantics. P3 is fully instrumented and the prerequisite here is different.

## Verification

1. Land the `emit_branch` report and add a Rust sweep modelled on
   `compose_paging_prediction_matches_the_branch_taken` — same shape: predicted against taken, per
   mode, with a coverage guard so a sweep that reaches one branch cannot pass silently. Expect it to
   fail immediately in printing mode; that failure is the finding reproduced in-crate.
2. Add a disagreement table to `scripts/bench_cost_model_agreement.py` next to `report_paging`,
   keyed by mode rather than acquire branch, to size the rate over the real corpus.
3. Re-run the calibration sweep. `StreamedSelect`'s netted ratio should move toward 1.0 from 0.58,
   and its p25 from 0.18. If it does not, the floor is not the mechanism and this closes.

## Caveat for held numbers

The 0.58 / 0.18 figures were collected with the pre-#797 cyclic participant rotation, and
[#801](https://github.com/jbylund/sylvan_librarian/issues/801) changes the shuffle seed on top of
that. Re-run the sweep after both land before fitting anything. The direction should hold — a 5×
over-cost is not ordering drift — but the constants will move.
