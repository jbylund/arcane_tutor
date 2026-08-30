# GatheredScan Under-Costed for a cmc-Range AND a Card-Invariant Existential Leaf

Found while looking for the highest-latency real queries in a 211k-query uniform sample
(`docs/issues/local-engine-gathered-scan-card-printing-varying-depth.md`'s benchmark corpus/protocol) and
checking whether the router picked the best plan on each. Not yet fixed — this is the starting point for
whoever picks it up.

## The miss

`cmc>=1 cmc<=5 border:black`, `unique=card`, `orderby=rarity`, `direction=desc`, `limit=175`, `offset=0`:
one of the 25 highest-latency real queries in the sample, and one of only two in that top-25 where routing
missed the best plan.

```
routed:  GatheredScan     1,155,375 ns  (measured)
best:    PrintingCompose    581,708 ns  (measured)
regret:    573,667 ns  (~2x)
```

## Reproducing

```python
from scripts import costbench
from api.parsing import parse_scryfall_query
engine = costbench.load_engine(pathlib.Path("benchmarks/bitplanes/corpus.jsonl"), pathlib.Path("<scratch>/store"))
kw = dict(filters=parse_scryfall_query("cmc>=1 cmc<=5 border:black"), unique="card", orderby="rarity",
          direction="desc", limit=175, offset=0, prefer="default")
acquire = engine.explain(**kw)["acquire"]
res = engine.explain_analyze(num_warmups=3, num_trials=15, **kw)
```

## Diagnosis: `PrintingCompose`'s estimate is fine here — `GatheredScan`'s is the one that's wrong

Reconstructed both plans' `predicted_ns` term-by-term from the real `acquire` feature dump and `cost.rs`'s
constants, and both formulas reproduce the reported `predicted_ns` almost exactly — so the feature values
below are trustworthy, not an artifact of a different bug in the reconstruction:

**`PrintingCompose`**: `broadcast_printings=181,706`, `project_printings=83,894`, `popcount_words=496`,
`compose_paging=OrderbyWalk` (`printings_walked=1,011`).
```
build = 181,706*1.93 + 83,894*1.93 + 496*1.07 = 513,139
page  = 1,011*0.58 + 175*2.19                = 970
total = 513,139 + 970 + 163.56               = 514,272   (reported: 514,272.07 — exact match)
```
Real measured trials: 542,166 – 636,958 ns. **Ratio ~1.13-1.2x — reasonably well-calibrated.** `broadcast_printings`
alone is 68% of this total, and it's driven entirely by the bare `cmc` range: re-querying `cmc>=1 cmc<=5`
alone (no `border`) reproduces the identical `broadcast_printings=181,706`, while `border:black` alone gives
`broadcast_printings=0` — confirming `border` reads a precomputed plane (cheap) and `cmc`'s own card-invariant
broadcast is the real, correctly-priced cost driver here, not a bug in `PrintingCompose`'s own arm.

**`GatheredScan`**: `eval_domain=24,734`, `scan_units=83,894`, `matches=24,543`, `residual_tier_ns100=0`
(i.e. "nothing to verify" — the `tier_ns > 0.0` gate in `cost.rs`'s `GatheredScan` arm never fires).
```
loop    = 24,734*3.88   = 95,968
scan    = 83,894*2.06   = 172,822
push    = 24,543*2.24   = 54,976
collect =    175*9.79   =  1,713
total   = 95,968+172,822+54,976+1,713+169.6 = 325,649   (reported: 326,262.98 — matches within rounding)
```
Real measured trials: 1,015,209 – 1,290,166 ns. **Ratio ~3.1-4.0x — this is the actual bug.**

If `residual_tier_ns100` were nonzero instead of 0 (charging `GATHER_CARD_PASS_NS + GATHER_RESIDUAL_FLOOR_NS`
per candidate, the formula's own floor for "there is something to verify"): `24,734 * (3.00 + 18.89) = 541,427`
additional ns → a would-be total of **867,076**, closing most (not all) of the gap to the measured range. This
doesn't prove the mechanism, but it's the single largest lever in the formula and the most likely place to
look first.

## Where to look

- `card_engine/src/lib.rs`, the `PrintingCompose`-acquire branch of `acquire_plan_features` (search for where
  `tier`/`residual_tier_ns100` gets decided — `verify_cost_tier_unproven`, `nothing_to_verify`,
  `compose_leaf_nothing_to_verify`, `card_invariant_domain_exact` are the names that came up investigating
  nearby rounds this session; none were traced against this specific shape). The question: for an `And` of an
  arith-tuple range (`cmc`) and a card-invariant existential leaf (`border`), does whatever proves "nothing
  left to verify" actually hold for `GatheredScan`'s own per-candidate pass, or is it borrowing a proof that's
  only valid for a different plan/mechanism?
- `card_engine/src/cost.rs`: `GATHER_CARD_PASS_NS` (3.00), `GATHER_RESIDUAL_FLOOR_NS` (18.89), and the
  `tier_ns > 0.0` gate in the `GatheredScan` arm of `plan_cost`.
- Cross-check against the exact-tightening machinery already built in `compose_printing_estimate` for
  arith+existential combinations (`compose_printing_estimate`'s `And` arm, `best_other`, `arith_tuple_count`,
  the ID-probe merge) — this may be a downstream consequence of one of those mechanisms correctly proving an
  exact CARD COUNT while something else incorrectly reads that as "no residual work at all" for `GatheredScan`
  specifically.

## Open questions (not resolved here)

- **Does the mis-route need the `AND` with `border:black`, or does bare `cmc>=1 cmc<=5` alone already
  mis-route?** Only the *feature* values were isolated (both give `broadcast_printings=181,706`), not full
  routing — worth checking before assuming the `And` combination itself is load-bearing.
- **Is this the same root cause as the other `printing_compose`-acquire miss in the same top-25**
  (`f:commander year>2003`, unique=artwork, a much smaller ~43,300 ns/~5% miss in the other direction —
  `GatheredScan` picked when `PrintingCompose` was actually 43µs better)? Not checked — could be the same
  `tier` classification issue manifesting in both directions, or two unrelated mechanisms.
- **Real-traffic size of this population.** Not measured — a natural next check is
  `bench_pairwise_ordering.py` sliced to this AST shape (arith-tuple range AND card-invariant existential
  leaf, `printing_compose` acquire) to see whether this is a rare edge case or a real regret contributor
  worth its own round.

## Related

- [local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md) —
  the session-long effort this was found during; its Rounds 1-9 fixed `domain_cards`/`eval_domain` accuracy
  for *printing-varying* range leaves, not `cmc` (card-invariant) — a different population from this one.
- [00852-engine-compose-acquire-p3-p4-ranking.md](00852-engine-compose-acquire-p3-p4-ranking.md) — the
  `GatheredScan`/`StreamedSelect` pair, resolved; this doc is the `GatheredScan`/`PrintingCompose` pair,
  still open.
