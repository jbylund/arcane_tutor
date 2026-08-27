# `PrintingCompose`'s `plan_cost` Should Reflect `Perm`'s Cheaper Branch, Once Step 5 Validates

Follow-up from [local-engine-compose-perm-sigma-decision-rule.md](local-engine-compose-perm-sigma-decision-rule.md)'s
step 5 (wired, gated off: `CARD_ENGINE_COMPOSE_SIGMA_ENABLED` defaults false). Not started, and
sequenced behind that plan's steps 6-7 — recorded now so the idea doesn't get lost before then.

## The finding

Once step 5's decision is validated and actually enabled somewhere, `Perm`'s true expected cost is no
longer just `walk_grouped_page`'s — it's approximately `min(walk_ns, three_phase_ns)`, since the
executor dynamically escapes to whichever `should_use_three_phase` predicts is cheaper. `cost.rs`'s
`plan_cost` for `PhysicalPlan::PrintingCompose`/`ComposePaging::Perm` still only prices the classic
walk (`COMPOSE_WALK_STEP_NS`/`COMPOSE_WALK_EMIT_PER_ROW_NS`). Left unchanged, the top-level argmin
against `GatheredScan`/`StreamedSelect` will keep over-pricing `PrintingCompose` for exactly the
sparse/deep-offset population the three-phase escape hatch exists to rescue — routing away from a plan
that has quietly gotten cheaper for that population.

## Why it isn't a small addition

Computing that `min` at acquire time needs both branches' costs before any plan is chosen.
`sigma_bound`'s side is fine (`n_cards`/`matches` are already estimable there). `three_phase_cost_ns`
needs `set_printings` (`popcount(pbits)`), and `pbits` doesn't exist yet at acquire time — composing it
*is* the expensive step that only gets paid once `PrintingCompose` wins. Requiring the exact value
would mean paying compose to decide whether compose is worth paying.

## Why it isn't dead, either

`set_printings` is a "how many" quantity, not a "where in the permutation" one — it doesn't inherit
the EDHREC-clumping problem that sank `cards_visited`/`printings_walked` estimation
([reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)).
A cheap approximate estimate may genuinely be obtainable where the general `cards_visited` estimator
wasn't:

- Scale the existing sound card-space cardinality estimator (`estimator.rs`) by the corpus's average
  printings/card — the same technique `sigma_bound::predicted_walk_ns` already uses to turn a
  card-count bound into a printing-count one.
- Or reuse/adapt the acquire-time `printings_walked_pred` estimate already computed for other plans'
  cost terms.

## The trade to take deliberately

The goal is more accurate cost models everywhere, but "accurate" doesn't have to mean "exact" at every
layer. At the routing/argmin layer specifically, a cheaper, noisier estimate may be worth more than an
expensive exact one: unlike `sigma_bound`'s own safety-bound role — where a wrong call costs real query
latency, so conservativeness and tightness both matter — a proxy feeding an argmin comparison only
needs to get the ordering right often enough to move total regret in the right direction. Other
`plan_cost` terms already lean on estimates rather than exact values (`printings_walked_pred` itself is
one); a noisy `set_printings` proxy would not be a new category of risk, just a new instance of an
existing one. Worth explicitly measuring the accuracy/cost trade rather than assuming either extreme
(exact-but-blocked, or "too noisy to bother") without data.

## The trap to avoid

`min(A, B) <= A` always, so this change can only ever make `PrintingCompose` look cheaper — the exact
shape [local-engine-p3-p4-joint-refit-vs-compose.md](local-engine-p3-p4-joint-refit-vs-compose.md)
already found unsafe to do in isolation: refitting one side of an argmin, even correctly, made it
over-win against competitors it wasn't actually beating everywhere. Any implementation here needs the
same discipline that doc's own recipe demands — full regret-matrix transition breakdown, not just an
aggregate metric — before landing, not a term added casually alongside steps 6-7.

## Sequencing

Blocked on the sigma decision rule's own steps 6-7 landing first: pricing a branch that never fires
doesn't help, and validating a `set_printings` proxy's accuracy needs the real branch-usage data step
7's traffic validation produces.

## Related

- [local-engine-compose-perm-sigma-decision-rule.md](local-engine-compose-perm-sigma-decision-rule.md) — the decision rule this is a cost-model follow-up to.
- [local-engine-p3-p4-joint-refit-vs-compose.md](local-engine-p3-p4-joint-refit-vs-compose.md) — the joint-refit discipline this change would need to borrow.
- [reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md) — why `cards_visited` estimation is hard, and why `set_printings` is a different, possibly easier case.
- `card_engine/src/sigma_bound.rs` — `should_use_three_phase`, `predicted_walk_ns`, `three_phase_cost_ns`.
- `card_engine/src/estimator.rs` — the sound card-space cardinality estimator a `set_printings` proxy could scale.
