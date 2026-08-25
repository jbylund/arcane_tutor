# Engine: `estimate_cardinality` Produces Unsound Bounds on ~1% of Queries

Filed as [#1020](https://github.com/jbylund/sylvan_librarian/issues/1020).

`estimator::estimate_cardinality`'s own accuracy report (`estimator_accuracy`, written for #702 and
left in `card_engine/src/tests.rs`) asserts `unsound == 0` — the true count must always fall inside
`[c.lo, c.hi]` — and that assertion currently fails on plain `main`.

## Reproduction

```
cargo test --release estimator_accuracy -- --ignored --nocapture
```

Deterministic (seeded fuzz corpus + RNG), so this reproduces identically every run: **41 of 5,000**
synthetic queries violate the bound, against 6,000 fuzz-generated cards. Confirmed on `main` at
`a2fd4180` (#1013) directly — this is not something introduced by any in-flight branch. It's
`#[ignore]`-gated (the test's own doc comment calls it "NOT an assertion — a reporting tool," which is
stale now that it ends in `assert_eq!(unsound, 0, ...)`), so it never runs in `make test` or CI and has
presumably been silently broken for a while.

## What the violations look like

Two distinct shapes in the failures:

- **Single-predicate `name:` substring queries**, bounds too narrow on the low end — e.g.
  `truth=0 bounds=[4,4] filter=name:ashnod's`, `truth=3 bounds=[5,5] filter=name:snakeskin`,
  `truth=17 bounds=[19,19] filter=name:signet`. The bounds collapse to a single point (`lo == hi`) that
  disagrees with the true count outright, not just an over-tight interval.
- **`NOT(...)` queries**, where truth sits far *above* `hi` — e.g. `truth=5999 bounds=[0,5985]
  filter=NOT(name:glory)`, `truth=5992 bounds=[0,5986] filter=NOT(name:writ)`. These are near-universe
  counts (5,985-5,999 of 6,000 cards) that the estimator is bounding well below actual.

Both shapes point at `name:`/text-substring handling specifically — every violation logged in a sample
run has a `name:` leaf somewhere in the filter, either directly or under a `NOT`/`AND`/`OR`. No
violation was observed on the purely numeric/color/type leaf types.

## Scope note

`estimate_cardinality` is explicitly a heuristic, not exact — its own doc contrasts it with two exact
alternatives measured and rejected during the #1009 investigation (see
[local-engine-compose-perm-cards-visited-estimator.md](local-engine-compose-perm-cards-visited-estimator.md)).
Being approximate is fine; the bounds it publishes are supposed to be a hard ceiling/floor regardless,
which is the actual invariant broken here. Nothing currently in the router depends on
`estimate_cardinality`'s bounds for correctness (per that same investigation, it's used nowhere in the
routing-relevant path today) — this is a soundness bug in shipped code, not a live incident, but it
should not stay silently broken given the test exists specifically to catch it.

## Not yet investigated

Root cause is unknown — this doc only confirms and characterizes the failure, it does not diagnose the
`name:` text-leaf estimation logic itself. Worth checking whether this predates #1013/#1015's
`HybridTagIndex`/`layout:` changes or goes back further; a bisect wasn't run.

## Related

- `card_engine/src/tests.rs::estimator_accuracy` — the failing test, `#[ignore]`-gated.
- [done/00702-engine-plan-selection-layer.md](done/00702-engine-plan-selection-layer.md) — the closed
  issue this report was originally written for.
- [local-engine-compose-perm-cards-visited-estimator.md](local-engine-compose-perm-cards-visited-estimator.md)
  — confirms nothing in routing currently reads `estimate_cardinality`'s output.
