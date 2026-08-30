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

## Follow-up round: root cause found, fixed (correction to this doc's own title)

Picked this doc up and answered the three open questions with real data before touching code, per this
round's brief. Correction up front: **the title's "card-invariant existential leaf" is a contradiction in
terms, and `border` is not one** — see Q2 below. `border` (and `rarity`) are printing-VARYING, which is
*why* they are existential. The bug is exactly that the router's tier logic treated them as if they were
card-invariant like `cmc`/`color`/`type`/`devotion`.

### Q1 — does the mis-route need the `And` with `border:black`?

Yes. Ran `explain_analyze` on bare `cmc>=1 cmc<=5` alone (same `unique=card`, several `orderby`/`limit`/
`offset` combos) against the real corpus:

```
cmc>=1 cmc<=5            orderby=rarity desc limit=175  -> GatheredScan picked, median 184,208 ns
                                                            PrintingCompose median 542,292 ns (NOT picked)
```

`GatheredScan` really is ~3x faster here and the router picks it correctly. Adding `border:black` blows
`GatheredScan`'s REAL time up ~6x (184,208 -> 1,155,375-1,290,166 ns) while its *predicted* cost barely
moves (327,291 -> 326,263 — `PlanFeatures` even reads slightly cheaper). The `And` is load-bearing: this
is not a bare-range problem.

### Q2 — where does `residual_tier_ns100` actually get set to 0, and is the classification wrong or is the bug elsewhere?

Traced it exactly. `cmc>=1 cmc<=5 border:black` under `unique=card`: **both** `cmc>=1`/`cmc<=5`
(`compile_numeric_cmp`) and `border:black` (`compile_border_cmp`) compile into `PlaneExpr`s
(`planes.rs::compile_plane`), and `split_planes`'s whole-filter shortcut folds the entire `And` into ONE
plane, leaving the residual `filter == FilterExpr::True`.

`acquire_plan_features`'s `PrintingCompose`-acquire branch then asks `plane_leaves_nothing_to_verify`:

```rust
fn plane_leaves_nothing_to_verify(filter, mode, plane, indexes) -> bool {
    matches!(filter, FilterExpr::True)
        && plane.is_none_or(|expr| {
            matches!(mode, Mode::Card) || !plane_expr_is_existential(expr, divergent_formats)
        })
}
```

For `Mode::Card` this returns `true` **unconditionally**, regardless of what the plane actually contains.
Its own doc justifies this only for *legality*: "the card has some legal printing" is exactly what
`unique=card` wants, so the #667 carveout lets `Mode::Card` skip re-verifying a divergent legality format
per printing. But `plane_expr_is_existential` is not legality-specific — `planes.rs::
needs_printing_verification` says plainly: **"for rarity and border that is every leaf"** (unconditionally
existential, unlike legality's per-format `divergent_formats` gate). The blanket `matches!(mode, Mode::Card)
||` bypass does not distinguish "legality, existential only for one divergent format" from "border/rarity,
always existential" — it grants the SAME free pass to both.

**So: the classification is a real bug, not a borrowed proof.** It is not "correct classification, bug
elsewhere" — `plane_leaves_nothing_to_verify`'s own Mode::Card carveout is unsound whenever the plane
touches rarity or border, and that unsoundness is exactly what leaks into `tier`/`residual_tier_ns100`.

A second, independent instance of the same conceptual bug turned up while verifying the fix: even after
correcting the plane-side check, `t:swamp tou=5 border:black`/card still read `residual_tier_ns100 == 0`,
because the OTHER disjunct, `compose_leaf_nothing_to_verify(filter)`, fires whenever the *residual* is a
bare safe collection leaf (`t:swamp`) — correct on its own terms (subtypes really are card-invariant) but
blind to what the `plane` alongside it contains. The original code OR'd two whole-query claims
(`plane_leaves_nothing_to_verify(filter, mode, plane, ..) || compose_leaf_nothing_to_verify(filter)`) when
what was needed was an AND of two HALF claims (filter side AND plane side each independently have nothing
left to verify).

### Q3 — what does the real executor do, and is the per-printing work genuinely necessary?

Traced `exec_gathered_scan`/`push_card_matches` directly (with temporary `eprintln!` instrumentation,
since reverted). `prepare_candidates`'s `all_match_known` is (harmlessly) `true` in both the border and
no-border cases — but a SEPARATE mechanism, `existential_plane_for` (gated on `plane_expr_is_existential`
with NO Mode::Card carveout at all), independently returns `Some` whenever the plane touches an existential
leaf, and forces `push_card_matches` into a per-printing loop that calls `eval_plane_expr_for_printing` on
each printing until one satisfies the FULL plane (both the constant `cmc` bit and the per-printing `border`
bit) — because `unique=card` still must return an ACTUAL border:black printing as the result row, not
merely prove one exists somewhere in the card's span. Confirmed against the real counters: `printings_examined`
(27,142) exceeds `cards_visited` (26,905) by 237 — cards whose first-checked printing didn't happen to be
black and needed a second look — proving the per-printing walk is real, not a costing artifact.

This work is genuinely necessary for correctness (unlike legality's carveout, which is a deliberate,
documented product decision that `unique=card` need not re-verify format legality per printing) — border
really can vary printing to printing for the same card, and the row returned has to actually match. The
cost model must charge for it; it cannot be modeled away.

### Q4 — how big is this population in real traffic?

Sampled `client.query_sampler.QuerySampler` in `realistic` mode: in 60s (31,111 queries, 7,928
`printing_compose`-acquire), 2,424 had `residual_tier_ns100 == 0` with `GatheredScan` picked, of which **61
(2.5%) touched `border`/`rarity`** — mostly common shapes like `r:rare t:plains`, `c:w r:mythic`,
`border:black t:shapeshifter`. This is not rare in the sense of "never happens" — rarity/border combined
with a type/color/keyword leaf is a completely ordinary real query.

But most of those 61 are SMALL-domain queries (a type/subtype leaf narrows hard), where the added tier
charge is a few µs against a query that was already single-digit-µs to begin with — spot-checked six of
them directly and `GatheredScan` remained correctly picked (and still fastest measured) both before and
after the fix. The specific sub-case that produces a *large*, `2x`-latency-class regret — a BROAD
card-invariant range (`cmc`/`power`/`toughness`) that leaves a large candidate domain, ANDed with
`border`/`rarity` — is a narrower slice of that population. This matches the flat aggregate
`bench_pairwise_ordering.py`/`bench_regret_matrix.py` numbers below: real, worth fixing (it's free and
correctness-preserving), but not a population large enough to move whole-corpus aggregates on its own.

### The fix

`card_engine/src/lib.rs`: added `plane_touches_rarity_or_border` (walks a compiled `PlaneExpr`, true iff
any leaf's plane index is `>= PLANE_RARITY` — rarity and border are the last two plane families,
contiguous through `PLANE_COUNT`, so an index compare identifies them exactly with no new table to keep in
sync with `planes.rs`'s private `PLANE_BLOCKS`) and `cost_plane_nothing_to_verify` (the plane-only half of
the check, with the `Mode::Card` bypass scoped to exclude a plane touching rarity/border). The `tier`
computation now ANDs the filter half and the plane half independently:

```rust
let filter_nothing_to_verify = matches!(filter, FilterExpr::True) || compose_leaf_nothing_to_verify(filter);
let nothing_to_verify = filter_nothing_to_verify && cost_plane_nothing_to_verify(mode, plane, indexes);
let tier = if nothing_to_verify { 0 } else { verify_cost_tier(composed) };
```

Deliberately scoped to ONLY this call site (the `PrintingCompose`-acquire branch's `tier` decision, the
one term the tracking doc's diagnosis identified as the actual bug). `plane_leaves_nothing_to_verify`
itself — used by the EXECUTOR's `all_match_known` in `prepare_candidates` — is untouched: granting
Mode::Card's bypass to rarity/border there is harmless (it only skips a redundant, already-cheap
`card_pass` call; the real per-printing correctness work runs through the wholly separate
`existential_plane_for` mechanism regardless of what `all_match_known` says). The other call site of
`plane_leaves_nothing_to_verify` (the `eval_domain`/`scan_units` broad-reset guard a few lines up) is also
left alone — a different concern (domain-size estimation, not verify cost) that this round's diagnosis did
not implicate.

**Pre-computation check**: the fix adds one small, bounded-size `PlaneExpr` tree walk (typically 1-5
nodes) once per acquire — no new per-candidate, per-match, or per-printing work, and no new index probe.
Cost is independent of corpus size, match count, or candidate count.

### Before / after (real corpus, original reproducer)

```
cmc>=1 cmc<=5 border:black, unique=card, orderby=rarity, direction=desc, limit=175, offset=0

BEFORE: GatheredScan     predicted 326,263 ns  picked=True   median 1,155,375-1,290,166 ns
        PrintingCompose  predicted 514,272 ns  picked=False  median   542,166-  636,958 ns

AFTER:  PrintingCompose  predicted 514,272 ns  picked=True   median   478,333-  511,166 ns
        GatheredScan     predicted 728,028 ns  picked=False  median 1,098,541-1,288,333 ns
```

Router now picks the actually-faster plan — a measured ~2.3-2.4x real latency win on this exact query.
`GatheredScan`'s revised `predicted_ns` (728,028) still under-charges the real 1.0-1.3ms (the tier's flat
`GATHER_RESIDUAL_FLOOR_NS`-based charge isn't calibrated for a per-printing existential walk specifically),
but the ARGMIN decision is what matters and it's now correct — no attempt was made to tighten the
absolute number further, since the primary metric for this round is ordering, not agreement.

### Correctness gate

`cargo test --manifest-path card_engine/Cargo.toml --release`: **168/168 passed** (167 pre-existing + one
new regression test, `compose_tier_charges_border_existential_and_arith_range`, added to `tests.rs` — a
minimal fixture reproducing the exact AST shape, asserting `residual_tier_ns100 > 0` for the `cmc`+`border`
`And` and `== 0` for the bare `cmc` range control). `cargo clippy --all-targets -- -D warnings`: clean.

### Confirmation pass

`bench_pairwise_ordering.py --seconds 60` (`GatheredScan` vs `PrintingCompose`, `printing_compose` acquire),
baseline vs fix, both uniform and realistic mode:

```
uniform:    baseline 86% ordered right, 5.47µs mean regret  ->  fix 86%, 5.51µs   (flat, within noise)
realistic:  baseline 90% ordered right, 3.32µs mean regret  ->  fix 90%, 3.24µs   (flat, within noise)
```

No aggregate movement either direction, consistent with Q4's population-size finding — the fix corrects a
real, narrow sub-population that doesn't dominate this pairwise slice's total regret. No regression.

`bench_cost_model_agreement.py --seconds 60`, `GatheredScan`/`card`: baseline median 0.81 (25% within 25%)
-> fix median 0.81 (25% within 25%) — unchanged, still PASS.

`bench_regret_matrix.py --seconds 120 --mode realistic`: baseline total regret 47.2ms over 52,384 queries
-> fix 45.2ms over 54,435 queries (~4% lower, more queries fit the same wall-clock budget because fewer
ran the now-corrected expensive misroute) — no regression, mild improvement, within this benchmark's
sample-to-sample noise band.

`bench_query_latency_ab.py --sample 400 --mode realistic --seed 7`, plus a same-build canary at the same
seed:

```
canary (baseline vs baseline):  B - A = -0.6µs  95% CI [-0.9, -0.4]   (noise floor)
baseline vs fix:                B - A = +0.1µs  95% CI [-0.2, +0.3]   NO DETECTABLE DIFFERENCE
```

Both indistinguishable from the noise floor — expected at n=400 given the affected shape's rarity (Q4).
No regression on general realistic-mode latency.

### Outcome

**Fixed.** Real bug (not a rare/skip-it case, not a borrowed-proof-elsewhere case), cheap to fix (no
hot-path cost added), shipped with a passing correctness gate and no detected regression on any
confirmation metric. The Phase A Q4 population size (rarity/border combined with something else is a
common query shape) argues this was worth fixing on principle even though it's invisible in whole-corpus
aggregates; the specific large-regret sub-case (broad card-invariant range AND rarity/border) is real and
now routes correctly.
