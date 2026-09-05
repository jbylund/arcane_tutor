# Stop flattening the estimate's two channels at consumer boundaries

`SpaceMeasure` carries two independent answers per space — `guaranteed` (the tightest PROVEN upper bound on the true count) and `estimate` (the best GUESS, documented as possibly undershooting). Every consumer boundary below the estimator flattens them to one scalar via `best() = min(estimate, guaranteed)`, and from that point on nothing downstream can tell which channel a number came from.

The rule for reading them correctly is real, correct, and written down at length — "soundness consumers read `guaranteed`, accuracy consumers read `best()`", `lib.rs:9378`. It is also **prose, not type**. Every call site has to be independently disciplined, forever, and the type offers no help. This doc proposes making it mechanical.

## The evidence that this is a defect class, not a style preference

Three findings, all from 2026-09-05, all the same root cause.

**1. `best()` reports a zero that no mechanism proved.** Measuring a proposed `total == 0` fastpath over 8,000 uniform-sampled queries found 4 where the acquire reported `matches == 0` and the executor returned rows. Tracing all four: `guaranteed` held every time, and the zero came entirely from `printing_estimate`.

| query | true | `card_guaranteed` | `printing_guaranteed` | `printing_estimate` | `matches` |
|---|---|---|---|---|---|
| `t:ferret usd>=0.29 usd<=14.62` | 1 | 1 | 1 | 0 | 0 |
| `set:drb pow>6` | 3 | 15 | 15 | 0 | 0 |
| `c:bu t:whale` | 1 | 17 | 35 | 0 | 0 |
| `set:phel cn<11` | 1 | 5 | 5 | 0 | 0 |

The estimator is behaving exactly as designed — `op=min_fold, mechanism=None`, an independence product flooring to zero, which the admission rule correctly bars from writing `guaranteed`. Round 59 has no hole. The damage is done by the flattening: a sound bound and an unsound guess become one number, and a consumer that needed the bound silently gets the guess.

**2. An unproven zero collapses all three spaces.** `matches` normally follows `unique=` correctly (`f:modern border:white` reports 978 / 3,117 / 1,501 per mode). On `c:bu t:whale` it reports **0 in all three modes**, while `card` best is 17 and `artwork` best is 19. The inference "no printings ⟹ no cards and no artworks" is sound in truth — it is the same cross-space argument that makes an empty-page fastpath safe — but it is being applied to the estimate channel, where the zero is not proven. A consumer that could see the channel could not have made this mistake.

**3. A known latent soundness read, parked because it could not be made safe locally.** `narrow_floor` reads `best()` and feeds it into the `guaranteed` channel (`lib.rs:12709-12710`). This is already on the queue as its own item, deferred with the note that it "stays latent and separate" — it is not currently biting the card channel at roots (0 of 27,459 measured). It is latent *because* of a numeric coincidence, not because of a barrier. Under the change proposed here it stops being expressible.

Finding 1 is a wrong-answer bug waiting for a consumer. Finding 2 is a live one. Finding 3 is one held off by luck.

## The end state

Consumers take the structured measure. The two reads get names that say which consumer they are, so intent is greppable and a review can see a soundness site reading a guess:

- `proven() -> Option<usize>` — the `guaranteed` channel. `None` means "no mechanism proved a bound", never zero. The only read licensed to authorise a short-circuit, an exactness claim, or anything written back into `guaranteed`.
- `routing_cardinality() -> Option<usize>` — today's `min(estimate, guaranteed)`. Clamping a guess to a proven ceiling is correct and stays in one place; open-coding this min at call sites is how Round 55's bug (a guess lowering a proven bound) happened, so the operation is kept, not deleted.

`best()` itself goes away as a name. The rename is the point: it reads like a default, and a default is what a soundness consumer reaches for without thinking.

The boundary that matters most is `mk_plan_feats` (`lib.rs:17216`), where each acquire branch passes `matches: u32` and the channel distinction is destroyed for the whole routing layer. `PlanFeatures.matches` is a bare `u32` (`cost.rs:91`).

## What this does NOT claim

It does not claim an estimate of 0 is wrong. It is a legitimate guess; truth may be 0, and routing legitimately runs on guesses. What is wrong is a consumer treating an *expected* zero as a *certain* one — cost terms scale on `matches`, so a predicted 0 prices a plan at nearly free, it wins the argmin, and then does real work. Only a `guaranteed` zero licenses assuming no work. Fixing the plumbing does not fix the pricing, and that is a separate item.

## Staging

Each stage is separately landable and separately measured. Stage 1 unblocks the empty-page fastpath ([local-engine-empty-page-priced-infinity.md](local-engine-empty-page-priced-infinity.md)) without waiting for the rest.

1. **Rename, no behaviour change.** `best()` -> `routing_cardinality()`, add `proven()`. Every one of the 23 `lib.rs` call sites classified in the commit message as soundness or accuracy. Must be a measured zero-delta on the estimate survey; if anything moves, a call site was mis-classified and that is the finding.
2. **Carry both channels to the routing boundary.** Widen what `mk_plan_feats` accepts. This is a hot path: needs a paired A/B isolating *this* change, per `.claude/rules/benchmark-methodology-review.md` — a measurement of a nearby change does not cover it.
3. **Re-derive the cross-space inference from `proven()` only.** Finding 2 should become unexpressible rather than fixed in place.
4. **Revisit `narrow_floor`.** Finding 3, which by then has no way to spell itself.

Finding the exact site of finding 2 is deliberately deferred: under stage 3 it either evaporates or becomes obvious, and locating it first would produce a patch against a boundary that is about to move.

## Measurement

`scripts/bench_empty_page_provable.py` reports the lie count directly and is the regression test for stage 1 — it must read 0 lies where it currently reads 4. Raw output for the current build: [measurements/2026-09-05-empty-page-provable-uniform.txt](measurements/2026-09-05-empty-page-provable-uniform.txt).

Stage 1's zero-delta guard is `scripts/nway_estimate_truth_survey.py --compare` on `predicted_matches` / `picked_plan` / `and_mechanism` / `count_source`.
