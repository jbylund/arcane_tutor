# The Unified Router: Target-State Pseudocode

Companion to [00702-engine-plan-selection-layer.md](./00702-engine-plan-selection-layer.md).
That doc argues *why* the scattered decision tree in `run_query` should become one
plan-selection layer and sequences the work; this doc pinned down *what the end state
looks like* so the incremental PRs had a shared target to converge on.

**Landed 2026-07-20** (`run_query_routed`, lib.rs). The organizing principle held:
**there is exactly one routing function**, and mode (card/printing/artwork) is a
*filter over the plan set* — never a branch in control flow. What actually got
deleted: the `CARD_ENGINE_PLAN_SELECT` toggle, the legacy `run_query` decision-tree
body (now a 4-line string→enum adapter delegating to `run_query_routed`), and the
`maybe_broad` `STREAM_MIN_MATCHES` *routing* threshold. What deliberately STAYED,
because they are not tree-routing thresholds: `STREAM_MIN_MATCHES` itself (now the
cost model's P3 small-total floor, `cost.rs`), the 7/8 narrowing cutoff and the
memoize gate (both inside `prepare_candidates`, shared by the router). Divergences
from this sketch, learned by measurement, are noted inline below.

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

**Divergence in the landed code (`run_query_routed`).** The clean
`applicable()`-filter-then-`count_source()` shape above did NOT land verbatim.
Because "how the discriminating count is obtained" is entangled with "which plan is
being considered", the count-source distinction couldn't be a separate helper — it
IS the branch structure. `run_query_routed` is three explicit cases instead:
  1. **Card True-residual + plane** — eval the plane once, reuse its bitmap; argmin{P2,P3,P4}.
  2. **Printing bare-range** — exact `k` from the range index (no materialization);
     argmin{P1,P3,P4}; P1 wins ⇒ run fastpath, else fall through to (3).
  3. **else** — `prepare_candidates` once; argmin{P3,P4}.
Each case builds a small stack-array plan list and runs the argmin inline, rather
than filtering a global `ALL_PLANS` by an `applicable()` predicate. The predicates
(`plane_popcount_order_applicable`, `printing_range_scan_applicable`,
`streamed_select_applicable`) exist and gate the cases, but as `if`-guards, not as a
data-driven filter. Whether the cleaner `applicable()` + `count_source()` decomposition
is worth pursuing (vs. the three cases being the honest shape of the problem) is an
open design question — see the discussion in #702.

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

## Cost-model calibration scope (FIXED — was prerequisite)

`plan_cost` was originally fit and validated for CARD mode only, under-predicting
printing/artwork P3/P4 by ~3× (= `n_printings/n_cards`) because `eval_domain` counted
CARDS while those plans scan all printings. **Fixed** via features-not-mode
(`PlanFeatures::scan_units`, see #702 "Is the cost model correct?"): the caller
populates scan counts in the plan's operating space, one mode-agnostic formula.
Printing fidelity 1.83×→1.50× (now on par with card), and the deep-printing routing
mispicks are gone. A 1200-query designed refit confirmed the constants are at the
identifiable ceiling (~1.4× absolute, ordering-correct); further tightening is blocked
by structural collinearity, not effort — so this is done, not deferred.
