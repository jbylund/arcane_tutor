"""Two candidate SAFETY BOUNDS on `walk_grouped_page`'s (`Mode::Card`) worst-case `cards_visited`, built from the one thing EDHREC clumping cannot corrupt: the exact matching-card count `matches` (`M`), already computed before the `Perm` branch runs (`compose_paging_with_total`/the fastpath's own `compose_total_for_mode`) -- never a position estimate, which is exactly what clumping defeats.

Two closed-form anchors, no calibration needed for either:

    worst_case(N, M, k)     = (N - M) + k       -- every non-match clumped before the k-th match.
    uniform_mean(N, M, k)   = k*(N+1)/(M+1)     -- expected position if M matches were scattered with
                                                    NO clumping at all (order statistic of a random
                                                    M-subset of N slots).

The user's ask, in `reference-engine-compose-popcount-skip-topk-select.md`'s decision-rule discussion: a
pure worst-case bound is safe but leaves a lot of `walk_grouped_page` traffic needlessly routed to the
(slower, but bounded) three-phase fallback; a pure mean estimate is exactly the thing clumping already
broke (`reference-engine-compose-perm-cards-visited-estimator.md`). So: build BOTH of the following
candidates, sharing one signature `(n_cards, matches, k, knob) -> float`, and let real uniform-sampled
traffic pick between them:

    blend_bound(n_cards, matches, k, knob)  -- knob in [0,1]: linear interpolation between the two
                                                anchors above. knob=0 is the mean, knob=1 is worst case.
    sigma_bound(n_cards, matches, k, knob)  -- knob = how many std devs above the mean, under the SAME
                                                no-clumping random-placement model (closed-form negative
                                                hypergeometric variance, no simulation, no corpus fit).

Both are safety bounds: bigger is "more conservative" (routes more `Perm` queries to the fallback,
never wrong to do so), smaller is "tighter" (more `Perm` traffic gets to run its native walk). The
question this harness answers is not "which model is more ACCURATE on average" -- it's "at a matched
violation rate (the bound undershoots the REAL executed `cards_visited`), which knob/method gives the
smaller bound," using real queries from `QuerySampler("uniform")` (not the checkpoint-eligible/EDHREC
population `bench_compose_perm_cards_visited.py` targets -- this bound is meant to work for ANY filter
shape, since it never reads WHERE matches sit in the permutation, only how many there are) with an
explicit deep-offset sweep (real traffic is offset~0-heavy by design, `costbench.OFFSETS`, so it can't
supply the tail this bound exists for).

    .venv/bin/python scripts/bench_compose_card_visited_safety_bound.py --n-queries 400

"""

from __future__ import annotations

import argparse
import functools
import itertools
import json
import math
import pathlib
import random
import sys
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

percentile = costbench.percentile

# Direct port of `card_engine::sigma_bound::THREE_PHASE_BREAKPOINTS` -- see that module's own
# "Provenance and re-fitting" doc for what corpus/machine this was fit on, when to recalibrate, and
# the exact steps to regenerate it. No shared source between Rust and this file for this table; keep
# the two in sync by hand when the Rust side is re-fit.
THREE_PHASE_BREAKPOINTS: tuple[tuple[int, float], ...] = (
    (23, 666.0),
    (55, 666.0),
    (97, 708.0),
    (215, 875.0),
    (477, 1291.0),
    (963, 2250.0),
    (1960, 3292.0),
    (2924, 4083.0),
    (4885, 5375.0),
    (9733, 9291.0),
    (19356, 18333.0),
    (24577, 24041.0),
    (29453, 29667.0),
    (39101, 41791.0),
    (58725, 113208.0),
    (78417, 155083.0),
    (97812, 196791.0),
)


def three_phase_cost_ns(set_printings: int) -> float:
    """Piecewise-linear interpolation of `THREE_PHASE_BREAKPOINTS`.

    Direct port of `card_engine::sigma_bound::three_phase_cost_ns`. Clamped below the first
    breakpoint and above the last, same reasoning as the Rust original: extrapolating a two-regime
    curve past its measured range risks being wrong in either direction, and the last breakpoint
    already sits at the fitting corpus's `n_printings`, which `set_printings` can never exceed on
    that corpus.

    Replaces the earlier hand-fit `ThreePhaseModel` (a single `rate*matches + fixed` line, calibrated
    from an early kernel-bench of the not-yet-promoted prototype and supplied on the CLI) now that the
    real promoted implementation has been measured directly and validated (~50% of predictions within
    5% of true cost, ~90% within 10%, rarely worse than ~20-25% -- see the Rust module's doc).
    """
    if set_printings <= THREE_PHASE_BREAKPOINTS[0][0]:
        return THREE_PHASE_BREAKPOINTS[0][1]
    if set_printings >= THREE_PHASE_BREAKPOINTS[-1][0]:
        return THREE_PHASE_BREAKPOINTS[-1][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(THREE_PHASE_BREAKPOINTS):
        if set_printings <= x1:
            t = (set_printings - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    msg = "set_printings is bounded by the first/last breakpoint checks above"
    raise AssertionError(msg)


# The PRODUCTION cost model's own `PhysicalPlan::PrintingCompose` / `ComposePaging::Perm` formula
# (`card_engine/src/cost.rs`): `printings_walked * COMPOSE_WALK_STEP_NS + limit * COMPOSE_WALK_EMIT_
# PER_ROW_NS`. Copied here, not imported -- there's no Python binding for `cost::plan_cost` itself, but
# `printings_walked` (its main input) is already exposed via `acquire["printings_walked"]`, so the
# model's OWN prediction can be graded against real `walk_ns` directly, using the ACQUIRE-time value
# the router actually sees (not a recomputation with hindsight).
PROD_COMPOSE_WALK_STEP_NS = 0.58
PROD_COMPOSE_WALK_EMIT_PER_ROW_NS = 2.19


def prod_cost_model_ns(printings_walked_pred: float, limit: int) -> float:
    """The PRODUCTION cost model's own prediction (`cost::plan_cost`'s `PrintingCompose`/`Perm` arm), for grading against real `walk_ns`."""
    return printings_walked_pred * PROD_COMPOSE_WALK_STEP_NS + limit * PROD_COMPOSE_WALK_EMIT_PER_ROW_NS


NS_PER_US = 1_000
NS_PER_MS = 1_000_000


def fmt_ns(ns: float) -> str:
    """Human units, fixed width -- raw nanosecond counts (`606542`) are hard to eyeball at a glance."""
    if ns < NS_PER_US:
        return f"{ns:.0f} ns"
    if ns < NS_PER_MS:
        return f"{ns / NS_PER_US:.1f} us"
    return f"{ns / NS_PER_MS:.2f} ms"


# `walk_grouped_page`'s own real cost, unlike three-phase's, IS directly observable now: `explain_
# analyze`'s `ns_loop` for `PrintingCompose` used to bundle compose-build time in with it (confirmed by
# reading `card_engine/src/lib.rs` -- `t_start` started before `compose_printing_bits` ran, and
# `ComposePageWork` published them as one undivided span), which is what produced a nonsensical
# ~85-170us reading at offset=0 even for single-predicate queries in an earlier version of this
# analysis. Fixed at the source (this session, `card_engine/src/lib.rs`): `ComposePageWork` now splits
# `ns_build` (compose) from `ns_paging` (the walk alone), landing in `ns_setup`/`ns_loop` respectively
# -- see the doc on `ComposePageWork::ns_build`. `evaluate()` below reads `compose["ns_loop"]` as the
# real, per-row `walk_ns`, no modeling needed on this side at all anymore.

# Permutation-backed sort columns only (`SortCol`'s six, minus `Rarity`/`PriceUsd` which have no
# permutation and never reach `Perm`) -- this bound is specifically about the `Perm` branch's risk.
ORDERBYS = ("edhrec", "cubecobra", "cmc", "power", "toughness", "name")
# Real `Perm`-paging traffic is offset~0-heavy (`costbench.OFFSETS` is `(0, 0, 0, 100)`) by design --
# that distribution can't exercise the deep tail this bound exists for, so sweep it explicitly instead.
OFFSET_SWEEP = (0, 50, 200, 500, 1_000, 2_000, 4_000, 8_000, 15_000, 25_000)
LIMIT = 20
NUM_WARMUPS = costbench.NUM_WARMUPS
NUM_TRIALS = costbench.NUM_TRIALS
POLICY_SPLIT_SEED = 730_1009
MIN_POLICY_GROUPS = 2


# ─── The two closed-form anchors ───────────────────────────────────────────────


def worst_case_bound(n_cards: int, matches: int, k: int) -> float:
    """Every non-matching card clumped before the k-th match -- an exact, unconditional ceiling."""
    if k > matches:
        # A partial final page never fills, so `walk_grouped_page` exhausts the permutation rather
        # than stopping at the last match.
        return float(n_cards)
    return max(n_cards - matches, 0) + k


def uniform_mean(n_cards: int, matches: int, k: int) -> float:
    """Expected position of the k-th match if `matches` cards were scattered with NO clumping -- the order-statistic mean of a uniformly random `matches`-subset of `n_cards` slots."""
    if k > matches:
        return float(n_cards)
    return k * (n_cards + 1) / (matches + 1)


def nhg_variance(n_cards: int, matches: int, k: int) -> float:
    """Variance of that same no-clumping position (negative hypergeometric, closed form).

    Zero at the edges by construction: `matches == n_cards` (nothing to scatter, position is exactly
    `k`) and `k == 0`. Verified against Monte Carlo in `selfcheck_nhg_moments` below, not just quoted.
    """
    n, m = n_cards, matches
    if m <= 0 or k <= 0 or k > m or m >= n:
        return 0.0
    return k * (n + 1) * (n - m) * (m - k + 1) / ((m + 1) ** 2 * (m + 2))


# ─── The two candidates, identical signature ───────────────────────────────────


def blend_bound(n_cards: int, matches: int, k: int, knob: float) -> float:
    """`knob` in [0, 1]: 0 is the pure no-clumping mean, 1 is the pure adversarial worst case.

    `knob == 1.0` returns `worst_case_bound` exactly rather than `lo + 1.0*(hi-lo)` -- that
    algebraically equals `hi`, but subtractive cancellation between two close floats can drift it a
    hair below `hi`, which matters here: a real query landing exactly ON the worst-case bound would
    then spuriously register as a "violation" of a bound that is, by construction, never violated.
    """
    if knob >= 1.0:
        return worst_case_bound(n_cards, matches, k)
    lo, hi = uniform_mean(n_cards, matches, k), worst_case_bound(n_cards, matches, k)
    return lo + knob * (hi - lo)


def sigma_bound(n_cards: int, matches: int, k: int, knob: float) -> float:
    """`knob` = how many std devs above the no-clumping mean, same random-placement model as `blend_bound`'s low anchor -- just a different (distribution-shaped, not linear) margin."""
    mean = uniform_mean(n_cards, matches, k)
    # A statistical margin is never usefully more conservative than the exact unconditional ceiling.
    return min(worst_case_bound(n_cards, matches, k), mean + knob * math.sqrt(nhg_variance(n_cards, matches, k)))


METHODS = {
    "blend": (blend_bound, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]),
    "sigma": (sigma_bound, [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]),
}


def selfcheck_nhg_moments(rng: random.Random) -> None:
    """Monte Carlo check of `uniform_mean`/`nhg_variance` against simulated random placements -- quoting a variance formula from memory is exactly the kind of thing this repo's conventions say to verify, not assume."""
    trials = 20_000
    cases = [(500, 50, 10), (5_000, 200, 80), (2_000, 1_800, 500)]
    print("selfcheck: analytic vs Monte Carlo (negative hypergeometric position of the k-th match)")
    for n, m, k in cases:
        positions = []
        for _ in range(trials):
            chosen = rng.sample(range(n), m)
            chosen.sort()
            positions.append(chosen[k - 1] + 1)  # 1-indexed position of the k-th match
        sim_mean = sum(positions) / trials
        sim_var = sum((p - sim_mean) ** 2 for p in positions) / (trials - 1)
        an_mean, an_var = uniform_mean(n, m, k), nhg_variance(n, m, k)
        print(
            f"  N={n:<6} M={m:<5} k={k:<4} mean: sim={sim_mean:9.2f} analytic={an_mean:9.2f}   "
            f"var: sim={sim_var:11.1f} analytic={an_var:11.1f}"
        )


def evaluate(engine: object, query: str, orderby: str, direction: str, offset: int) -> dict | None:
    """One (query, orderby, direction, offset) point's features + realized `cards_visited`.

    Grades against `compose["result_total"]`, the EXECUTED plan's exact match count ("ground truth
    for this run", `card_engine/src/lib.rs`'s own doc on `PlanTrial::result_total`) -- not `explain()`'s
    acquire-time `matches`, which can be a `calibrated_balls_into_bins` ESTIMATE rather than an exact
    count when `exact_result_total` declines the filter's shape (`compose_paging_with_total`'s own
    doc on `result_total`: "the acquire-time ESTIMATE where the fastpath has the exact count"). A
    first pass graded against the acquire estimate and found the SUPPOSEDLY-IMPOSSIBLE-TO-VIOLATE
    `worst_case_bound` violated on real queries -- not a flaw in the bound's math (re-verified by
    hand), but the acquire estimate over-stating `M` for those queries, which shrinks `(n_cards - M)`
    below the true value. Grading the bound formula itself needs the exact `M` it was proven against;
    the acquire-time estimate's own error is a separate, second-order question (`est_matches` is kept
    on the row for that comparison) -- and the one that matters operationally, since a WIRED-IN
    version of this bound would run inside the fastpath using the exact count it already computes
    before choosing `Perm` (`compose_total_for_mode`, paid for regardless), not the acquire estimate.

    `None` if this point doesn't land on `Mode::Card` `Perm` paging for both the plan estimate and the
    actual execution, or is too degenerate (page past the total, or a match count `>= n_cards`, where
    clumping cannot exist at all) to be an interesting test of a CLUMPING bound.
    """
    try:
        filters = parse_scryfall_query(query)
    except Exception:  # noqa: BLE001 - a handful of vocab strings don't round-trip through the grammar
        return None
    quick = engine.explain(filters=filters, unique="card", orderby=orderby, direction=direction, limit=LIMIT, offset=offset)
    acq = quick["acquire"]
    if acq["compose_paging"] != "Perm":
        return None
    n_cards, n_printings, est_matches = acq["n_cards"], acq["n_printings"], acq["matches"]
    printings_walked_pred = acq["printings_walked"]  # cost::printings_walked(f) -- the router's OWN input, acquire-time
    k = offset + LIMIT
    full = engine.explain_analyze(
        filters=filters,
        unique="card",
        orderby=orderby,
        direction=direction,
        limit=LIMIT,
        offset=offset,
        num_warmups=NUM_WARMUPS,
        num_trials=NUM_TRIALS,
    )
    costbench.require_schema(full)
    compose = next((p for p in full["plans"] if p["plan"] == "PrintingCompose"), None)
    if compose is None or not compose["trials_ns"] or compose.get("paging_taken") != "Perm":
        return None
    matches = compose["result_total"]
    realized = compose["cards_visited"]
    if matches <= 0 or offset >= matches or matches >= n_cards:
        return None
    # `ns_loop` is now the paging branch ALONE -- `card_engine/src/lib.rs`'s `ComposePageWork` split
    # (this session) separated it from `ns_setup` (the compose-build cost that used to be bundled in
    # with it, `plan_self_ns`'s old total). This is the REAL per-row walk cost, not a kernel-modeled
    # stand-in -- exactly what the full code path can now report directly.
    walk_ns = compose["ns_loop"]
    if walk_ns <= 0:
        return None
    return {
        "n_cards": n_cards,
        "n_printings": n_printings,
        "matches": matches,
        "est_matches": est_matches,
        "k": k,
        "offset": offset,
        "realized": realized,
        "printings_examined": compose["printings_examined"],
        # Real popcount(pbits) for this exact query, keyed variable for `three_phase_cost_ns` --
        # unlike `matches` (a bound-dependent quantity when a policy is grading a hypothetical, not
        # this row's real filter), this is a fixed property of the query itself.
        "set_printings": compose["set_printings"],
        "orderby": orderby,
        "direction": direction,
        "query": query,
        "clumping_factor": realized / uniform_mean(n_cards, matches, k),
        "walk_ns": walk_ns,
        "printings_walked_pred": printings_walked_pred,
    }


def _report_prod_cost_model(rows: list[dict]) -> None:
    """PRODUCTION cost model (`cost::plan_cost`) vs real `walk_ns`, pooled and by offset."""
    prod_ratios_by_row = [(prod_cost_model_ns(r["printings_walked_pred"], LIMIT) / r["walk_ns"], r) for r in rows]
    prod_ratio = sorted(ratio for ratio, _ in prod_ratios_by_row)
    print(
        "PRODUCTION cost model (cost::plan_cost, PrintingCompose/Perm) vs real walk_ns, "
        "ratio = predicted/realized (>1 over-costs, <1 under-costs):"
    )
    for p in (0, 5, 10, 25, 50, 75, 90, 95, 100):
        print(f"  p{p:<3} {percentile(prod_ratio, p):.3f}")
    print("\nSAME ratio, BY OFFSET (is the under-cost uniform, or concentrated at the deep offsets real")
    print("traffic rarely reaches -- i.e. is the model actually fine for the shallow case it was likely")
    print("tuned against, and only broken for the population this whole investigation is about?):")
    by_offset_prod: dict[int, list[float]] = {}
    for ratio, r in prod_ratios_by_row:
        by_offset_prod.setdefault(r["offset"], []).append(ratio)
    for off, vals in sorted(by_offset_prod.items()):
        vals.sort()
        print(f"  offset={off:<8} n={len(vals):<4} p50={percentile(vals, 50):>7.3f}  p90={percentile(vals, 90):>7.3f}")


def _report_acquire_estimate_error(rows: list[dict]) -> None:
    """Acquire-time estimate of `matches` vs the exact count -- the second-order error `evaluate()` keeps separate from the bound's own grading."""
    print()
    est_ratio = sorted(r["est_matches"] / r["matches"] for r in rows)
    print(
        f"acquire-estimate matches / exact matches (>1 over-estimates M, shrinks worst_case's margin): "
        f"p10={percentile(est_ratio, 10):.2f}  p50={percentile(est_ratio, 50):.2f}  p90={percentile(est_ratio, 90):.2f}"
    )


def _report_worst_case_bound(rows: list[dict]) -> None:
    """`worst_case_bound` alone: violation count and slack, pooled and by offset."""
    wc_slack = [worst_case_bound(r["n_cards"], r["matches"], r["k"]) / r["realized"] for r in rows]
    print(f"worst_case_bound alone: 0 violations by construction; slack (bound/realized) p50={percentile(sorted(wc_slack), 50):.2f}")
    print("\nworst_case_bound slack BY OFFSET (does the bound stay huge even at shallow offset, where\n"
          "walk_grouped_page's real cost is tiny? -- if so, comparing its COST estimate against three-\n"
          "phase's would reject walk_grouped_page there too, not just on the deep pages it's meant to catch):")
    by_offset: dict[int, list[float]] = {}
    for r in rows:
        by_offset.setdefault(r["offset"], []).append(worst_case_bound(r["n_cards"], r["matches"], r["k"]) / r["realized"])
    for off, vals in sorted(by_offset.items()):
        vals.sort()
        print(f"  offset={off:<8} n={len(vals):<4} p50 slack={percentile(vals, 50):>8.1f}  p90 slack={percentile(vals, 90):>8.1f}")


def _report_blend_bound_by_offset(rows: list[dict]) -> None:
    """`blend_bound(knob=0.6)` slack by offset, for comparison against `worst_case_bound`'s table."""
    print("\nblend_bound(knob=0.6) slack BY OFFSET, for comparison against worst_case's table above --")
    print("does the better-behaved knob still stay tight at shallow offset (where it matters for keeping")
    print("walk_grouped_page in play), not just safe at deep offset?")
    by_offset_blend: dict[int, list[float]] = {}
    for r in rows:
        by_offset_blend.setdefault(r["offset"], []).append(blend_bound(r["n_cards"], r["matches"], r["k"], 0.6) / r["realized"])
    for off, vals in sorted(by_offset_blend.items()):
        vals.sort()
        print(f"  offset={off:<8} n={len(vals):<4} p50 slack={percentile(vals, 50):>8.2f}  p90 slack={percentile(vals, 90):>8.2f}")


def _dump_worst_case_violations(rows: list[dict]) -> None:
    """Dump any row where `worst_case_bound` undershot `realized` -- should never fire; diagnostic only."""
    wc_violations = [r for r in rows if worst_case_bound(r["n_cards"], r["matches"], r["k"]) < r["realized"]]
    if wc_violations:
        print(f"  !! {len(wc_violations)} worst_case_bound violations (should be impossible) -- dumping for diagnosis:")
        for r in wc_violations:
            wc = worst_case_bound(r["n_cards"], r["matches"], r["k"])
            print(f"     n_cards={r['n_cards']} matches={r['matches']} k={r['k']} offset={r['offset']} realized={r['realized']} worst_case={wc}")


def _report_method_knob_table(rows: list[dict]) -> None:
    """Per-(method, knob) violation rate and slack, weighting represented offsets equally."""
    print(f"{'method':<8} {'knob':>6} {'violations':>11} {'viol%':>8} {'p50 slack':>10} {'p90 slack':>10} {'p99 slack':>10}")
    weighted_rows = _offset_weighted_rows(rows)
    total_weight = sum(weight for _, weight in weighted_rows)
    for name, (fn, knobs) in METHODS.items():
        for knob in knobs:
            slacks = []
            violations = 0
            violation_weight = 0.0
            for r, weight in weighted_rows:
                bound = fn(r["n_cards"], r["matches"], r["k"], knob)
                slacks.append((bound / r["realized"], weight))
                if bound < r["realized"]:
                    violations += 1
                    violation_weight += weight
            slacks.sort()
            p50 = _weighted_percentile(slacks, 50)
            p90 = _weighted_percentile(slacks, 90)
            p99 = _weighted_percentile(slacks, 99)
            print(
                f"{name:<8} {knob:>6.2f} {violations:>11} {100 * violation_weight / total_weight:>7.2f}% "
                f"{p50:>10.2f} {p90:>10.2f} {p99:>10.2f}"
            )


def _report_overshoot_among_violations(rows: list[dict]) -> None:
    """Overshoot (realized/bound) among violations only, every knob -- how bad a miss is, not just how often.

    A "95% safe" bound is only useful if the 5% that miss don't reintroduce the exact unbounded tail
    this whole effort exists to remove.
    """
    print("\novershoot (realized/bound) AMONG VIOLATIONS ONLY, EVERY knob -- if this stays near 1.0, a")
    print("violation is a near-miss; if it's large/growing, relaxing the violation-rate target doesn't cap")
    print("the pathological case, it just makes it rarer:")
    weighted_rows = _offset_weighted_rows(rows)
    for name, (fn, knobs) in METHODS.items():
        for knob in knobs:
            overshoots = []
            for r, weight in weighted_rows:
                bound = fn(r["n_cards"], r["matches"], r["k"], knob)
                if bound < r["realized"]:
                    overshoots.append((r["realized"] / bound, weight))
            overshoots.sort()
            if not overshoots:
                print(f"  {name} knob={knob:<5.2f}: 0 violations")
                continue
            print(
                f"  {name} knob={knob:<5.2f} n_violations={len(overshoots):<5} "
                f"p50={_weighted_percentile(overshoots, 50):.2f}  "
                f"p90={_weighted_percentile(overshoots, 90):.2f}  max={overshoots[-1][0]:.2f}"
            )


def _report_clumping_by_selectivity(rows: list[dict]) -> None:
    """`clumping_factor` by selectivity decile -- does a low- or high-selectivity query clump worse?

    Does clumping severity correlate with anything cheap to know a priori?
    """
    print("\nclumping_factor (realized/uniform_mean) BY SELECTIVITY DECILE (does a low-M/n_cards query")
    print("clump worse than a high-selectivity one, or is it roughly uniform across selectivity?):")
    by_sel: list[tuple[float, dict]] = sorted(((r["matches"] / r["n_cards"], r) for r in rows), key=lambda t: t[0])
    decile_size = max(len(by_sel) // 10, 1)
    for d in range(0, len(by_sel), decile_size):
        chunk = by_sel[d : d + decile_size]
        if not chunk:
            continue
        sel_lo, sel_hi = chunk[0][0], chunk[-1][0]
        cfs = sorted(r["clumping_factor"] for _, r in chunk)
        print(
            f"  selectivity [{sel_lo:.4f}, {sel_hi:.4f}]  n={len(chunk):<4} "
            f"p50={percentile(cfs, 50):>8.2f}  p90={percentile(cfs, 90):>8.2f}  max={cfs[-1]:>8.2f}"
        )


def _report_clumping_by_orderby(rows: list[dict]) -> None:
    """`clumping_factor` by orderby column -- does a semantically-loaded sort column clump worse than an arbitrary one?"""
    print("\nclumping_factor BY ORDERBY COLUMN (does a real, semantically-loaded sort column like edhrec")
    print("clump worse than an arbitrary one like name?):")
    by_orderby: dict[str, list[float]] = {}
    for r in rows:
        by_orderby.setdefault(r["orderby"], []).append(r["clumping_factor"])
    for col, cfs in sorted(by_orderby.items()):
        cfs.sort()
        print(f"  {col:<12} n={len(cfs):<5} p50={percentile(cfs, 50):>8.2f}  p90={percentile(cfs, 90):>8.2f}  max={cfs[-1]:>8.2f}")


def _report_worst_outliers(rows: list[dict]) -> None:
    """The 10 rows with the highest `clumping_factor`, for manual inspection."""
    print("\nworst 10 outliers by clumping_factor (manual-inspection candidates):")
    worst = sorted(rows, key=lambda r: -r["clumping_factor"])[:10]
    for r in worst:
        print(
            f"  cf={r['clumping_factor']:>7.2f}  orderby={r['orderby']:<10} dir={r['direction']:<5} "
            f"offset={r['offset']:<6} n_cards={r['n_cards']} matches={r['matches']} realized={r['realized']:<7} "
            f"query={r['query']!r}"
        )


def report(rows: list[dict]) -> None:
    """Per-(method, knob) violation rate and bound tightness, so a knob can be picked by matching violation rates across methods and comparing which gives the smaller (tighter) bound there."""
    if not rows:
        print("Nothing landed on Card-mode Perm for both explain() and explain_analyze() -- nothing to grade.")
        return
    print(f"\nn={len(rows)} (query, orderby, direction, offset) points graded\n")
    _report_prod_cost_model(rows)
    _report_acquire_estimate_error(rows)
    _report_worst_case_bound(rows)
    _report_blend_bound_by_offset(rows)
    _dump_worst_case_violations(rows)
    _report_method_knob_table(rows)
    _report_overshoot_among_violations(rows)
    _report_clumping_by_selectivity(rows)
    _report_clumping_by_orderby(rows)
    _report_worst_outliers(rows)


@dataclass(frozen=True)
class WalkCostModel:
    """Non-negative two-term fit for the native walk's measured work."""

    ns_per_card: float
    ns_per_printing: float

    def estimate(self, cards_visited: float, printings_examined: float) -> float:
        """Estimate walk latency from both operations the loop reports."""
        return self.ns_per_card * cards_visited + self.ns_per_printing * printings_examined


def _fit_walk_cost_model(rows: list[dict]) -> WalkCostModel:
    """Fit non-negative least squares with the same equal-offset weighting as policy evaluation."""
    weighted_rows = _offset_weighted_rows(rows)
    cc = sum(weight * r["realized"] ** 2 for r, weight in weighted_rows)
    cp = sum(weight * r["realized"] * r["printings_examined"] for r, weight in weighted_rows)
    pp = sum(weight * r["printings_examined"] ** 2 for r, weight in weighted_rows)
    cy = sum(weight * r["realized"] * r["walk_ns"] for r, weight in weighted_rows)
    py = sum(weight * r["printings_examined"] * r["walk_ns"] for r, weight in weighted_rows)
    candidates = [
        WalkCostModel(cy / cc if cc else 0.0, 0.0),
        WalkCostModel(0.0, py / pp if pp else 0.0),
    ]
    determinant = cc * pp - cp * cp
    if determinant > 0:
        both = WalkCostModel((cy * pp - py * cp) / determinant, (py * cc - cy * cp) / determinant)
        if both.ns_per_card >= 0 and both.ns_per_printing >= 0:
            candidates.append(both)

    def squared_error(model: WalkCostModel) -> float:
        return sum(
            weight * (model.estimate(r["realized"], r["printings_examined"]) - r["walk_ns"]) ** 2
            for r, weight in weighted_rows
        )

    return min(candidates, key=squared_error)


def _split_policy_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split by query shape so no offset sibling appears in both calibration and evaluation."""
    keys = sorted({(r["query"], r["orderby"], r["direction"]) for r in rows})
    if len(keys) < MIN_POLICY_GROUPS:
        msg = "policy simulation needs at least two distinct query/orderby/direction groups"
        raise ValueError(msg)
    random.Random(POLICY_SPLIT_SEED).shuffle(keys)
    calibration_keys = set(keys[: len(keys) // 2])
    calibration = [r for r in rows if (r["query"], r["orderby"], r["direction"]) in calibration_keys]
    evaluation = [r for r in rows if (r["query"], r["orderby"], r["direction"]) not in calibration_keys]
    return calibration, evaluation


def _predicted_printings_for_bound(
    engine: object,
    orderby: str,
    direction: str,
    bound_cards_ceil: int,
) -> float:
    """Convert a card-visit bound to a sound printing-probe bound with one archived-prefix lookup.

    `walk_grouped_page` starts at permutation entry zero and examines every printing span it visits.
    The engine archives the cumulative printing span for each sort permutation, so looking up
    `ceil(bound_cards)` preserves the card bound's conservatism without an O(bound_cards) hot-path
    scan. Production's eventual decision rule can call the same Rust helper directly.
    """
    return float(
        engine.perm_printings_examined_upper(
            bound_cards_ceil,
            orderby=orderby,
            direction=direction,
        )
    )


# The two rejected offset-gate thresholds (see the module's `Dead ends` doc elsewhere in this repo:
# gating protects the wrong population, since clumping outliers sit at shallow offsets too). Kept as
# comparison rows for `worst_case`/`blend` only -- named so the label can never drift from the value
# it names, which a bare literal duplicated into a separate f-string label cannot guarantee.
GATE_OFFSET_LOW = 2_000
GATE_OFFSET_HIGH = 4_000


def _build_policies(engine: object, walk_model: WalkCostModel) -> dict[str, object]:
    """The named decision policies `simulate_policies` grades against each other, keyed by display name."""

    @functools.cache
    def predicted_printings(orderby: str, direction: str, bound_cards_ceil: int) -> float:
        # The prefix depends only on corpus, permutation, and prefix length, not on the filter that
        # produced the bound. Cache the PyO3 lookup while the output tables call each policy repeatedly.
        return _predicted_printings_for_bound(engine, orderby, direction, bound_cards_ceil)

    def gated(gate: int, bound_fn) -> object:  # noqa: ANN001 - bound_fn is one of the module's *_bound callables
        def cost(r: dict) -> tuple[float, bool]:
            if r["offset"] < gate:
                return r["walk_ns"], False
            bound_cards = bound_fn(r["n_cards"], r["matches"], r["k"])
            predicted_walk_ns = walk_model.estimate(
                bound_cards,
                predicted_printings(
                    r["orderby"],
                    r["direction"],
                    math.ceil(bound_cards),
                ),
            )
            three_phase_ns = three_phase_cost_ns(r["set_printings"])
            if predicted_walk_ns <= three_phase_ns:
                return r["walk_ns"], False
            return three_phase_ns, True

        return cost

    return {
        "always_walk (today)": lambda r: (r["walk_ns"], False),
        "always_three_phase": lambda r: (three_phase_cost_ns(r["set_printings"]), True),
        "oracle (best possible)": lambda r: (
            (r["walk_ns"], False)
            if r["walk_ns"] <= three_phase_cost_ns(r["set_printings"])
            else (three_phase_cost_ns(r["set_printings"]), True)
        ),
        "no gate, worst_case": gated(0, worst_case_bound),
        "no gate, blend(0.6)": gated(0, lambda n, m, k: blend_bound(n, m, k, 0.6)),
        "no gate, sigma(1.0)": gated(0, lambda n, m, k: sigma_bound(n, m, k, 1.0)),
        "no gate, sigma(2.0)": gated(0, lambda n, m, k: sigma_bound(n, m, k, 2.0)),
        "no gate, sigma(3.0)": gated(0, lambda n, m, k: sigma_bound(n, m, k, 3.0)),
        "no gate, sigma(4.0)": gated(0, lambda n, m, k: sigma_bound(n, m, k, 4.0)),
        "no gate, sigma(6.0)": gated(0, lambda n, m, k: sigma_bound(n, m, k, 6.0)),
        "no gate, sigma(8.0)": gated(0, lambda n, m, k: sigma_bound(n, m, k, 8.0)),
        f"gate={GATE_OFFSET_LOW}, worst_case": gated(GATE_OFFSET_LOW, worst_case_bound),
        f"gate={GATE_OFFSET_HIGH}, worst_case": gated(GATE_OFFSET_HIGH, worst_case_bound),
        f"gate={GATE_OFFSET_LOW}, blend(0.6)": gated(GATE_OFFSET_LOW, lambda n, m, k: blend_bound(n, m, k, 0.6)),
        f"gate={GATE_OFFSET_HIGH}, blend(0.6)": gated(GATE_OFFSET_HIGH, lambda n, m, k: blend_bound(n, m, k, 0.6)),
    }


def _weighted_percentile(sorted_weighted: list[tuple[float, float]], pct: float) -> float:
    """Nearest-rank percentile where each row carries an explicit weight."""
    if not sorted_weighted:
        return float("nan")
    if pct <= 0:
        return sorted_weighted[0][0]
    target = pct / 100 * sum(weight for _, weight in sorted_weighted)
    cumulative = 0.0
    for value, weight in sorted_weighted:
        cumulative += weight
        if cumulative >= target:
            return value
    return sorted_weighted[-1][0]


def _offset_weighted_rows(rows: list[dict]) -> list[tuple[dict, float]]:
    """Give every represented offset the same total weight."""
    counts: dict[int, int] = {}
    for r in rows:
        counts[r["offset"]] = counts.get(r["offset"], 0) + 1
    return [(r, 1 / counts[r["offset"]]) for r in rows]


def _print_pooled_latency_table(rows: list[dict], policies: dict) -> dict[str, list[tuple[float, float]]]:
    """% diverted and paging-branch latency percentiles, pooled across the whole offset sweep."""
    print(f"{'policy':<26} {'%diverted':>10} {'p50':>10} {'p90':>10} {'p99':>10} {'max':>10}")
    print("(pooled across OFFSET_SWEEP, which weights shallow and deep offsets EQUALLY -- nothing like")
    print(" real traffic's offset~0-heavy mix, so this table is a policy-vs-policy comparison on a")
    print(" deliberately offset-heavy stress grid, not a production latency estimate. Latencies are")
    print(" PAGING-BRANCH ONLY: every policy has already paid the same compose build. See the by-offset")
    print(" breakdown below for the number that matters -- reweight it by your own traffic's real offset")
    print(" distribution instead of trusting a blended average here. 'diverted' means routed to")
    print(" three-phase instead of running the native walk.)")
    weighted_rows = _offset_weighted_rows(rows)
    total_weight = sum(weight for _, weight in weighted_rows)
    latencies_by_policy: dict[str, list[tuple[float, float]]] = {}
    for name, cost_fn in policies.items():
        latencies = []
        diverted_weight = 0.0
        for r, weight in weighted_rows:
            ns, diverted = cost_fn(r)
            latencies.append((ns, weight))
            diverted_weight += weight * diverted
        latencies.sort()
        latencies_by_policy[name] = latencies
        print(
            f"{name:<26} {100 * diverted_weight / total_weight:>9.1f}% "
            f"{fmt_ns(_weighted_percentile(latencies, 50)):>10} {fmt_ns(_weighted_percentile(latencies, 90)):>10} "
            f"{fmt_ns(_weighted_percentile(latencies, 99)):>10} {fmt_ns(latencies[-1][0]):>10}"
        )
    return latencies_by_policy


def _print_worst_surviving_rows(rows: list[dict], policies: dict) -> None:
    """Worst SURVIVING (non-diverted) row for a shortlist of policies -- does a bound's own worst case walk a row that should have been diverted?"""
    print("\nworst SURVIVING (non-diverted) row for each policy -- oracle's max is capped by construction")
    print("at always_three_phase's own max (see the earlier discussion); does sigma's/blend's own worst")
    print("case walk a row that should have been diverted, and by how much does it miss?")
    for name in (
        "oracle (best possible)",
        "no gate, blend(0.6)",
        "no gate, sigma(1.0)",
        "no gate, sigma(2.0)",
        "no gate, sigma(3.0)",
        "no gate, sigma(4.0)",
        "no gate, sigma(6.0)",
        "no gate, sigma(8.0)",
    ):
        cost_fn = policies[name]
        results = [(*cost_fn(r), r) for r in rows]
        walked = [(ns, r) for ns, diverted, r in results if not diverted]
        if not walked:
            continue
        worst_ns, r = max(walked, key=lambda t: t[0])
        tp = three_phase_cost_ns(r["set_printings"])
        print(
            f"  {name:<24} worst_walked={fmt_ns(worst_ns):>10}  offset={r['offset']:<6} matches={r['matches']:<6} "
            f"n_cards={r['n_cards']}  three_phase_would_be={fmt_ns(tp):>10}  clumping_factor={r['clumping_factor']:.2f}"
        )


def _print_headline_percentiles(
    latencies_by_policy: dict[str, list[tuple[float, float]]], headline: list[str], chart_path: pathlib.Path | None
) -> None:
    """5%-granularity percentile-vs-paging-latency table, plus the tail-concentrated chart export."""
    print(f"\npercentile vs PAGING-BRANCH latency (ns), 5% granularity, for the {len(headline)} headline policies:")
    pcts_print = list(range(0, 101, 5))
    header2 = f"{'pct':<5}" + "".join(f"{name:>24}" for name in headline)
    print(header2)
    # Chart export uses a TAIL-CONCENTRATED grid, not the flat 5% steps printed above: the policies
    # only visibly separate past p90 (pooling-heavy shallow offsets dominate the bulk of the range),
    # and a flat step size leaves only 2-3 points there -- not enough to see curve shape once a chart
    # zooms in. Use 1% steps 0-89 and 0.5% steps 90-100; the policy-split line above reports the
    # evaluation population that underlies each point.
    pcts = [float(p) for p in range(90)] + [90 + 0.5 * i for i in range(21)]
    chart_data = {"pcts": pcts, "series": {}}
    for name in headline:
        chart_data["series"][name] = [_weighted_percentile(latencies_by_policy[name], p) for p in pcts]
    for p in pcts_print:
        line = f"{p:<5}"
        for name in headline:
            line += f"{_weighted_percentile(latencies_by_policy[name], p):>24.0f}"
        print(line)

    if chart_path is not None:
        chart_path.write_text(json.dumps(chart_data))
        print(f"\nchart data written to {chart_path}")


def _print_by_offset_tables(rows: list[dict], policies: dict) -> None:
    """% diverted and median paging-branch latency, BY OFFSET, for a policy shortlist."""
    # By offset instead of pooled: this is the view to actually trust, since it doesn't require
    # guessing how real traffic distributes across offsets -- read it against YOUR OWN traffic's depth
    # distribution instead of a blended average that bakes in an arbitrary sweep weighting.
    shortlist = {
        "always_walk": policies["always_walk (today)"],
        "always_three_phase": policies["always_three_phase"],
        "no gate, blend(0.6)": policies["no gate, blend(0.6)"],
        "no gate, sigma(2.0)": policies["no gate, sigma(2.0)"],
        "no gate, sigma(3.0)": policies["no gate, sigma(3.0)"],
        "no gate, sigma(4.0)": policies["no gate, sigma(4.0)"],
    }
    by_offset_rows: dict[int, list[dict]] = {}
    for r in rows:
        by_offset_rows.setdefault(r["offset"], []).append(r)

    print("\n% diverted (routed to three-phase), BY OFFSET:")
    header = f"{'offset':<8}" + "".join(f"{name:>24}" for name in shortlist)
    print(header)
    for off, off_rows in sorted(by_offset_rows.items()):
        line = f"{off:<8}"
        for cost_fn in shortlist.values():
            frac = sum(1 for r in off_rows if cost_fn(r)[1]) / len(off_rows)
            line += f"{100 * frac:>23.1f}%"
        print(line)

    print("\nmedian realized PAGING-BRANCH latency (ns), BY OFFSET:")
    print(header)
    for off, off_rows in sorted(by_offset_rows.items()):
        line = f"{off:<8}"
        for cost_fn in shortlist.values():
            lat = sorted(cost_fn(r)[0] for r in off_rows)
            line += f"{percentile(lat, 50):>24.0f}"
        print(line)


def simulate_policies(
    engine: object,
    calibration_rows: list[dict],
    evaluation_rows: list[dict],
    chart_path: pathlib.Path | None,
) -> None:
    """For each policy, what fraction of traffic is diverted, and what happens to paging latency?

    The walk side uses each row's REAL `walk_ns` (see `evaluate()`) whenever a policy picks it,
    including `oracle` (the reference point: the best any policy COULD do, using the row's real
    `walk_ns` directly -- unknowable in advance, but it bounds how much more there is to gain over a
    real decision rule, which only ever gets `matches`/`n_cards`/`k`, never the outcome). The
    bound-based policies still have to PREDICT a hypothetical walk cost before running anything, so
    they convert bound cards to a sound printing-work bound through the engine's archived
    per-permutation prefix, then price both through a two-term model fit on a disjoint calibration
    half. The three-phase side is no longer modeled by a hand-fit calibration either: `three_phase_
    cost_ns` is a direct port of the validated Rust breakpoint table, keyed on each row's REAL
    `set_printings` -- the only thing not measured directly here is the promoted implementation's own
    latency, since it has no Python binding to run.
    """
    walk_model = _fit_walk_cost_model(calibration_rows)
    print(
        f"\npolicy split: calibration={len(calibration_rows)} rows, evaluation={len(evaluation_rows)} rows; "
        f"walk fit={walk_model.ns_per_card:.3f} ns/card + {walk_model.ns_per_printing:.3f} ns/printing\n"
    )
    policies = _build_policies(engine, walk_model)
    latencies_by_policy = _print_pooled_latency_table(evaluation_rows, policies)
    _print_worst_surviving_rows(evaluation_rows, policies)
    headline = [
        "always_walk (today)",
        "always_three_phase",
        "oracle (best possible)",
        "no gate, sigma(1.0)",
        "no gate, sigma(2.0)",
        "no gate, sigma(3.0)",
        "no gate, sigma(4.0)",
        "no gate, sigma(6.0)",
        "no gate, sigma(8.0)",
    ]
    _print_headline_percentiles(latencies_by_policy, headline, chart_path)
    _print_by_offset_tables(evaluation_rows, policies)


def main() -> None:
    """Sample uniform real queries, sweep deep offsets explicitly, grade both bound candidates."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n-queries", type=int, default=400)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    parser.add_argument("--chart-path", type=pathlib.Path, default=None, help="write chart series JSON here")
    args = parser.parse_args()
    if args.n_queries <= 0:
        parser.error("--n-queries must be greater than zero")

    rng = random.Random(args.seed)
    selfcheck_nhg_moments(rng)

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".cardvisitedbound.store"))
    sampler = QuerySampler(args.corpus, "uniform")

    rows: list[dict] = []
    considered = 0
    for _ in range(args.n_queries):
        # Exercise the sampler's normal predicate counts and connective structures. The
        # `ns_build`/`ns_paging` split keeps multi-predicate compose work out of the measured walk, so
        # there is no longer a measurement reason to narrow this to one flat predicate.
        query = sampler.structured_query(rng)["query"]
        orderby = rng.choice(ORDERBYS)
        direction = rng.choice(("asc", "desc"))
        for offset in OFFSET_SWEEP:
            considered += 1
            row = evaluate(engine, query, orderby, direction, offset)
            if row is not None:
                rows.append(row)

    print(f"\n{considered} (query, orderby, direction, offset) points considered, {len(rows)} landed on Card-mode Perm both ways.")
    if not rows:
        report(rows)
        return
    try:
        calibration_rows, evaluation_rows = _split_policy_rows(rows)
    except ValueError as exc:
        # A small requested sample can legitimately leave only one eligible query shape after the
        # Perm filters above. The descriptive report is still useful; only the held-out policy
        # comparison is impossible.
        report(rows)
        print(f"\npolicy simulation skipped: {exc}")
        return

    # Every diagnostic that can inform the choice of method/knob sees calibration rows only. The
    # evaluation half remains untouched until the policy table below. Reporting several predeclared
    # sigma knobs there is a sensitivity analysis; choosing a new knob from those rows would make
    # that choice exploratory and require another held-out run.
    print(f"\nBOUND CALIBRATION ONLY ({len(calibration_rows)} rows; evaluation remains held out)")
    report(calibration_rows)
    simulate_policies(engine, calibration_rows, evaluation_rows, args.chart_path)


if __name__ == "__main__":
    main()
