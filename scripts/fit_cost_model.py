"""Refit the cost model's rate constants against measured plan time, on the model's own features.

`bench_cost_model_agreement.py` says *which* (plan, acquire) cells disagree. This says *what the
constants should be* — it regresses measured per-plan time on exactly the feature vector
`cost::plan_cost` consumes, so a fitted coefficient drops straight into `cost.rs`.

Two things make this different from eyeballing a ratio:

- **The regression is relative, not absolute.** Plain least squares is dominated by the handful of
  slowest queries, which is how a model can fit the big cases and be 3x off across the common ones.
  Every row is scaled by its own measured time, so the objective is squared *relative* error — the
  same thing the [0.8, 1.25] median bar measures.
- **Coefficients are constrained non-negative.** These are per-unit hardware costs; a negative rate
  fits noise and then extrapolates catastrophically outside the sampled range.

Fitting only works once the FEATURES are right. A feature that mis-counts by 2.5x cannot be repaired
by any rate, and the fit will happily bury the error in whichever coefficient correlates with it —
so `--counters` first checks each realized counter against the feature that is supposed to predict
it, and refuses to report rates for a plan whose features do not track reality.

    .venv/bin/python scripts/fit_cost_model.py --seconds 600
"""

from __future__ import annotations

import argparse
import collections
import math
import pathlib
import random
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.bench_cost_model_agreement import AGREE_HI, AGREE_LO  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

NUM_WARMUPS = 2
NUM_TRIALS = 7
LIMITS = (10, 100, 175)
OFFSETS = (0, 0, 0, 100)
# Coordinate descent on the non-negative normal equations. The problem is convex and tiny (<=6
# coefficients), so this converges in far fewer sweeps than the cap.
MAX_FIT_SWEEPS = 500
FIT_TOLERANCE = 1e-12
# Below this a cell's fit is noise, not a rate.
MIN_ROWS_FOR_FIT = 200
# `run_query_streamed`'s small-total gather branch scans all n_cards; mirrors CARD_ENGINE_STREAM_MIN_MATCHES.
STREAM_MIN_MATCHES = 1024
# Mirrors cost.rs MATCH_RATE_FLOOR, the density floor under the page-fill walk length.
MATCH_RATE_FLOOR = 1.0 / 1_000_000.0
# Mirrors cost.rs WALK_LENGTH_BIAS: matches clump along the sort order, so a walk runs ~1.45x longer
# than uniform spacing predicts. Measured 0.69 against `printings_examined` before this existed.
WALK_LENGTH_BIAS = 1.45
# A realized counter this far from the feature meant to predict it is a FEATURE bug; refitting rates
# on top of it just relocates the error.
COUNTER_TOL = 0.15
# The mirror check's tolerance. This is a reimplementation of cost.rs in Python, so it can drift --
# and did: the arms moved to `max(tier, floor)` and gained a residual-gated per-row term while
# `design_row` still modelled the tier as a multiplier, which silently invalidated every coefficient
# reported for two revisions. The check below compares the mirror against the engine's own
# predicted_ns and refuses to report if they disagree.
MIRROR_TOLERANCE = 0.001
MIRROR_MIN_AGREEMENT = 0.99
# Gauss-Newton on the log objective; converges in a handful of steps from the current constants.
MAX_IRLS_ITERS = 40
# --by-mode: how far a per-unit rate may move between distinct-on modes before the arm is judged to be
# missing a mode-dependent term rather than to be sampling noise. Per-unit hardware costs should not
# depend on distinct-on at all -- that is the assumption the single shared arm rests on, so the bar is
# set near the noise floor rather than at a "surely that is broken" level. At 2.0 only one term in the
# engine flagged; the interesting cases sit between 1.3 and 2.0.
MODE_SPLIT_FACTOR = 1.3
# Below this (ns per unit) a rate is unidentified rather than mode-dependent, and its ratio is noise.
MODE_SPLIT_MIN_RATE = 0.05
IRLS_TOLERANCE = 1e-6
IRLS_MIN_PREDICTION_NS = 1.0
# Ridge pull toward the current constants, per row of design. Small enough that a well-identified
# term moves freely, large enough to pin the collinear ones (floor/page_span vs the intercept).
RIDGE_STRENGTH = 0.01

# The constants currently in cost.rs, in the same term order design_row emits. The IRLS start point,
# and the baseline each fitted rate is reported against.
CURRENT: dict[str, list[float]] = {
    # eval_domain, scan_units, tier scale, matches, page_span, fixed
    # ..., page_span, page_rows, fixed -- page_rows new 2026-08-03. The phase has two drivers: the
    # quickselect scales with offset+limit, the collect with the page actually returned. A designed page
    # sweep separates them where traffic cannot, since the two are correlated in the sampled query mix.
    # 2026-08-03: the first column is now the UNCONDITIONAL loop rate and the third carries the
    # `card_pass` call (3.00) on top of the floor (18.89), because the arm gates the call on
    # `tier_ns > 0` -- `all_match_known` skips it. The design matrix already had these as two
    # columns; only the arm and these labels changed.
    "GatheredScan": [3.88, 2.06, 21.89, 2.24, 3.51, 9.79, 169.6],
    # eval_domain, scan_units, residual floor, matches, artwork_seen_cards, n_cards floor, corpus pass, fixed
    # Refit once `printings_examined` existed: this plan's fit was vetoed for as long as the only
    # available counter was the printing SPAN, which its all_match rows disagree with by ~3x over a
    # term the arm multiplies by zero. Median agreement 0.63 -> 0.92, within-25% 19% -> 58%.
    # ..., perm_steps, ... -- the permutation walk's length, new 2026-08-03. It is the one quantity in
    # P3's finish phase no other feature is proportional to: the walk steps until the page fills, so it
    # visits ~page_span * n_cards / matches entries, inversely proportional to selectivity.
    # Same split as GatheredScan above: 2.58 unconditional, and the call (2.47) folded into the
    # residual-gated column alongside the 6.58 floor.
    "StreamedSelect": [2.58, 5.97, 9.05, 0.12, 1.0, 1.21, 1.02, 0.02, 217.0],
    # broadcast, scatter, project, popcount, walk step, walk emit, gather card pass, gather bittest,
    # gather push, fixed. Several of these are SHARED with other arms in cost.rs (LINEAR_PASS,
    # RANGE_SCATTER, GATHER_CARD_PASS, GATHER_PUSH_PER_MATCH, ...), so a fitted value that disagrees
    # with the other arm's is information about the shared constant, not a number to paste blindly.
    # GATHER_GROUP_PER_PRINTING sits between the bit-test and push columns, matching design_row.
    # Added when the artwork tail was traced to the grouping arm's work being charged at the bit-test
    # rate; its 1.5 start is a physical guess (a struct read plus prefer_score), meant to be fitted.
    # BUILD_PER_PRINTING (second to last) is the full-width bitmap build, Gather-arm only: it was
    # measured directly over a 10x corpus axis rather than fitted here, so a pooled fit disagreeing
    # with 0.0835 is a signal to re-examine, not to paste.
    "PrintingCompose": [1.93, 0.48, 1.93, 1.07, 0.58, 2.19, 13.22, 0.38, 1.5, 3.39, 0.0835, 163.56],
}


def fit_log_ratio(design: list[list[float]], targets: list[float], start: list[float], weights: list[float]) -> list[float]:
    """Fit c >= 0 minimising squared LOG ratio, sum (log(Xc) - log(y))^2, by Gauss-Newton IRLS.

    Fitting `sum (Xc/y - 1)^2` instead looks like the same thing and is not: over-prediction is
    unbounded there while under-prediction saturates at 1, so the minimiser buys cheap error
    reduction by driving every per-unit rate to zero and leaving only the fixed term. Measured — it
    produced all-zero rates and *worse* agreement than the constants it was replacing.

    Log space is symmetric in over/under, which is what a "median ratio near 1.0" bar actually asks
    for. It is not linear in the coefficients, so each iteration reweights by the current prediction
    (the Gauss-Newton step for the log objective) and re-solves.
    """
    coeffs = list(start)
    for _ in range(MAX_IRLS_ITERS):
        scaled_rows, scaled_targets = [], []
        for row, y, w in zip(design, targets, weights, strict=True):
            pred = max(sum(c * v for c, v in zip(coeffs, row, strict=True)), IRLS_MIN_PREDICTION_NS)
            # sqrt(count): squared residuals then sum as if the shape appeared `count` times, which
            # is what a frequency-weighted median-ratio bar actually measures. Deduplicating to one
            # row per shape instead fits a DIFFERENT distribution -- it gives a rare expensive shape
            # the same say as a common cheap one, and measured 0.99 on shapes while the sampled
            # distribution sat at 0.62-0.85.
            scale = math.sqrt(w) / pred
            scaled_rows.append([v * scale for v in row])
            scaled_targets.append(y * scale)
        # Ridge toward the current constants, in RELATIVE units so one strength suits every term.
        # Several columns barely vary across this corpus — the StreamedSelect floor is literally
        # `n_cards` or 0, and page_span is usually just `limit` — leaving them collinear with the
        # intercept. Unregularised, the fit trades freely between them and lands on absurdities like
        # a 42 µs fixed cost. The prior pins those directions and lets the identified ones move.
        penalty = math.sqrt(RIDGE_STRENGTH * sum(weights))
        for j, prior in enumerate(start):
            if prior <= 0:
                continue
            row = [0.0] * len(start)
            row[j] = penalty / prior
            scaled_rows.append(row)
            scaled_targets.append(penalty)
        updated = nnls(scaled_rows, scaled_targets)
        shift = max(abs(a - b) / max(b, IRLS_MIN_PREDICTION_NS) for a, b in zip(updated, coeffs, strict=True))
        coeffs = updated
        if shift < IRLS_TOLERANCE:
            break
    return coeffs


def nnls(rows: list[list[float]], targets: list[float]) -> list[float]:
    """Minimise ||Xc - y|| subject to c >= 0, by coordinate descent on the normal equations."""
    n = len(rows[0])
    gram = [[sum(r[i] * r[j] for r in rows) for j in range(n)] for i in range(n)]
    xty = [sum(r[i] * t for r, t in zip(rows, targets, strict=True)) for i in range(n)]
    coeffs = [0.0] * n
    for _ in range(MAX_FIT_SWEEPS):
        delta = 0.0
        for i in range(n):
            if gram[i][i] <= 0:
                continue
            # Exact coordinate-wise minimum, clamped at the non-negativity boundary.
            residual = xty[i] - sum(gram[i][j] * coeffs[j] for j in range(n) if j != i)
            new = max(0.0, residual / gram[i][i])
            delta = max(delta, abs(new - coeffs[i]))
            coeffs[i] = new
        if delta < FIT_TOLERANCE:
            break
    return coeffs


# The residual floors the shipped arms use inside `max(tier_ns, floor)`.
#
# These MUST equal the `*_RESIDUAL_FLOOR_NS` constants in cost.rs, and equal the third entry of the
# matching `CURRENT` vector. All three are the same number: `design_row` makes the floor the
# coefficient of the `eval_domain * residual_on` column AND uses it to compute the `excess` offset for
# rows where the tier beats it. Change one without the others and `mirror_matches_engine` drops below
# its 99% bar -- which is exactly how the 2026-08-02 refit was caught pasting a fitted floor of 6.58
# into cost.rs while the offset here still assumed 8.18 (7.4% of rows disagreed).
#
# A consequence worth stating: the fitted floor is not directly pasteable, because the offset it was
# fitted against assumed the OLD floor. Applying it and re-fitting is a fixed-point iteration, and
# each run is only self-consistent with whatever is shipped at the time.
# The residual-gated column now prices the `card_pass` call as well as the floor, but the OFFSET
# below is still about the floor alone: it captures `eval_domain * max(tier_ns - FLOOR, 0)`, the
# excess where an expensive residual beats the floor, and the call is not part of that maximum.
SHIPPED_RESIDUAL_FLOOR = {"GatheredScan": 18.89, "StreamedSelect": 6.58}


def design_row(plan: str, acq: dict, limit: int, offset: int) -> tuple[list[float], list[str], float] | None:
    """The feature vector for one plan's cost arm, plus the part no coefficient scales.

    Mirrors `cost.rs` exactly. The awkward term is the residual charge, which the arms express as
    `eval_domain * max(tier_ns, FLOOR)` -- not linear in the floor, so it cannot be one column. It is
    split: a column of `eval_domain` gated on residual presence, whose coefficient IS the floor, plus
    an OFFSET of `eval_domain * max(tier_ns - FLOOR, 0)` for the excess where an expensive residual
    beats the floor. The offset must be subtracted from the target before fitting, or the fit solves
    `Xc ~= y` while the model computes `Xc + offset` -- a different problem, which shows up as
    impossible (negative) improvements.
    """
    eval_domain = float(acq["eval_domain"])
    scan_units = float(acq["scan_units"])
    # P3's own scan estimate, which differs from `scan_units` on a legality-composed acquire -- see
    # `PlanFeatures::stream_scan_units`. Absent from an older recorded run, in which case it equals
    # `scan_units` and this mirrors the pre-split arm exactly.
    stream_scan_units = float(acq.get("stream_scan_units", acq["scan_units"]))
    matches = float(acq["matches"])
    n_cards = float(acq["n_cards"])
    tier_ns = acq["residual_tier_ns100"] / 100.0
    page_span = float(min(offset + limit, acq["matches"]))
    # Mirrors cost.rs: `select_page` returns clamp(matches - offset, 0, limit), so a page past the end of
    # the matches collects fewer rows than requested.
    page_rows = float(min(max(acq["matches"] - offset, 0), limit))
    residual_on = 1.0 if tier_ns > 0.0 else 0.0
    floor = SHIPPED_RESIDUAL_FLOOR.get(plan, 0.0)
    excess = eval_domain * max(tier_ns - floor, 0.0) if tier_ns > 0.0 else 0.0

    if plan == "GatheredScan":
        return (
            [eval_domain, scan_units, eval_domain * residual_on, matches, page_span, page_rows, 1.0],
            [
                "LOOP_PER_CARD",
                "SCAN_PER_ROW",
                "CARD_PASS+FLOOR",
                "PUSH_PER_MATCH",
                "SELECT_PER_PAGE_SLOT",
                "COLLECT_PER_PAGE_ROW",
                "FIXED",
            ],
            excess,
        )
    if plan == "StreamedSelect":
        # Mirrors the arm's guards: an empty result or a page past the end returns before BOTH branches,
        # so neither the gather floor nor the walk is charged there.
        walks_perm = matches > STREAM_MIN_MATCHES and matches > 0 and offset < matches
        perm_steps = min(page_span * n_cards / matches, n_cards) if walks_perm else 0.0
        # Mirrors run_query_streamed's early return: zero matches, or a page past the total, never
        # reaches the small-total gather. See STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS in cost.rs.
        runs_small_gather = 0 < matches <= STREAM_MIN_MATCHES and offset < matches
        small_total = n_cards if runs_small_gather else 0.0
        # P3's per-row term is GATED on residual presence: it only counts matches, and
        # `card_match_count` is O(1) offset arithmetic under all_match, so it walks printings only
        # when a residual must be tested. `n_cards` carries the O(corpus) work it pays regardless of
        # selectivity -- the counts buffer resized and cleared every query.
        return (
            [
                eval_domain,
                stream_scan_units * residual_on,
                eval_domain * residual_on,
                matches,
                perm_steps,
                float(acq["artwork_seen_cards"]),
                small_total,
                n_cards,
                1.0,
            ],
            [
                "LOOP_PER_CARD",
                "SCAN_PER_ROW",
                "CARD_PASS+FLOOR",
                "EMIT_PER_MATCH",
                "PERM_STEP",
                "ARTWORK_SEEN_PER_CARD",
                "SMALL_TOTAL_FLOOR_PER_CARD",
                "CORPUS_PASS_PER_CARD",
                "FIXED",
            ],
            excess,
        )
    if plan == "PrintingCompose":
        # The arm no tool has ever fitted, while the regret matrix puts 75% of all lost time on it.
        # `build` is common to every paging branch; the page term is whichever branch will run, so a
        # row contributes to exactly one of the two page columns and zero to the other. Decline costs
        # infinity and never reaches a measurement, so those rows are absent by construction.
        paging = acq.get("compose_paging", "Gather")
        if paging == "Decline":
            return None
        gather = paging == "Gather"
        # Recomputed rather than read from the exposed u32, which is truncated for display. The
        # mirror has to match cost.rs bit for bit or its self-check fails on small walks.
        match_rate = max(matches / max(float(acq["n_printings"]), 1.0), MATCH_RATE_FLOOR)
        # Mirrors `cost::printings_walked`: the closed form times WALK_LENGTH_BIAS. The
        # `orderby_walk_scan` floor this used to take a max against is gone -- both walks now step a
        # value index entry at a time, so there is no bucket granularity to express.
        walk = page_span / match_rate * WALK_LENGTH_BIAS if not gather else 0.0
        return (
            [
                float(acq["broadcast_printings"]),
                float(acq["scatter_printings"]),
                float(acq["project_printings"]),
                float(acq["popcount_words"]),
                walk,
                limit if not gather else 0.0,
                eval_domain if gather else 0.0,
                float(acq["compose_scan_printings"]) if gather else 0.0,
                float(acq.get("gather_group_printings", 0)) if gather else 0.0,
                matches if gather else 0.0,
                # The full-width printing-bitmap build, charged on the Gather arm only -- Perm and
                # OrderbyWalk had their rates fitted with it already absorbed. See
                # `COMPOSE_BUILD_PER_PRINTING_NS` in cost.rs for why it is scoped rather than shared.
                float(acq["n_printings"]) if gather else 0.0,
                1.0,
            ],
            [
                "BROADCAST_PER_PRINTING",
                "SCATTER_PER_PRINTING",
                "PROJECT_PER_PRINTING",
                "POPCOUNT_PER_WORD",
                "WALK_STEP",
                "WALK_EMIT_PER_ROW",
                "GATHER_CARD_PASS",
                "GATHER_BITTEST_PER_PRINTING",
                "GATHER_GROUP_PER_PRINTING",
                "GATHER_PUSH_PER_MATCH",
                "BUILD_PER_PRINTING",
                "FIXED",
            ],
            0.0,  # no residual-floor term in this arm, so nothing comes off the target
        )
    return None


def perm_step_check(samples: list[dict]) -> tuple[int, float, float, float] | None:
    """Realized `perm_steps` against the estimate cost.rs derives. The ratio should be 1.00.

    Separate from `counter_check` because this feature is not published by acquire -- the arm computes
    it from `page_span`, `n_cards` and `matches`. The rate was fitted and cross-validated (kernel
    0.958-1.256 ns/entry, traffic 1.15), but a rate can look right while the quantity it multiplies is
    wrong, so the ESTIMATE needs its own grade.

    What is being tested is the uniform-spread assumption: the walk is modelled as finding one match
    every `n_cards / matches` entries, which holds if matches are scattered evenly through the sort
    permutation and fails if they cluster. Clustering is not far-fetched -- the permutation is ordered
    by a sort column, and predicates correlate with sort columns (`year>=2020` under `order=released`
    is the extreme case), so a real skew here would be a genuine model defect and not noise.

    Read the SPREAD, not just the median. The executor bounds its walk to the realized match span, so
    the ends of a cluster no longer cost anything and this ratio can only be inflated by non-matching
    entries INTERIOR to the span. Bounding those ends took p90 from 6.43 to 4.26 (p10 0.13 -> 0.08,
    median 1.00 -> 0.90) on one seed and sample length: a third of the tail was the leading prefix, and
    what is left is a different mechanism's to fix.

    Returns (rows, p10, median, p90) of realized/estimated, or None if no row walked.
    """
    ratios = []
    for s in samples:
        if s["plan"] != "StreamedSelect" or not s.get("perm_steps"):
            continue
        acq, matches = s["acq"], float(s["acq"]["matches"])
        if matches <= 0:
            continue
        page_span = float(min(s["offset"] + s["limit"], matches))
        estimate = min(page_span * float(acq["n_cards"]) / matches, float(acq["n_cards"]))
        if estimate > 0:
            ratios.append(float(s["perm_steps"]) / estimate)
    if not ratios:
        return None
    ratios.sort()
    return (len(ratios), ratios[len(ratios) // 10], ratios[len(ratios) // 2], ratios[(9 * len(ratios)) // 10])


def counter_check(samples: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """Realized counter vs the feature that should predict it, per plan. Ratios should be 1.00."""
    # `scan_units` pairs with `printings_examined`, not the `printing_span` this used to read: the span
    # is computed by the caller before the match kernel runs, so in card mode -- where every kernel
    # stops at the first qualifying printing -- it reports work that never happened.
    pairs = (("cards_visited", "eval_domain"), ("printings_examined", "scan_units"), ("matches_pushed", "matches"))

    def feature_for(plan: str, counter: str, row: dict) -> str:
        """Compose reads its own scan field, and which one depends on the paging branch that runs."""
        if counter != "printings_examined" or plan != "PrintingCompose":
            return {"cards_visited": "eval_domain", "printings_examined": "scan_units", "matches_pushed": "matches"}[counter]
        return "compose_scan_printings" if row["acq"].get("compose_paging") == "Gather" else "printings_walked"

    out: dict[str, list[tuple[str, float]]] = {}
    by_plan: dict[str, list[dict]] = collections.defaultdict(list)
    for s in samples:
        by_plan[s["plan"]].append(s)
    for plan, rows in sorted(by_plan.items()):
        instrumented = [r for r in rows if r["ns_round_total"] and r["cards_visited"]]
        if not instrumented:
            continue  # only the two scan plans carry counters; absent is not the same as wrong
        checks = []
        for counter, _default in pairs:
            # Only grade rows whose arm actually multiplies the feature by a rate. StreamedSelect's
            # scan term is `if tier_ns > 0.0 { scan_units * ... } else { 0.0 }`, so on an all_match
            # query (tier 0) `scan_units` is a number the model never reads -- and grading it anyway
            # read 0.65 here and vetoed the whole plan's fit over a term that contributes zero.
            graded = instrumented
            if counter == "printings_examined" and plan == "StreamedSelect":
                graded = [r for r in instrumented if r["acq"]["residual_tier_ns100"] > 0]
            # Compose's three paging branches charge different features, so grading a row against a
            # feature its branch never multiplies by a rate manufactures a defect. Its Gather branch
            # charges eval_domain/compose_scan_printings/matches; Perm and OrderbyWalk charge only
            # printings_walked and stop at page_offset+limit, so their `cards_visited` is 0 (the
            # orderby walk steps a value structure, not cards) and their `matches_pushed` is a page,
            # not a total. Ungated, those read 0.02 and 0.01 and vetoed the plan's whole fit.
            if plan == "PrintingCompose":
                gather_only = counter in ("cards_visited", "matches_pushed")
                graded = [r for r in graded if (r.get("paging_taken") in ("Gather", "GatherWalkDeclined")) == gather_only]
            if not graded:
                continue
            got = [r[counter] / max(r["acq"][feature_for(plan, counter, r)], 1) for r in graded]
            feature = feature_for(plan, counter, graded[0])
            if got:
                checks.append((f"{counter}/{feature}", statistics.median(got)))
        if checks:
            out[plan] = checks
    return out


def collect(engine: object, rng: random.Random, seconds: float, sampler: QuerySampler | None = None) -> list[dict]:
    """Sample queries until the budget runs out, keeping one row per plan that actually ran."""
    if sampler is None:
        sampler = QuerySampler(REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl", "uniform")
    samples: list[dict] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        limit, offset = rng.choice(LIMITS), rng.choice(OFFSETS)
        kw = {
            "filters": None,
            "unique": sampler.unique(rng),
            "orderby": sampler.orderby(rng),
            "direction": rng.choice(("asc", "desc")),
            "limit": limit,
            "offset": offset,
        }
        q = sampler.query(rng)
        try:
            kw["filters"] = parse_scryfall_query(q)
            acq = engine.explain(**kw)["acquire"]
            res = engine.explain_analyze(prefer="default", num_warmups=NUM_WARMUPS, num_trials=NUM_TRIALS, **kw)
        except Exception:  # noqa: BLE001, S112 - a rejected query is a skipped sample
            continue
        for p in res["plans"]:
            # `costbench.plan_self_ns` is this rule, now shared: net `ns_prepare` except under a
            # range acquire, and DROP the row when the subtraction overshoots. Dropping beats
            # clamping here in particular -- a row scaled by 1/1ns swamps the Gram matrix and drags
            # every coefficient to zero. `predicted_ns` screens the infinite cost a declining
            # compose reports, which no `<= 0` guard catches.
            measured = costbench.plan_self_ns(p, acq)
            if not p["trials_ns"] or costbench.predicted_ns(p) is None or measured is None:
                continue
            samples.append(
                {
                    "plan": p["plan"],
                    "q": q,
                    "unique": kw["unique"],
                    "acq": acq,
                    "limit": limit,
                    "offset": offset,
                    "measured": measured,
                    "predicted": float(p["predicted_ns"]),
                    "ns_round_total": p["ns_round_total"],
                    "cards_visited": p["cards_visited"],
                    "ns_setup": p["ns_setup"],
                    "ns_loop": p["ns_loop"],
                    "ns_finish": p["ns_finish"],
                    "printing_span": p["printing_span"],
                    "paging_taken": p.get("paging_taken"),
                    "printings_examined": p["printings_examined"],
                    "matches_pushed": p["matches_pushed"],
                    "perm_steps": p.get("perm_steps", 0),
                }
            )
    return samples


def mirror_matches_engine(samples: list[dict]) -> tuple[float, int]:
    """Fraction of rows where this file's arm mirror equals the engine's own `predicted_ns`.

    `design_row` + `CURRENT` is a Python reimplementation of `cost::plan_cost`. If it has drifted, the
    fitter is fitting coefficients for a model the engine does not run, and every number it prints is
    meaningless. Cheap to check exactly, because `explain` reports the engine's prediction.
    """
    ok = total = 0
    for x in samples:
        built = design_row(x["plan"], x["acq"], x["limit"], x["offset"])
        if built is None or x["predicted"] <= 0:
            continue
        vec, _, excess = built
        mine = sum(c * v for c, v in zip(CURRENT[x["plan"]], vec, strict=True)) + excess
        total += 1
        ok += abs(mine / x["predicted"] - 1.0) < MIRROR_TOLERANCE
    return (ok / total if total else 0.0), total


def fit_plan(plan: str, rows: list[dict], label: str | None = None) -> tuple[list[str], list[float]] | None:
    """Fit and report one plan's arm: current vs fitted coefficient, and the agreement each gives.

    Returns the fitted `(names, coeffs)` so a caller partitioning by distinct-on can compare them
    across modes; `None` when the plan has no fittable design.
    """
    design, names, targets = [], None, []
    for r in rows:
        built = design_row(plan, r["acq"], r["limit"], r["offset"])
        if built is None:
            # Skip the ROW, not the plan. Compose's Decline branch costs infinity and so has no arm to
            # fit, but `explain_analyze` runs every plan regardless of the model, so 440 of 2,886
            # compose rows come back Decline-and-measured. Aborting the plan on the first of them is
            # why PrintingCompose silently never got fitted.
            continue
        vec, names, excess = built
        design.append(vec)
        # Fit and score the part coefficients control: the residual EXCESS over the floor is not
        # scaled by any of them, so it comes off the target rather than riding as a column.
        targets.append(max(r["measured"] - excess, 1.0))
    # One row per DISTINCT feature vector, at its median measured time. The sampler draws from a
    # fixed predicate pool, so a few hundred distinct query shapes turn into tens of thousands of
    # rows; left duplicated, the fit optimises whichever shape recurs most and degenerates to a
    # constant (measured: a 19.8 µs StreamedSelect FIXED term and every per-unit rate at zero).
    if names is None:
        return None
    grouped: dict[tuple[float, ...], list[float]] = collections.defaultdict(list)
    for vec, y in zip(design, targets, strict=True):
        grouped[tuple(vec)].append(y)
    design = [list(k) for k in grouped]
    targets = [statistics.median(v) for v in grouped.values()]
    weights = [float(len(v)) for v in grouped.values()]
    coeffs = fit_log_ratio(design, targets, CURRENT[plan], weights)

    print(f"\n=== {label or plan} ({len(rows):,} rows, {len(design):,} distinct shapes) ===")
    print(f"{'term':<30}{'current':>12}{'fitted':>12}{'x':>8}")
    for name, cur, c in zip(names, CURRENT[plan], coeffs, strict=True):
        print(f"{name:<30}{cur:>12.2f}{c:>12.2f}{c / cur if cur else math.inf:>8.2f}")

    # Both scored on the same deduplicated shapes, so the comparison is like for like.
    before, after = [], []
    for d, y, w in zip(design, targets, weights, strict=True):
        for coefs, out in ((CURRENT[plan], before), (coeffs, after)):
            pred = sum(c * v for c, v in zip(coefs, d, strict=True))
            out.extend([y / pred if pred > 0 else math.inf] * int(w))
    for tag, ratios in (("current", before), ("fitted", after)):
        finite = [x for x in ratios if math.isfinite(x)]
        near = sum(1 for x in finite if AGREE_LO <= x <= AGREE_HI) / len(finite)
        qs = statistics.quantiles(finite, n=10)
        print(
            f"  {tag:<8} median {statistics.median(finite):>6.2f}   p10 {qs[0]:>6.2f}   p90 {qs[8]:>7.2f}   within 25% {near:>5.0%}"
        )
    return names, coeffs


def fit_by_mode(plan: str, rows: list[dict]) -> None:
    """Fit one arm separately per distinct-on, and show how far the coefficients move.

    A single arm is fitted across all three modes today, on the assumption that distinct-on changes
    only the FEATURES (`scan_units`, `matches`) and not the per-unit costs. Where that assumption
    holds, the three fits land on the same rates and the split is just noise. Where they diverge
    sharply, the arm is missing a mode-dependent term and no single set of constants can serve all
    three — the fit will land on a compromise that is wrong everywhere.

    This is a diagnostic, not a source of shippable constants: each partition sees a third of the
    rows, so a term that is weakly identified overall becomes noisy here. Read the SPREAD, and treat
    a flagged term as a question about the arm's shape rather than as three numbers to hard-code.
    """
    by_mode: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_mode[r["unique"]].append(r)
    fits: dict[str, tuple[list[str], list[float]]] = {}
    for mode, mrows in sorted(by_mode.items()):
        if len(mrows) < MIN_ROWS_FOR_FIT:
            continue
        got = fit_plan(plan, mrows, label=f"{plan} / {mode}")
        if got is not None:
            fits[mode] = got
    if len(fits) < 2:  # noqa: PLR2004 - nothing to compare against
        return
    modes = list(fits)
    names = fits[modes[0]][0]
    print(f"\n--- {plan}: coefficient spread across distinct-on ---")
    print(f"{'term':<30}" + "".join(f"{m:>11}" for m in modes) + f"{'max/min':>10}")
    for i, name in enumerate(names):
        vals = [fits[m][1][i] for m in modes]
        lo, hi = min(vals), max(vals)
        # A term at ~0 in every mode is unidentified, not mode-dependent; ignore it either way.
        ratio = hi / lo if lo > MODE_SPLIT_MIN_RATE else math.inf
        flag = "  MODE-DEPENDENT" if hi > MODE_SPLIT_MIN_RATE and ratio > MODE_SPLIT_FACTOR else ""
        shown = "   inf" if math.isinf(ratio) else f"{ratio:>10.2f}"
        print(f"{name:<30}" + "".join(f"{v:>11.2f}" for v in vals) + f"{shown}{flag}")
    print(f"  Flagged where the rate moves more than {MODE_SPLIT_FACTOR}x between modes: that is the arm")
    print("  missing a mode-dependent term, not three constants waiting to be hard-coded.")


def main() -> None:
    """Collect a sample, verify features track counters, then fit each scan plan's rates."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    parser.add_argument(
        "--by-mode",
        action="store_true",
        help="also fit each arm separately per distinct-on, to expose rates that are not mode-independent",
    )
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".fit.store"))
    # Pass the sampler explicitly: `collect`'s fallback builds one off a hardcoded corpus path, so
    # `--corpus` was loading the engine from one file and drawing queries from another (or, off the
    # default checkout, raising FileNotFoundError). Values must come from the corpus the engine holds.
    samples = collect(engine, random.Random(args.seed), args.seconds, QuerySampler(args.corpus, "uniform"))
    print(f"\n{len(samples):,} plan-rows collected in {args.seconds:.0f}s")

    agree, checked = mirror_matches_engine(samples)
    print(f"arm mirror vs engine predicted_ns: {agree:.1%} exact over {checked:,} rows")
    if agree < MIRROR_MIN_AGREEMENT:
        print(
            f"  REFUSING TO FIT: the Python mirror of cost.rs disagrees with the engine on "
            f"{1 - agree:.1%} of rows. `design_row`/`CURRENT` have drifted from the shipped arms; any "
            f"coefficients fitted now would be for a model the engine does not run. Sync them first."
        )
        return

    print(f"\n{'plan':<20}{'counter / feature':<40}{'median':>9}")
    suspect: set[str] = set()
    for plan, checks in counter_check(samples).items():
        for label, ratio in checks:
            flag = "" if abs(ratio - 1.0) <= COUNTER_TOL else "  <-- FEATURE, not rate"
            if flag:
                suspect.add(plan)
            print(f"{plan:<20}{label:<40}{ratio:>9.2f}{flag}")
    print("  a ratio far from 1.00 is a miscounted feature; no rate can absorb it.")
    perm = perm_step_check(samples)
    if perm is not None:
        rows, p10, med, p90 = perm
        print(f"\nStreamedSelect perm_steps realized/estimated over {rows:,} walking rows:")
        print(f"  p10 {p10:.2f}   median {med:.2f}   p90 {p90:.2f}")
        print("  tests the uniform-spread assumption behind `page_span * n_cards / matches`; skew would")
        print("  show as a median away from 1.00, and clustering as a wide p10-p90 spread.")

    by_plan: dict[str, list[dict]] = collections.defaultdict(list)
    for s in samples:
        by_plan[s["plan"]].append(s)
    for plan, rows in sorted(by_plan.items()):
        fittable = any(design_row(plan, r["acq"], r["limit"], r["offset"]) is not None for r in rows)
        if len(rows) < MIN_ROWS_FOR_FIT or not fittable:
            continue
        if plan in suspect:
            print(f"\n=== {plan} — SKIPPED: fix the feature above before fitting rates to it ===")
            continue
        fit_plan(plan, rows)
        if args.by_mode:
            fit_by_mode(plan, rows)


if __name__ == "__main__":
    main()
