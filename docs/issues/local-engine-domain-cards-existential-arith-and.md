# `domain_cards`/`eval_domain` Is Wrong for Arith-Range AND Existential-Leaf, and Now Has a Root Cause

## Round 22: the gate fix, shipped and validated

Round 21's sketch (below) turned into a fix. `compose_printing_estimate`'s `And` arm (`card_engine/src/lib.rs`,
~line 7845): the `else if !card_invariant.is_empty()` guard on `best_other`'s existential-leaf loop is now a
plain `else` — a lone existential leaf (no card-invariant partner at all) runs the SAME
`popcount_with_bits(Some(e))` loop a card-invariant-paired existential leaf already used, unmodified.
`popcount_with_bits` already handled an empty `card_invariant` vec correctly (it was never incapable of
answering this shape, just never *called* for it) — see Round 21's own diagnosis above, confirmed unchanged
on `costcell/trunk`@`cc17e031` before this round touched anything.

### Ingredients 2 and 3 from Round 21's sketch: investigated, not shipped

**Ingredient 2** (prefer a precomputed exact count over a fresh `eval_planes`+`popcount` for the scalar):
does not cleanly apply to the population this bug actually affects. The BITS from `popcount_with_bits` are
still needed regardless, for the arith-ID-probe merge a few lines below `best_other` — and for the exact
flagship shape (an arith-tuple leaf ANDed with the existential leaf), that merge is what produces the TRUE
joint intersection; the scalar from `popcount_with_bits` alone (before the merge) is only the existential
leaf's own bare count, not yet the answer this round needs. So the eval_planes call itself cannot be
skipped for the population that matters, and substituting the scalar alone (leaving bits untouched) would
only help a narrower, already-rare shape (zero arith children, `card_invariant` empty, one existential
leaf) that ingredient 3 would have covered for free anyway. Not shipped.

**Ingredient 3** (fold `folded.result.card` in as a `.min()` floor at the final struct construction):
investigated and explicitly NOT shipped, because the SAME idea was already tried once before, for the
retired `domain_hint` field, and reverted — see the existing code comment directly above the final struct
construction (`card_engine/src/lib.rs`, ~line 7941 as of this round): *"a single BROAD leaf's own card
count... is not a safe upper bound on the whole `And` unless `narrow_rec` would actually USE that leaf to
narrow."* The concern is not mathematical (a single leaf's own count IS always ≥ the true joint) — it is
that `.card`/`domain_cards` is consumed downstream as an estimate of what the real EXECUTION PLAN will
visit, and `narrow_rec` may decline a broad leaf entirely (the documented `broad_ok: false` precedent,
`border:black` at 87%). Folding in a broad leaf's own count without the breadth guard the retired
`domain_hint` used to carry risks resurrecting that exact, already-fixed bug. Given the primary fix
(the gate) is independently sufficient and validated (below), this round left ingredient 3 out rather than
risk it — a future round could revisit it WITH an equivalent breadth guard, but that is new scope, not a
"free" addition.

### Reproducers: before / after (real corpus, isolated release wheels, `costcell/trunk`@`cc17e031` vs this round)

`unique=card`, `orderby=rarity`, `direction=desc`, `limit=175`, `offset=0`, `prefer=default`:

| query | before `eval_domain` | before `cards_visited`/`eval_domain` | after `eval_domain` | after ratio |
|---|--:|--:|--:|--:|
| `cmc=1 border:black` | 4,893 | 0.624 | 3,052 | **1.000** |
| `cmc=1 border:white` | 2,756 | 0.113 | 311 | **1.000** |
| `cmc=1 r=mythic` | 4,710 | 0.021 | 101 | **1.000** |
| `cmc>=1 cmc<=5 border:white` | 2,756 | 0.641 | 1,766 | **1.000** |
| `cmc>=1 cmc<=5 r=mythic` | 4,710 | 0.391 | 1,842 | **1.000** |
| `cmc>=1 cmc<=5 border:black` | 24,734 | 1.088 | 24,734 | 1.088 (unchanged — already tightened via `arith_tuple_count`, a separate mechanism; the gate fix additionally now runs the arith-ID-probe merge here too, but border:black's near-100% selectivity means it doesn't move the number) |

Every reproducer Round 21 named — `cmc=1 border:black`, `cmc=1 border:white`, and a rarity case
(`cmc=1 r=mythic` / `cmc>=1 cmc<=5 r=mythic`) — moves to an EXACT `eval_domain` (ratio 1.000). The
two-sided flagship shape (`cmc>=1 cmc<=5 border:black`) was already in a defensible band via a different,
unaffected mechanism and stays there.

### Broader sweep: is the confound Round 20 hit now cleared, broadly?

Round 20's own blocker was precise: of 223 systematically-varied population-A rows (3 arith fields × 7
widths × 11 existential leaf values), only 13 (5.8%) landed within 15% of real `cards_visited` —
`border:black` was "the only leaf value tested where `eval_domain` is trustworthy." Re-ran the identical
sweep (3 fields × 13 widths × 11 leaf values, 393 successful rows) against this round's fixed wheel:

```
total rows: 393, within 15% of cards_visited: 351 (89.3%)   -- was 13/223 (5.8%) before this round

border:black        n=39  median 1.000  100% within 15% except width-13 outlier (82.1% — border:black's
                                                                                own near-universal count)
border:white/borderless/gold, r=common/uncommon/rare/mythic, f:oldschool:
                     n=39 each, median 1.000, 100% within 15%  -- every one of these was badly wrong
                                                                   (0.02-0.85) before this round
r=special            n=39  median 0.699  10.3% within 15%  -- STILL broken, but a DIFFERENT, already-known
                                                               bug (see below), not this round's gate
```

The gate fix clears the confound for every leaf value tested except `r=special` (0.012 bare selectivity,
325 of 31,724 cards). Traced directly: `r=special`'s `eval_domain` reads a FLAT 219 across `cmc<=1` through
`cmc<=9` (only changing once the range widens enough to include ALL 325 matching cards) — this is the
SAME, separately-documented gap Round 17/19/20 already flagged in passing ("`eval_domain` reads identical
across every range width for several minority existential leaf values... a gap in the
`card_invariant_domain_exact`/estimated-domain fallback"), not a symptom of the `best_other` gate this
round fixed. Out of this round's blast radius (a different mechanism, `acquire_plan_features`'s domain
fallback, not `compose_printing_estimate`'s `And` arm) — flagged here as still open, not chased.

### Pre-computation check: acquire-time cost, measured directly

The gate fix makes the SAME already-existing `popcount_with_bits`/arith-ID-probe-merge machinery run for
MORE query shapes than before (previously gated off whenever `card_invariant` was empty) — this is real,
measurable added cost for the newly-covered population specifically, not a new mechanism. Measured
directly (20 warmups, 200 trials, isolated release wheels, `explain_analyze`'s own `acquire_ns`):

| query | before (median) | after (median) | delta |
|---|--:|--:|--:|
| `cmc=1 border:black` | 709 ns | 42,083 ns | +41,374 ns |
| `cmc=1 border:white` | 709 ns | 6,125 ns | +5,416 ns |
| `cmc=1 r=mythic` | 1,000 ns | 6,583 ns | +5,583 ns |
| `cmc>=1 cmc<=5 border:white` | 4,833 ns | 26,500 ns | +21,667 ns |
| `cmc>=1 cmc<=5 r=mythic` | 5,125 ns | 28,166 ns | +23,041 ns |
| `cmc>=1 cmc<=5 border:black` | 4,959 ns | 92,791 ns | +87,832 ns |

This is NOT from `popcount_with_bits`'s `eval_planes` call itself (a fixed ~496-word bitmap AND, the same
small cost the mechanism has always paid when it ran) — traced to the arith-ID-probe merge a few lines
below `best_other`, now reached for this population for the first time. That merge's cost scales with the
arith-tuple leaf's own selectivity (`bare_numeric_field_ids`/`arith_tuple_ids` materializes one `Vec<u32>`
of matching card ids, then filters/sums over it) — `cmc>=1 cmc<=5` (77% of all cards) costs far more than
`cmc=1` alone (10% of all cards), matching the table above. This merge is NOT optional: it is what produces
the TRUE joint intersection (e.g. `cmc=1 ∩ border:white` = 311 cards, not border:white's own 2,059-card
bare count) — without it, the fix would only partially tighten `eval_domain`, not close it to exact.

Confirmed the added cost is SCOPED to the newly-covered population, not a general regression: four
unrelated queries that already had a card-invariant partner (so `best_other` already fired before this
round) show no measurable acquire-time change: `c:w cmc<=3` 667ns→666ns, `f:modern c:u` 7,250ns→6,833ns,
`t:elf` 500ns→542ns, `devotion:w c:u usd>5` 7,500ns→7,833ns (15 warmups/100 trials each; all within noise).

This is a real, bounded-but-non-trivial trade for a narrow, previously-mis-costed population — accepted
because (a) it reuses existing, already-designed-for-this-purpose machinery rather than adding anything
new, (b) it is invisible in whole-corpus aggregates (below), and (c) the population it fixes was previously
driving `eval_domain` off by up to 47x (`cmc=1 r=mythic`, 0.021 ratio), which is the more consequential
error for routing.

### Correctness gate

`cargo test --manifest-path card_engine/Cargo.toml --release`: **173/173 passed** (172 pre-existing + 1 new
regression test, `compose_and_arm_tightens_lone_existential_leaf_with_no_card_invariant_partner` in
`tests.rs`, reusing the existing `cmc_border_existential_fixture_store` fixture — asserts the AND's exact
card intersection (`Some(2)`) and printing span (`3`) directly via `compose_printing_estimate`, for the
specific "arith range AND one existential leaf, no other card-invariant leaf" shape. Confirmed the test
actually catches the regression by temporarily reverting the gate: fails with `left: None, right: Some(2)`
against the old `!card_invariant.is_empty()` guard, as expected). Every existing assertion unchanged.
`cargo test --manifest-path card_engine/Cargo.toml` (debug, with debug-assert tripwires): 173/173 passed.
`cargo clippy --manifest-path card_engine/Cargo.toml --all-targets -- -D warnings`: clean.

### Broader regression check (mandatory — this touches the same `And` arm Rounds 1-9 validated for a
### different population)

`bench_cost_model_agreement.py --seconds 300 --seed 0`, full table, baseline vs fix:

```
overall: 11/17 cells inside [0.8, 1.25]  ->  12/17 cells inside [0.8, 1.25]   (improved, not regressed)
GatheredScan/card:     0.84 (26% within 25%)  ->  0.86 (27% within 25%)       (moved toward 1.0)
GatheredScan/printing_compose: 1.18 (24%)     ->  1.22 (24%)                  (flat, within noise)
```

No cell flips from PASS to FAIL. One cell (`PrintingCompose`/`plane`) moves from 17% to 30% within-25%,
both still below the 80% pass bar — not a regression, a small improvement. Total sampled queries in the
same 300s window: 101,108 → 97,253 (−3.8%), consistent with the measured acquire-time cost above diluted
across the WHOLE uniform sample (most queries in the sample never touch this population at all).

`bench_pairwise_ordering.py --seconds 300`, `GatheredScan` vs `PrintingCompose`/`StreamedSelect`, both
modes, baseline vs fix:

```
realistic:  GatheredScan vs PrintingCompose      88% ordered right both, regret 11.90µs->11.89µs (flat),
                                                  gap meas/pred 0.91 -> 1.01  (moved to near-exact)
            GatheredScan vs StreamedSelect        97% both, 0.86µs->0.87µs (flat), 1.04->1.04 (unchanged)
uniform:    GatheredScan vs PrintingCompose        88% both, 7.27µs->7.03µs (flat), 1.03->1.06 (flat)
            GatheredScan vs StreamedSelect        95% both, 1.88µs->1.73µs (flat), 1.07->1.07 (unchanged)
```

`bench_regret_matrix.py --seconds 120 --mode realistic --seed 0`: baseline total regret 38.8ms over 50,549
queries (mean 0.77µs) -> fix 42.9ms over 52,944 queries (mean 0.81µs), +5.2% mean, comparable to prior
rounds' own sample-to-sample noise band (Round 15/16 reported similar ±0.5-4% swings from re-sampling
alone). One new single-query outlier (max regret 1,927.6µs in a `StreamedSelect -> PrintingCompose`
misroute category that existed in baseline too, just with a smaller max there, 95.3µs) — likely a
different rare query landed in the differently-sized random walk (same seed, but per-query timing
differences shift how many queries QuerySampler draws in the same wall-clock budget); no NEW misroute
category appeared, and the "picked -> best" breakdown's set of categories is identical before/after.

`bench_query_latency_ab.py --sample 400 --mode realistic --seed 7`, baseline vs fix, plus a same-build
canary at the same seed (sequential runs, not literally interleaved sub-second — see caveat below):

```
canary (base run 1 vs base run 2):  B - A = +0.4µs  95% CI [+0.2, +0.5]
baseline vs fix:                    B - A = +1.0µs  95% CI [+0.6, +1.7]
```

The fix's interval does not fully overlap the canary's, but the two are close (0.6µs apart) at a sample
size the script's own docs flag as noisy for cross-process comparisons at these defaults (n=400). Given
the affected population's rarity (Round 21: ~0.85-1.23% of `Mode::Card` queries by a rough regex proxy,
an over-count relative to the exact AST shape), a small, real, borderline-detectable aggregate effect this
size is consistent with the acquire-time cost measured directly above, not a red flag on its own.

### Step 5 (joint rate refit): blocker demonstrably cleared, refit itself deferred

Round 20's own recommended next step was explicit: fix `eval_domain` first, then the `GATHER_CARD_PASS_NS`/
`GATHER_RESIDUAL_FLOOR_NS`/leaf-count-rate joint refit becomes testable for real. The broad sweep above
confirms the blocker IS cleared for essentially the whole population Round 20 swept (89.3% of rows within
15% of `cards_visited`, up from 5.8%) — a real, load-bearing precondition for that future refit, not a
minor caveat.

The refit itself was NOT attempted in this round. Building it properly means reintroducing Round 19/20's
`count_plane_leaves`/`plane_extra_eval_leaves` plumbing as a clearly-separate addition, a standalone fit
script mirroring `fit_round20.py`'s design, and the SAME three-population (compound/bare/residual)
held-out validation discipline Round 20 used — each of those was itself a full round's worth of work in
Rounds 19/20, and both of THOSE rounds' negative results (the additive term overshoots the flagship
reproducer even with a correct mechanism, Round 19; the floor's original calibration already unevenly
absorbs part of the compound-leaf effect, Round 20) were about the RATE FIT itself, not about `eval_domain`
— clearing the `eval_domain` blocker does not by itself imply the rate refit will now succeed. Rebuilding
that machinery and re-running the fit deserves its own dedicated round rather than being compressed into
this one's remaining scope. Per this round's own brief: "if the correctness fix (1-4) works but the refit
(5) doesn't [get attempted], ship the correctness fix alone" — done, with the next round now unblocked to
attempt the refit directly against trustworthy `eval_domain` data.

### Commit

One commit on `costcell/22-existential-and-fix` (gate fix + regression test + fixture addition; no
`cost.rs` changes, since step 5 was not attempted). `git diff --stat costcell/trunk`: `card_engine/src/lib.rs`,
`card_engine/src/tests.rs`, this doc.



Round 20 of the `GatheredScan`-compound-plane effort
([done/local-engine-gathered-scan-undercosted-arith-existential-and.md](done/local-engine-gathered-scan-undercosted-arith-existential-and.md))
found that three rounds of rate-fitting against `cmc>=1 cmc<=5 border:black`-shaped queries all failed
for the same reason: `eval_domain`/`domain_cards` (the acquire-time card-domain estimate every
`GatheredScan`/`StreamedSelect` per-candidate term multiplies) is itself wrong by up to 14x for this
population, and every rate fit was measured against a corrupted ground truth. This doc is that round's
named next step — fix the domain estimate first — and finds the exact code responsible, not just its
symptom. It is the third appearance of the same general problem: combining two range/existential leaves
into one accurate card-domain count was also the blocker in
[#852](00852-engine-compose-acquire-p3-p4-ranking.md)'s item 1, and an independence-product family of
fixes for a related (but distinct) shape was proven a dead end in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)'s
Round 2.

No fix ships in this doc. Everything below was verified against the real corpus (`benchmarks/bitplanes/corpus.jsonl`)
via `engine.explain()`/`explain_analyze()` on an isolated release wheel built from `costcell/trunk`@`bb5798b2`
(Round 20's own commit), plus temporary `eprintln!` instrumentation in `compose_printing_estimate`/
`acquire_plan_features` (reverted before this commit — `git diff --stat costcell/trunk` shows only this file).

## Question 1: what type of queries are affected

**The population is precisely: `Mode::Card`, `PrintingCompose` acquire, an `And` whose children are some
mix of arith-tuple leaves (`cmc`/`power`/`toughness`) and exactly one printing-varying existential leaf
(`border`/`rarity`/a divergent-format `legality`), with no OTHER card-invariant leaf (color, non-divergent
legality, devotion, ...) present.** Every dimension below was swept directly.

### Fields and range width

All three arith-tuple fields (`cmc`, `power`, `toughness`) show the identical pattern — this is not
`cmc`-specific. Width matters, but not in the way "a single value is exact" would suggest: it interacts
with which side of the `And` a plain per-child `min` picks (see Question 2), not with narrowness alone.
Swept widths 1 (`cmc=V`), 3, 5, and the full interior range (13) against 11 existential leaf values; every
width shows the same qualitative failure for every leaf except `border:black`.

### Existential leaf families and values — the "near-universal" hypothesis, tested directly

Bare-leaf selectivity in this corpus (`Mode::Card`, fraction of `n_cards=31,724`):

| leaf | frac |
|---|--:|
| `border:black` | 0.989 |
| `border:borderless` | 0.110 |
| `border:white` | 0.065 |
| `border:gold` | 0.017 |
| `border:silver` | 0.000 |
| `r=rare` | 0.349 |
| `r=common` | 0.337 |
| `r=uncommon` | 0.324 |
| `r=mythic` | 0.083 |
| `r=special` | 0.012 |
| `f:oldschool` (the corpus's one divergent format) | 0.030 |

No rarity value is near-universal (max 35%), and the corpus's only *divergent* legality format
(`oldschool`, the only format where "existential" even applies — a non-divergent format like `modern` or
`commander` is card-invariant and never reaches this code path at all) is itself a minority value (3%).
**Border is the only family in this corpus with a near-universal value at all**, so the brief's "find a
near-universal value in a different family" cannot be answered with a second clean field+value pair from
this corpus's own data — but the mechanism itself (Question 2) was confirmed directly by construction
instead: adding a near-vacuous 99.8%-selective leaf from a *different* family (`f:commander`, non-divergent
legality) to a broken query flips the same code path on and fixes it, which is a stronger and more direct
test than a second natural near-universal value would have been. Selectivity of the *discarded* side, not
field identity, is confirmed to be the true driver — see Question 2.

Ratio (`GatheredScan.cards_visited / eval_domain`) across all 11 leaf values x 4 widths x 3 fields (33
rows, `cmc>=1 cmc<=5`-shaped and narrower/wider):

- `border:black`: 0.624 (width 1) to 1.268 (full range) — the only leaf that stays in a defensible band.
- Every other value (`border:white/borderless/gold`, `r=common/uncommon/rare/mythic/special`,
  `f:oldschool`): 0.02 to 0.95, monotonically worse (lower) as the leaf's own selectivity drops, and
  **always an over-estimate** (`eval_domain` too big) except for the degenerate `border:silver` case
  (0 corpus matches at all — `eval_domain=0` while `cards_visited>0`, a separate, minor edge case in the
  `range_too_broad_to_narrow`/zero-match interaction, not chased further here).

### Mode

Identical failure in `Mode::Printing` and `Mode::Artwork` — `eval_domain` for `cmc>=1 cmc<=5 border:white`
reads 2,756 in all three modes (`unique=card/printing/artwork`), and `Mode::Printing`/`Mode::Artwork` share
the exact same `domain_cards` computation `Mode::Card` reads (`acquire_plan_features`'s shared
`(eval_domain, scan_units)` tuple, keyed off `domain_cards` regardless of `mode`). Not card-mode-specific.

### Acquire branch

Confirmed acquire-branch-specific, matching Round 20's finding exactly: the same filter under
`orderby=name` (`Prep::Candidates`/`count_source=plane`, not `PrintingCompose`) reads `eval_domain` exactly
equal to `cards_visited` for all three test leaves (black/white/mythic), ratio 1.0 every time. The bug is
entirely a `compose_printing_estimate`/`acquire_plan_features`'s `PrintingCompose`-branch phenomenon, and
does not touch the `plane`/`Prep::Candidates` acquire's own (correct) domain computation.

### Real-traffic representation

A regex-based proxy over `QuerySampler` (40,000 draws each, `uniform`/`realistic`) matching queries that
mention both an existential-family leaf and an arith-tuple comparison anywhere in the text: 1.7-2.4% of all
sampled queries, ~0.85-1.23% in `Mode::Card` specifically. This over-counts the actual bug population,
because (per Question 2) adding *any other* card-invariant leaf — color, a non-divergent format, devotion —
cures it; a realistic query combining `f:modern c:w cmc<=3 border:black`-style leaves would not hit this
bug at all. Round 20's own, more rigorous measurement of the closely-related `plane_extra_eval_leaves`
population (60,000 combined draws, both modes) found **zero** naturally-sampled rows — this doc's
population is a superset of that one (it doesn't require the leaf-count feature, just the domain
corruption), but is still a narrow, hand-constructible shape rather than one `QuerySampler` reliably hits.
Confirms and refines Round 20's finding rather than contradicting it: real but rare, and rare specifically
because most real queries carry an incidental card-invariant leaf that happens to fix the bug as a side
effect, not because the AST shape itself is exotic.

## Question 2: the mechanism — confirmed root cause, not just a symptom

### The bug, precisely

`compose_printing_estimate`'s `And` arm (`card_engine/src/lib.rs:7680`) computes an exact joint card
count only through `best_other` (line 7840):

```rust
let mut best_other: Option<(usize, Vec<u64>)> = None;
if existential.is_empty() {
    if card_invariant.len() >= 2 { best_other = Some(popcount_with_bits(None)); }
} else if !card_invariant.is_empty() {
    for e in &existential {
        let candidate = popcount_with_bits(Some(e));
        if best_other.as_ref().is_none_or(|(c, _)| candidate.0 < *c) { best_other = Some(candidate); }
    }
}
```

`card_invariant`/`existential` are populated at line 7801 by filtering OUT every arith-tuple-eligible
child (`cmc`/`power`/`toughness` are excluded from both) and partitioning the rest by
`plane_expr_is_existential`. **For the flagship reproducer's own minimal shape — one or more arith leaves
plus exactly one existential leaf and nothing else — `card_invariant` is empty and `existential` has one
element, so *neither* branch of the `if`/`else if` fires: the `else if !card_invariant.is_empty()` guard
requires a card-invariant partner that a lone existential leaf does not need.** `best_other` stays `None`
for the rest of the function, so `exact_domain_cards` (and everything downstream: `est.result.card`,
`domain_cards`'s `is_and` tightening, `card_invariant_domain_exact`) never gets an exact answer — even
though `popcount_with_bits(Some(e))` (line 7818) works fine with an empty `card_invariant` vec; it is
never *called* for this shape, not incapable of answering it.

Confirmed directly with a temporary `eprintln!` at line 7852 (right after the `if`/`else if` block,
reverted before commit): for every one of `cmc=1 border:white`, `cmc=1 border:white f:commander`,
`cmc=1 border:black`, `cmc>=1 cmc<=5 border:black`, `cmc>=1 cmc<=5 border:white`, `cmc>=1 cmc<=5 r=mythic`
— `card_invariant.len()==0`, `existential.len()==1`, `best_other.is_some()==false`, in every case with no
OTHER card-invariant leaf in the query.

### Falsifiable test, run directly: does adding a card-invariant partner fix it?

Yes, cleanly, and the fix does not need the partner to be selective — a near-vacuous one works just as
well as a real one, which is exactly what the "gating bug, not an accuracy bug" diagnosis predicts:

| query | `card_invariant.len()` | `best_other` | `eval_domain` | `cards_visited` | ratio |
|---|--:|:--:|--:|--:|--:|
| `cmc=1 border:white` | 0 | false | 2,756 | 311 | 0.113 |
| `cmc=1 border:white f:commander` (99.8% selective, near-vacuous) | 1 | true | 309 | 311 | **1.006** |
| `cmc=1 border:white f:modern` (70.8% selective, a real constraint) | 1 | true | 127 | 127 | **1.000** |
| `cmc=1 border:black` | 0 | false | 4,893 | 3,052 | 0.624 |
| `cmc=1 border:black f:commander` | 1 | true | 3,044 | 3,068 | **1.008** |

The last row is the sharper point: even `border:black` — the leaf every prior round called "clean" — is
*not* actually well-estimated at width 1 (ratio 0.624) once you isolate the shape from the wider-range
case. It only reads "clean" for the `cmc>=1 cmc<=5`-shaped flagship reproducer specifically, and that
cleanliness comes from a second, *unrelated* coincidence (below), not from `best_other` firing — `best_other`
is confirmed `false` there too. Once ANY card-invariant partner is present, `best_other` fires and the
estimate becomes essentially exact for every leaf tested, `black` included.

### Why `border:black` looks clean anyway, for the specific `cmc>=1 cmc<=5` shape

`acquire_plan_features`'s `domain_cards_before_card` (line 12147) does not even read the `best_other`
path's output when it fires — it reads `est.candidate.printing`/`est.result.printing` instead:

```rust
let domain_cards_before_card = if est.candidate.printing == est.result.printing {
    est_cards
} else {
    calibrated_balls_into_bins(est.candidate.printing, n_cards as usize)
};
```

For a 2-sided range (`cmc>=1 cmc<=5`, two arith children), the *printing-space* value `result` gets
tightened by a **separate**, already-working mechanism (`arith_tuple_count`, an exact `#743` index scan
over 2+ arith children — unaffected by the `best_other` bug, since it never touches `card_invariant`/
`existential` at all) — but `candidate` never receives that tightening (`candidate` is deliberately the
untightened per-child `min`, "what narrow_rec actually leaves the alternatives to walk" per the function's
own doc). Confirmed via `eprintln!`: `cmc>=1 cmc<=5 border:black` has `est.candidate.printing=85,411` vs
`est.result.printing=83,894` (NOT equal), so `domain_cards_before_card` takes the `calibrated_balls_into_bins`
branch on the **untightened** 85,411, not the tightened 83,894. That untightened number is itself just
`min(cmc>=1's own printing count, cmc<=5's own printing count, border:black's own printing count)` — and
it happens to land close to the truth here purely because **whichever side the plain per-child `min`
discards is, for `border:black` specifically, close to 100% selective, so discarding it costs almost
nothing.** For every other leaf tested, the discarded side is a real minority constraint, and discarding
it is exactly the over-estimate measured in Question 1. This is the same "selectivity of the discarded
side" mechanism as the `best_other` gate, arrived at through a completely different code path — two
independent coincidences, not one robust mechanism, which is why `border:black` alone (width 1, no second
arith child) is *not* clean (ratio 0.624 above) even though the wider-range reproducer is.

### A third, free, already-computed ingredient the fold already carries and discards

Checked (via a second `eprintln!`, also reverted) whether the per-child fold that builds `folded` (line
7714, `children_estimates.iter().fold(...)`, `SpaceEstimate::min` at line 7417) already carries a
useful `.card` value before `best_other` ever runs. It does, for **border** specifically: `border`'s own
leaf arm in `compose_printing_estimate` calls `exact_result_total(filter, indexes, Mode::Card)` (which
hits `vt.border`, a precomputed exact 3-space per-value table — O(1)/O(log n), no bitmap) and returns it
via `ComposeEstimate::leaf_spaces`, so `folded.result.card` already holds `min` of every child's own exact
card count wherever one exists. Confirmed directly: `cmc>=1 cmc<=5 border:white` → `folded.result.card =
Some(2,059)`, exactly `border:white`'s own bare card-match count. **But this value is thrown away
regardless of whether `best_other` fires** — the final struct literal at line 7948,
`ComposeEstimate { result: result_space, exact_domain, ..folded }`, always sets `.card` from
`result_space` (built from `exact_domain_cards`, `best_other`'s output, `None` here), never falls back to
`folded.result.card` when that's `None`. This is free (no new probe, already computed today) and would be
a strict tightening (an individual child's own exact count is always ≥ the true joint intersection, so
`.min()`-ing it in can only help) — but it caps out at "the tightest single child's own marginal count,"
not the true joint intersection, so it is a partial complement to fixing `best_other`, not a substitute
for it. **Rarity does not currently have this same free ingredient**: its own leaf arm
(`compose_printing_estimate`, `NumericCmp{RarityInt}`) deliberately uses `ComposeEstimate::leaf` (not
`leaf_spaces`), leaving `.card`/`.artwork` at `None` — its own comment cites a documented, pre-existing
bug in `RangeCardCounts::distinct_cards` for **broad** comparisons (`r<=mythic` read 31,722 against a true
31,724). Whether that bug also affects a narrow `Eq` value like `r=mythic` specifically was not
re-verified here — flagged as open below, not assumed either way.

### Hypothesis, stated falsifiably, and the verdict

**Hypothesis**: `domain_cards`/`eval_domain` for this population is not "estimated," it is a plain `min`
over each individual child's own marginal count (via one of two independent code paths — `best_other`'s
gate, or `domain_cards_before_card`'s untightened `candidate` fallback), which silently discards whichever
side of the `And` the `min` doesn't pick — and the estimate reads as "accurate" if and only if the
discarded side happens to be near-100% selective (so discarding it costs little), regardless of which
family or field that side belongs to.

**Test**: constructed queries where the card-invariant partner is deliberately near-vacuous (`f:commander`,
99.8%) versus genuinely selective (`f:modern`, 70.8%) alongside a badly-broken leaf (`border:white`).
**Confirmed**: both restore `best_other` and make `eval_domain` exact (ratio 1.006 and 1.000
respectively) — the fix works whether or not the added partner narrows anything, because it is a *gating*
fix (does an exact joint popcount run at all), not an *accuracy* fix for an existing estimate. Also
confirmed the corollary: `border:black` itself is NOT reliably clean absent the second-arith-child
coincidence (ratio 0.624 at width 1, becoming 1.008 once a card-invariant partner is added) — refuting the
version of the hypothesis that would say "border:black is intrinsically well-modeled." It isn't; it's
lucky, twice, in slightly different ways depending on range width.

## What a fix would need to do (a sketch, not a design)

Two complementary ingredients, both confirmed as real by the data above, likely both wanted together:

1. **Drop the `!card_invariant.is_empty()` requirement in `best_other`'s `else if` branch** (line 7845),
   so a lone existential leaf (no card-invariant partner) still gets its own exact popcount via
   `popcount_with_bits(Some(e))` with an empty `card_invariant` vec — this is the direct fix for the
   *gating* bug, confirmed to work by the `f:commander`/`f:modern` natural experiment above (which works
   *because* it flips this exact gate, not because those queries are special).

2. **Prefer an existing precomputed exact count over a fresh `eval_planes`+`popcount`, where one exists**,
   rather than materializing a bitmap purely to get a scalar. `exact_result_total` already has `vt.border`
   (a direct O(1) 3-space lookup, confirmed used by `border`'s own leaf arm already) and `vt.legality`
   (`legality_totals_key`-keyed, same shape, confirmed to exist for legality too). Whether rarity has an
   equally safe equivalent for a narrow `Eq` value specifically (as opposed to the documented-broad-range
   bug in `RangeCardCounts::distinct_cards`) is unresolved — worth checking directly before relying on it,
   not assumed by this doc. The BITS (needed separately for the arith-ID-probe merge a few lines below
   `best_other`) may or may not need a fresh `eval_planes` call at all if a single leaf's compiled
   `PlaneExpr` already resolves to a direct slice of `indexes.planes.words[...]` — not confirmed here,
   worth a look before assuming the cheap-count path and the bits path have to be the same call.

3. **Stop discarding `folded.result.card`/`folded.candidate.card` at the final struct construction**
   (line 7948) when `exact_domain_cards` is `None` — a free, already-computed, strictly-safe `.min()`
   floor (an individual child's own exact count is always ≥ the true joint), covering border today and
   any other leaf whose own arm already populates `.card`, with zero new per-query cost. This is a partial
   complement to (1)/(2), not a substitute — it only ever tightens to "the best single child's own count,"
   never to the true joint intersection two-or-more constraints would give.

Whichever combination ships, the next round should re-run the exact `fit_round20.py` joint-refit protocol
Round 20 built (design fully specified in that doc, not checked in) — once `eval_domain` is trustworthy
across leaf values, the `GATHER_CARD_PASS_NS`/`GATHER_RESIDUAL_FLOOR_NS`/leaf-count-rate joint fit Rounds
19-20 couldn't validate becomes testable for real, on the same sample construction already built for that
purpose.

## Open questions / what's still uncertain

- **Does rarity's known `RangeCardCounts::distinct_cards` bug (documented for broad ranges) also affect a
  narrow `Eq` value?** Not re-verified here; `compose_printing_estimate`'s rarity leaf arm blanket-disables
  `.card` for the whole `NumericCmp{RarityInt}` family regardless of comparison operator, so this doc
  cannot tell whether that blanket is itself over-broad.
- **Legality's own `best_other` behavior for a divergent format was not separately re-verified past
  Round 15/16's existing fix** (`plane_expr_is_existential`) — this doc's data is entirely border/rarity;
  `f:oldschool` was swept in Question 1's ratio table but not independently traced through `best_other` the
  way border/rarity were.
- **Whether the arith-ID-probe merge's bits can come from a stored slice instead of a fresh `eval_planes`
  call** (ingredient 2's second half) is a real-cost question for whoever implements the fix, not answered
  by this investigation round.
- **The real-traffic frequency estimate (Question 1) is a rough regex proxy**, not an AST-level
  classification of "empty card_invariant" — likely an over-count relative to the exact bug population,
  for the reason stated (an incidental card-invariant leaf elsewhere in a real query cures it as a side
  effect). A precise count would need to instrument the actual gate, not query text.
