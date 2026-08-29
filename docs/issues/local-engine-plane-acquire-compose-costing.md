# PrintingCompose Miscosted Under the Plane Acquire Branch

Surfaced re-verifying [00852-engine-compose-acquire-p3-p4-ranking.md](00852-engine-compose-acquire-p3-p4-ranking.md)
after [local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)'s
Rounds 1-9 closed out that doc's `GatheredScan`/`StreamedSelect` pair (87%→97% ordered right). Base
branch for this work is `engine-cost-model-cleanup` (via the local `costcell/trunk`), same as that doc.

## Problem

`bench_pairwise_ordering.py` (both `--mode realistic` and `--mode uniform`) shows `GatheredScan vs
PrintingCompose` and `PrintingCompose vs StreamedSelect` as the two worst-ordered, highest-regret pairs
in the whole engine, concentrated specifically in the `plane`-acquire branch (a plane already exists —
legality/color/rarity/type-compiled predicates — and the router is deciding whether
`PlanePopcountOrder`, `PrintingCompose`, `GatheredScan`, or `StreamedSelect` is fastest):

| pair / acquire | n (realistic) | ordered right | mean regret | gap meas/pred |
| --- | --: | --: | --: | --: |
| `GatheredScan vs PrintingCompose` [plane] | 14,933 | 87% | 19.09 µs | 0.85 |
| `PrintingCompose vs StreamedSelect` [plane] | 14,967 | 92% | 11.42 µs | 0.75 |

(uniform sampling reaches worse tails: 83%/27.21µs and 86%/15.72µs respectively.)

## Root cause

`acquire_plan_features`'s `Plane` branch (`card_engine/src/lib.rs`, the first arm, `if
PhysicalPlan::PlanePopcountOrder.applicable(...)`) computes `count`/`scan_units` for
`PlanePopcountOrder`'s own cost, then returns `mk_plan_feats(ctx, params, count, count, scan_units, 0)`
directly — no further field assignments. Every OTHER acquire branch that reaches `PrintingCompose`-costing
territory sets `feats.broadcast_printings`, `feats.scatter_printings`, `feats.project_printings`,
`feats.popcount_words`, and `feats.compose_paging` explicitly after the shared `mk_plan_feats` call,
because `cost.rs`'s `PrintingCompose` arm reads all five to price its own build + page cost. Under
`Plane` acquire these all sit at `mk_plan_feats`'s defaults (0 / `ComposePaging::Gather`), which describe
nothing real about what `PrintingCompose` would do if chosen — it's a genuine alternative plan whenever
the plane-covered predicate (or its `unsplit` residual) is also printing-composable, not merely `plane`'s
own leftover bookkeeping.

Precedent: this doc's sibling `00852` already fixed the identical class of bug for a different acquire
branch (`compose_paging` left at its `Gather` default, measured 146x over-cost on `border:black ordered
by rarity` before `compose_paging_for` was made shared).

## Constraints

- **Pre-computation over hot-path computation** (same standing constraint every round in this repo —
  see `local-engine-cost-model-cleanup-remaining.md`'s "Explicitly considered and rejected" section for
  the specific 23.6x acquire-time regression precedent). Computing `PrintingCompose`'s real build cost
  under `Plane` acquire must not become an unconditional expensive pass paid by every plane-acquired
  query merely to price a plan that usually loses anyway — reuse whatever the `PrintingCompose` branch
  itself already computes cheaply (`compose_paging_for`, `broadcast`/`scatter`/`project`/`popcount_words`
  derivations), don't invent a new, separate computation.
- **What `PrintingCompose` would actually do when a plane exists needs tracing first**: does it reuse the
  plane's bits at all, or always rebuild from scratch? Does `printing_compose_applicable`'s use of
  `unsplit` (the residual filter once the plane-covered part is removed) mean a much cheaper build in
  the common case (little or nothing left to compose) — in which case the current all-zero defaults
  might be closer to right than they look, and the real bug could be narrower (e.g. only when `unsplit`
  is non-trivial)? Verify against real data before assuming the fix is "compute the full build cost
  always."
- **Primary success metric is `bench_pairwise_ordering.py`**, not `bench_cost_model_agreement.py` — per
  Phase 2's plan, this whole investigation is about routing/ordering accuracy for a specific plan pair,
  not absolute per-plan agreement.

## Current best

Not yet started — this is the Round 12 target. No code shipped.

## Iteration ledger

| # | Idea | Outcome | Pair result | Notes |
|---|------|---------|--------------|-------|
