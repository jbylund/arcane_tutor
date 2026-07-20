# The Unified Router: Target-State Pseudocode

Companion to [00702-engine-plan-selection-layer.md](./00702-engine-plan-selection-layer.md).
That doc argues *why* the scattered decision tree in `run_query` should become one
plan-selection layer and sequences the work; this doc pins down *what the end state
looks like* so the incremental PRs have a shared target to converge on.

The organizing principle: **there is exactly one routing function**, plans are data
(each carrying its own eligibility gate and cost formula), and mode
(card/printing/artwork) is a *filter over the plan set* — never a branch in control
flow. When this lands, the 7/8 cutoff, `STREAM_MIN_MATCHES`, the memoize gate, the
`CARD_ENGINE_PLAN_SELECT` toggle, and the legacy `run_query` body are all deleted,
subsumed into `applicable()` + `plan_cost()`.

## Plans as data

The tree and the thresholds are gone. Each plan owns its eligibility and its cost;
adding a plan is adding a row here (compile-time-forced complete — see #702 "Keeping
costs/plans current").

```
enum Plan { PrintingRangeScan, PlanePopcountOrder, StreamedSelect, GatheredScan }

# Which plans could *correctly* answer this query. These predicates ARE the former
# tree's structural conditions, but now they gate eligibility ONLY — never speed.
# GatheredScan has no gate: it is the universal fallback and always in the set, so
# the set is never empty.
fn applicable(plan, q, mode) -> bool:
    match plan:
        PrintingRangeScan  -> mode == Printing
                              and q.filter.is_bare_range()       # single range pred
                              and q.order.aligns_with(range col) # walk == order-by
        PlanePopcountOrder -> mode == Card
                              and q.filter.reduces_to_plane()    # residual == True
        StreamedSelect     -> q.order.is_perm_backed()           # any mode
        GatheredScan       -> true                               # universal reference
```

The three modes collapse into this filter — there is no `if card … elif printing …`
anywhere. Card gets `{P2, P3, P4}`, printing gets `{P1, P3, P4}`, artwork gets
`{P3, P4}`, all by the same predicate evaluation.

## Cardinality estimation

Cheap, sound, per operating space. Bounds are a hard invariant (`truth ∈ [lo,hi]`);
`est` is best-effort. See #702 "Cardinality estimation" for the algebra.

```
struct Card { lo, est, hi }

# Recursion composes single-space triples (AND→independence, OR→Bonferroni,
# NOT→complement). Text uses trigram-min posting; border/devotion use plane
# popcounts; range uses the index's binary-search k. NO plane materialization.
fn estimate(filter, space) -> Card
```

## The count source (the load-bearing part)

This is the real design content, not the argmin. Getting the exact match count is
sometimes **free** (a byproduct of prep the chosen plan needs anyway) and sometimes
**speculative** (work the winner won't reuse). Estimate only when materializing would
be wasted work.

This is the lesson from the card-mode prototype: the naive "estimate → route →
execute" pipeline was ~15% slower because estimating meant a plane eval the executor
then *repeated*. The fix is to route on the exact count *when the prep is shared*, and
fall back to the estimator only when no shared prefix exists.

```
fn count_source(q, mode, plans) -> (Features, Option<Materialized>):
    if plans share a candidate-prep prefix that the winner will reuse:
        #  card+plane : one eval_planes → popcount = exact matches, keep bitmap
        #  residual   : one prepare_candidates → exact count, keep candidate list
        mat   = run_shared_prefix(q)              # done exactly once
        feats = features_from(mat)                # EXACT matches, no estimate
        return (feats, Some(mat))
    else:
        #  e.g. printing range: the discriminating feature is range-k, which the
        #  index gives for free; the P1 walk vs P4 gather don't share a prefix,
        #  so estimating is strictly cheaper than speculatively materializing.
        est   = estimate(q.filter, mode.space)
        feats = features_from(est)                # bounds-aware matches
        return (feats, None)
```

## The one router

Replaces `run_query`'s decision tree entirely.

```
fn run_query(q, mode, page):
    plans = [p for p in ALL_PLANS if applicable(p, q, mode)]

    # Trivial escape: no cost math when there's nothing to decide. Keeps the
    # sub-µs fast path free of estimator/argmin overhead — the queries the tree
    # handled for free must stay free.
    if plans == [GatheredScan]:
        return execute(GatheredScan, q, page, mat=None)

    (feats, mat) = count_source(q, mode, plans)

    best = argmin(plans, |p| plan_cost(p, feats))   # cost.rs, per-plan formula

    return execute(best, q, page, mat)              # reuses mat if the prefix ran
```

## Cost model

One formula per plan, constants fit on the real corpus ([cost.rs](../../card_engine/src/cost.rs)).
`argmin` cares about *ratios*, which is what makes P1's bad tail visible: the tree
took P1 unconditionally; here P1 competes and *loses* when its walk is pathological
(narrow range under a misaligned sort — the idea-1/idea-2 crossover, the founding
motivation).

```
fn plan_cost(plan, f) -> ns:
    match plan:
        PrintingRangeScan  -> (page_span / match_rate)·STEP + FIXED   # blind-walk tail
        PlanePopcountOrder -> matches·SCATTER + words·WORD + FIXED    # O(words) floor
        StreamedSelect     -> eval_domain·(MATCH+tier) + small_total_floor + FIXED
        GatheredScan       -> eval_domain·(VISIT+tier) + matches·PUSH + page + FIXED
```

## Two things that are load-bearing and non-obvious

1. **`count_source` is where the value is, not the argmin.** A CBO that estimates on
   every query taxes the fast paths (the plane eval that gets repeated). The design
   is free on those paths precisely because it routes on the exact count when the
   prep is shared, and estimates only when the discriminating feature (range `k`) is
   itself a free byproduct.

2. **The trivial-escape line is what preserves latency parity.** Most card queries
   have exactly one applicable plan after gating, or resolve fast enough that any f64
   arithmetic shows up. Short-circuiting when there's nothing to decide is what keeps
   the unified router from being a tax on the queries the tree handled for free.

## What each mode yields (measured 2026-07-20 — supersedes earlier hypotheses)

- **Card** — tie. Cost-routing reproduces the tuned tree's choices (the thresholds
  already sit at the cost crossovers). Value is structural, not speed. (A/B geomean
  1.010×, see #702.)
- **Printing (P1 vs P3/P4)** — tie, NOT the win once hypothesized. The tree's
  `range_too_broad_to_narrow` ratio already sits at the P1/P4 crossover; P1 wins broad
  ranges at *every* depth tested (P3/P4 pay a full O(n_cards) match phase). Measured:
  tree gold 54/54, `printing_range_route_probe`. The earlier "tree takes P1
  unconditionally → cost bails P1→P4" story was falsified — the tree *does* guard narrow.
- **Artwork** — tie, like card (only P3/P4 apply). Confirming A/B, not a headline.
- **The actual win — idea-1 vs idea-2 (a plan not yet in `applicable()`):** the tree
  can't pick idea-2 (range → printing existence bitmap → popcount-skip) because it
  isn't built (#656). idea-2 is offset-independent; idea-1 (P1) grows ~`(offset+limit)/
  match_rate`, so they cross at a depth that scales inversely with match-rate (offset
  ~500–2000 for low-selectivity broad ranges, deeper for high). This IS the cost-shaped,
  tree-inexpressible decision the whole effort was aiming at — measured in
  `idea1_vs_idea2_probe`, real but confined to deep-paged broad printing ranges. Adding
  idea-2 means a fifth `Plan` row (gate: `mode==Printing ∧ bare_range ∧ ¬aligned`) plus
  its cost formula — the "plans as data" test of this design.

## Cost-model calibration scope (must fix before this lands)

`plan_cost` is fit and validated for CARD mode only. In printing/artwork it
under-predicts P3/P4 by ~3× (= `n_printings/n_cards`) because `eval_domain` counts
CARDS while those plans scan all printings. A unified router that argmin's across modes
with today's constants will mispredict (it already flips 2 deep printing rows). The fix
is features-not-mode (see #702 "Is the cost model correct?"): the caller populates
scan/emit counts in the plan's operating space, keeping one mode-agnostic formula. This
is prerequisite work for the single `route()` above.
