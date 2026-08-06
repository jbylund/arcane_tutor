# The `And` Arm's Cost-Based Skip Regresses a Card-Space Posting Child 1.56×

Status: proposed, not diagnosed. Filed as
[#851](https://github.com/jbylund/sylvan_librarian/issues/851). The one open cell from the cost-based skip
that shipped in #836 (`3cfd441`), recorded in
[the `is:` / `frame:` audit](done/local-engine-is-frame-predicates.md).

## What shipped, and what it is worth

`narrow_rec`'s `And` arm already had the rule — `AND_PROBE_FLOOR`'s doc: *"a child with `k < best` becomes
the new, strictly-smaller driver (fewer residual verifications, never a regression)"* — but gated it on
`rank > 0`, and only rank-1 range children had a probe. Rank 0 was assumed cheap to materialise and never
checked, which is false for containment collections (`is:spell` is ~60k printing ids).

`probe_collection_k` gives collections the same cheap size probe, and the skip drops `rank > 0` in favour of
"we know what this child costs". Unprobed rank-0 children keep their benefit of the doubt.

Measured against a **back-to-back** baseline: total 0.969, p50 0.971, p90 0.964, p99 0.967, with
`keyword:extort frame:inverted` at **0.48×** and untouchable queries at 0.98–0.99. So the skip earns its
keep. This is the one cell it does not.

## The regression

`o:owner keyword:flying` reads **1.56×** baseline.

`keyword:flying` is a large **card-space** posting list. Card-space children were explicitly exempted from
the broad guard, with the reasoning recorded at the site: *"card-space lists need no guard — same argument
as `numeric_candidates`"*, because materialising card ids is cheap.

That argument covers the **cost of materialising** the child. It does not cover **skipping** one when the
alternative driver is an oracle-text scan — which is not cheap, and is what `o:owner` makes the driver here.
If that is the mechanism, the probe should apply to printing-space children only, and card-space lists
should keep narrowing regardless of their size.

## Two cautions before acting

**Re-measure on a quiet machine.** This cell sits close to the band where the audit's own untouchable
control queries read 0.98–0.99, and 1.56× is a single cell rather than a population. Machine drift has
misled this line of work before — canaries missed 22% drift once, which is why baselines here are taken
back-to-back with a control subset rather than against a stored number.

**The fix is a one-line gate either way.** The work is not the change; it is deciding which side of the gate
card-space lists belong on, and that needs the paired measurement rather than the argument above. Force the
skip off for card-space children and measure `o:owner keyword:flying` alongside the cells the skip currently
wins (`keyword:extort frame:inverted` at 0.48×) — a gate that fixes one and loses the other is not a fix.

## Related

- [done/local-engine-is-frame-predicates.md](done/local-engine-is-frame-predicates.md) — section 5, where
  the skip shipped and this cell was flagged.
- [done/local-engine-proven-conjuncts.md](done/local-engine-proven-conjuncts.md) — the other half of what
  made oracle-text drivers fast, and the reason a broad printing-space partner is no longer the suspect it
  used to be.
