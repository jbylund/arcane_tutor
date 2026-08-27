# `PrintingCompose`/`Perm`'s cost model under-costs the walk by ~5x at the median

Filed as [#1025](https://github.com/jbylund/sylvan_librarian/issues/1025).

Found as a side effect of building a paging safety net for `Perm`'s Card-mode walk (see
[#730](https://github.com/jbylund/sylvan_librarian/issues/730) and
[local-engine-compose-perm-sigma-decision-rule.md](local-engine-compose-perm-sigma-decision-rule.md),
not yet landed on `main`), not while looking for it — worth its own doc because it's a real,
independently-shippable finding about `plan_cost`'s existing behavior, unrelated to whether that
other work ever lands.

## The finding

`cost::plan_cost`'s `PhysicalPlan::PrintingCompose` / `ComposePaging::Perm` arm prices the walk as
`printings_walked * COMPOSE_WALK_STEP_NS (0.58) + limit * COMPOSE_WALK_EMIT_PER_ROW_NS (2.19)`
(`card_engine/src/cost.rs`). Graded against real, now-cleanly-isolated `walk_ns` (see below for why
that's newly possible) on 2,705 real single-predicate `Mode::Card` `Perm` queries, offsets swept
0-25,000:

```
predicted / realized:
p0     p5     p10    p25    p50    p75    p90    p95    p100
0.018  0.074  0.106  0.149  0.182  0.269  0.439  0.594  2.447
```

**Under-costs by ~5.5x at the median**, and it isn't a deep-offset-only artifact — broken out by
offset, the ratio sits flat at 0.15-0.21 from offset 0 through 25,000. This is a real, directional
bias, not the "wide but centered" variance `WALK_LENGTH_BIAS`'s own doc already concedes.

## Why this wasn't visible before

`explain_analyze`'s `ns_loop` for `PrintingCompose` used to bundle compose-build time (`compose_
printing_bits` etc.) together with the paging walk as one undivided span — confirmed by reading
`card_engine/src/lib.rs`: `t_start` started before the compose call ran. That's a real confound for
grading the walk's own cost, and it isn't hypothetical — a first pass at this analysis read
~85-170µs at offset=0 even for single-predicate queries, an order of magnitude above any plausible
per-card walk cost, until it was traced to this.

**Fixed this session**: `ComposePageWork` now splits `ns_build` (the compose step) from `ns_paging`
(the walk alone), landing in `PhaseStats::ns_setup`/`ns_loop` respectively — the same setup/loop
split every other plan already uses. `plan_self_ns`'s total is unchanged (`ns_setup + ns_loop`
still sums to what `ns_loop` alone used to report), so nothing downstream broke, but a harness can
now read the walk's real cost in isolation for the first time. That split is what made this
measurement possible at all.

## Likely cause

`COMPOSE_WALK_STEP_NS` prices every printing touched at one flat rate, whether the bit-test finds a
match (and pays `prefer_score`) or not. A kernel bench that isolates the pure bit-test cost (all-zero
`pbits`, nothing ever matches) reads roughly half that rate. The real per-printing cost of a printing
that DOES match — scoring it — isn't small, and a rate fit against a population without enough real
matches in it would miss that entirely. Not confirmed by a controlled experiment here, but consistent
with everything else this session found about `printings_walked`'s existing rates.

## Caveat

Measured on single-predicate queries only (`Shape(predicates=1)`) — that restriction was needed to
isolate the walk's cost from compose-build cost via `explain_analyze`, not because it's the
representative traffic mix `COMPOSE_WALK_STEP_NS` was presumably tuned against. Worth confirming the
same ratio holds on the full "realistic" multi-predicate mix before refitting, though a consistent
~5x miss across a 25,000-offset range on real queries isn't the kind of thing predicate count usually
explains away.

## What shipping this looks like

A refit of `COMPOSE_WALK_STEP_NS`/`COMPOSE_WALK_EMIT_PER_ROW_NS` against real `(printings_walked,
ns_paging)` pairs, now that they're cleanly separable — the same kind of natural-query rate
regression `fit_cost_model.py` already does for other plans, pointed at this one.

**Correction, this was wrong**: originally framed here as "independent of... this only touches an
existing cost-model constant... not new dispatch logic," implying it's safe to ship standalone.
[local-engine-p3-p4-joint-refit-vs-compose.md](local-engine-p3-p4-joint-refit-vs-compose.md) proves
that assumption false for exactly this shape of change: `PrintingCompose` sits in the same
`plan_cost` argmin as `GatheredScan`/`StreamedSelect`, and refitting any one of the three in isolation
— in either direction — shifts routing across all of them, regressing total regret even when the
isolated fix is individually correct. Raising `COMPOSE_WALK_STEP_NS` to its true rate (correcting the
undercost this doc measures) would make `PrintingCompose`'s `Perm` arm look pricier and lose to
`GatheredScan`/`StreamedSelect` more often, including where it's still actually the better choice.

The rate to use once this is safe to wire in is already known — see
[reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)'s
reconciliation: ~1.9 ns/card, ~0.32 ns/printing, cross-validated two independent ways. What's missing
is not the number but an accurate `cards_visited` **feature** to multiply it against for general
traffic — that doc's own conclusion is that this is still blocked on the same estimator gap gating the
P3/P4 joint refit, not a smaller follow-up.

## Related

- [local-engine-compose-perm-sigma-decision-rule.md](local-engine-compose-perm-sigma-decision-rule.md)
  — the work that surfaced this as a side finding.
- [local-engine-p3-p4-joint-refit-vs-compose.md](local-engine-p3-p4-joint-refit-vs-compose.md) — why a
  standalone refit of this constant isn't safe, and what a joint refit actually requires.
- [reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)
  — the rate this doc's fix should use, and the feature-estimation gap still blocking it.
- `card_engine/src/cost.rs` — `COMPOSE_WALK_STEP_NS`, `COMPOSE_WALK_EMIT_PER_ROW_NS`,
  `printings_walked`, `WALK_LENGTH_BIAS`.
- Branch `engine-compose-cards-visited-estimator` — where the measurement lives
  (`scripts/bench_compose_card_visited_safety_bound.py`'s production-cost-model grading section).

## Status

The enabling split (`ns_build`/`ns_paging`) is landing via
[#1009](https://github.com/jbylund/sylvan_librarian/pull/1009). The refit itself is measured but
**not fixable as a standalone next step**: it's gated on the same `cards_visited` feature-estimation
gap blocking the P3/P4 joint refit (see Related), not just on someone doing the regression.
