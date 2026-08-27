//! Closed-form safety bounds on `Perm`'s worst-case `cards_visited`, for the walk-vs-three-phase
//! decision rule (step 3 of `docs/issues/local-engine-compose-perm-sigma-decision-rule.md`; step 1,
//! the two closed-form anchors, was found and Monte-Carlo-verified in
//! `scripts/bench_compose_card_visited_safety_bound.py`). Direct port, not a new derivation — see
//! `sigma_bound_matches_python_fixture` for the differential check against that Python original.
//!
//! NOT wired into `plan_cost` or the dispatch path yet. Wiring the decision into
//! `printing_compose_fastpath`'s `Perm` arm is step 5; this only makes the math available in Rust.
//!
//! All three inputs are exact, never estimates: `n_cards` and `matches` (`M`) are already computed
//! before the `Perm` branch runs (`compose_total_for_mode`), and `k` is the rank of the match that
//! fills the requested page. That is what makes this immune to the EDHREC-order clumping that broke
//! every attempt to estimate `cards_visited` directly (`reference-engine-compose-perm-cards-visited-
//! estimator.md`) — it never reads WHERE matches sit in the permutation, only how many there are.
//!
//! ## Provenance and re-fitting `THREE_PHASE_BREAKPOINTS`
//!
//! Fit on this session's `real.store` (31,724 cards, 97,812 printings), same methodology `cost.rs`'s
//! own module doc asks for its constants: named data points, a stated recalibration trigger, not just
//! numbers. Recalibrate when the corpus size changes meaningfully (more cards/printings shifts every
//! `set_printings` value this table is keyed by) or `walk_card_page_via_popcount_skip`'s scatter
//! phase itself changes — a uniform hardware speed change alone does NOT require it, since step 5's
//! use of this table is a within-process comparison against the real walk's own measured cost, not an
//! absolute threshold.
//!
//! To regenerate:
//!
//! 1. `cargo test --release three_phase_walk_rate_fit -- --ignored --nocapture` against the target
//!    corpus's `real.store`. Uses `WARMUP=200, ITERS=5000`, not this file's usual 20/200 -- an
//!    earlier attempt at 20/200 found one point at 20.7% run-to-run CV against neighbors' 5-9%,
//!    which looked like a genuine per-point hardware anomaly but turned out to be simple
//!    under-sampling: raising the trial count alone (before even repeating runs) dropped that same
//!    point to 1.6% CV, in line with everything else. An outlier point in a min-of-N sweep is
//!    evidence the sample size hasn't converged there yet, not evidence of a real per-point effect --
//!    fix the trial count before reaching for more runs. Prints a ready-to-paste
//!    `const THREE_PHASE_BREAKPOINTS` block.
//! 2. Run it a few times and take the MEDIAN at each point, then a running max in `set_printings`
//!    order for monotonicity (never decrease moving right). At 200/5000 per-run spread is small
//!    (1-4% CV almost everywhere), so this is a modest refinement over trusting one run, not the load
//!    -bearing fix the trial count itself is.
//! 3. Paste the result in place of the block below.
//! 4. `cargo test --release three_phase_cost_ns_matches_breakpoints_and_is_monotonic` — this is NOT
//!    optional: it is what catches a new table that came out locally non-monotonic.
//! 5. `cargo test --release three_phase_cost_ns_predicts_held_out_densities -- --ignored --nocapture`
//!    (also bumped to 200/5000, for the same reason -- its own reported mean/worst-case ratio swung
//!    wildly across repeat runs at 20/200: mean 0.874-1.022, worst case 7.0%-28.2%, useless as
//!    single-run evidence) to re-check prediction quality on densities the table was NOT fit from
//!    (interpolation is exact at the fitted points by construction, which says nothing about accuracy
//!    between them). At 200/5000 this table's MEAN ratio is now tight and near-unbiased across
//!    repeats (0.987-1.013 over 5 runs) -- no longer the safe-leaning over-prediction the very first
//!    (discarded) fitting attempt had. Its WORST-CASE ratio still moves some (14.6%-31.2% over the
//!    same 5 runs), but with the mean this stable that spread is genuine piecewise-linear
//!    approximation error at a small number of points near real curvature, not leftover measurement
//!    noise -- which point is "worst" shifts slightly between runs because several points sit in a
//!    comparable error band, not because any one of them is itself unstable.
//! 6. `cargo test --release three_phase_cost_ns_error_distribution -- --ignored --nocapture` for the
//!    question the 16-point spot-check above can't answer: what FRACTION of the time is the
//!    prediction actually close, not just what the single worst case is. A rare bad case can coexist
//!    with an otherwise very usable prediction, or it can be typical -- only a real distribution over
//!    many random points (150 here, log-uniform over the whole fitted density range, not just the 16
//!    log-midpoints) tells the two apart. On this table: **50% of predictions land within 5% of the
//!    real cost, ~90% within 10%, and 100% within 20%** (two runs: p50 error 0.050/0.051, p90
//!    0.084/0.116, max observed 0.176/0.228) -- the spot-check's 14.6%-31.2% "worst case" was real but
//!    rare, not representative of a typical prediction.
//!
//! **Open question for whoever wires step 5**: this table's mean prediction is now close to unbiased,
//! unlike `worst_case_bound`/`sigma_bound` above, which are deliberately safe-biased ("never wrong to
//! be too cautious"). Given the error distribution above (typically single-digit percent, rarely up
//! to ~20-25%), whether the dispatch decision needs an explicit safety multiplier on top of
//! `three_phase_cost_ns`'s raw prediction -- and how large, now that the shape of the risk is known
//! rather than just its worst observed value -- is a real design choice, not decided here.

/// Every non-matching card clumped before the k-th match — an exact, unconditional ceiling on
/// `walk_grouped_page`'s `cards_visited`. `k > matches` means the page never fills, so the walk
/// exhausts the permutation rather than stopping at a last match that doesn't exist.
// Ported ahead of step 5 (docs/issues/local-engine-compose-perm-sigma-decision-rule.md); not yet
// called from the dispatch path.
#[allow(dead_code)]
pub(crate) fn worst_case_bound(n_cards: usize, matches: usize, k: usize) -> f64 {
    if k > matches {
        return n_cards as f64;
    }
    (n_cards.saturating_sub(matches)) as f64 + k as f64
}

/// Expected position of the k-th match if `matches` cards were scattered with NO clumping at all —
/// the order-statistic mean of a uniformly random `matches`-subset of `n_cards` slots.
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) fn uniform_mean(n_cards: usize, matches: usize, k: usize) -> f64 {
    if k > matches {
        return n_cards as f64;
    }
    (k as f64) * (n_cards as f64 + 1.0) / (matches as f64 + 1.0)
}

/// Variance of that same no-clumping position (negative hypergeometric, closed form). Zero at the
/// edges by construction: `matches == n_cards` (nothing to scatter, position is exactly `k`) and
/// `k == 0`.
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) fn nhg_variance(n_cards: usize, matches: usize, k: usize) -> f64 {
    if matches == 0 || k == 0 || k > matches || matches >= n_cards {
        return 0.0;
    }
    let (n, m, k) = (n_cards as f64, matches as f64, k as f64);
    k * (n + 1.0) * (n - m) * (m - k + 1.0) / ((m + 1.0).powi(2) * (m + 2.0))
}

/// `knob` = how many std devs above the no-clumping mean, under the same random-placement model
/// `worst_case_bound`/`uniform_mean` share. A statistical margin is never usefully more conservative
/// than the exact unconditional ceiling, so capped there.
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) fn sigma_bound(n_cards: usize, matches: usize, k: usize, knob: f64) -> f64 {
    let mean = uniform_mean(n_cards, matches, k);
    let margin = mean + knob * nhg_variance(n_cards, matches, k).sqrt();
    worst_case_bound(n_cards, matches, k).min(margin)
}

/// Piecewise-linear model of `walk_card_page_via_popcount_skip`'s real cost, keyed by `set_printings`
/// (total set bits in the composed `pbits`), NOT the distinct-card `matches` count -- see
/// `three_phase_walk_rate_fit` (`tests.rs`) for why `matches` is the wrong variable: it saturates
/// toward `n_cards` at high density while the scatter's real work keeps growing with the number of
/// set PRINTING bits it must visit. `set_printings` (`popcount(pbits)`) is cheap to compute -- one
/// word-count pass, cheaper than the scatter itself -- but nothing currently computes it before the
/// `Perm` branch runs; a caller wiring this in needs to add that.
///
/// A single straight line badly misprices this curve (R² caps around 0.98 with real, non-random
/// residual structure, not noise): a fixed ~1,000ns floor dominates below roughly 1,000 set
/// printings (the scatter's outer loop always scans every word of `pbits` regardless of how many are
/// set), cost then grows well past a linear rate through a middle band, and roughly re-linearizes
/// near saturation. `three_phase_scatter_phase_kernel_costs` (`tests.rs`) root-caused both ends: the
/// floor to the fixed word-scan (confirmed by ruling OUT the per-call `permuted` allocation as an
/// alternative explanation), the mid-band rise to a plausible cache-capacity effect in `order.inv`'s
/// and `permuted`'s random-position accesses (plausible, not proven with a profiler -- no
/// perf-counter access in this harness). Piecewise-linear interpolation between real measured points
/// is exact at every knot and reasonable between them, without pretending the true curve is a line.
///
/// See this module's own doc ("Provenance and re-fitting") for what corpus/machine this was fit on,
/// when to recalibrate, and the exact steps. Y-values are the MEDIAN of 15 independent runs at each
/// `set_printings` point (each a full re-fit at `WARMUP=200, ITERS=5000` -- see
/// `three_phase_walk_rate_fit`'s own comment for why that trial count, not the file's usual 20/200,
/// matters here), then a running max in `set_printings` order for monotonicity.
///
/// An earlier attempt at 20/200 found one point at 20.7% run-to-run CV against neighbors' 5-9% and
/// treated it as a possible genuine per-point hardware anomaly worth flagging rather than explaining.
/// It wasn't: raising the trial count to 200/5000 alone (before even repeating runs) dropped that same
/// point to 1.6% CV, in line with every other point's 1-4%. The lesson generalizes -- an outlier point
/// in a min-of-N sweep is evidence the sample size hasn't converged there yet, not evidence of a real
/// per-point effect, and the fix is more trials before it is more runs.
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) const THREE_PHASE_BREAKPOINTS: [(u32, f64); 17] = [
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
];

/// Linear interpolation of `THREE_PHASE_BREAKPOINTS` at `set_printings`. Clamped below the first
/// breakpoint and above the last -- extrapolating a two-regime curve past its measured range risks
/// being wrong in either direction, and the last breakpoint already sits at the fitting corpus's
/// `n_printings`, which `set_printings` can never exceed on that corpus.
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) fn three_phase_cost_ns(set_printings: usize) -> f64 {
    let x = set_printings as u32;
    let n = THREE_PHASE_BREAKPOINTS.len();
    if x <= THREE_PHASE_BREAKPOINTS[0].0 {
        return THREE_PHASE_BREAKPOINTS[0].1;
    }
    if x >= THREE_PHASE_BREAKPOINTS[n - 1].0 {
        return THREE_PHASE_BREAKPOINTS[n - 1].1;
    }
    for i in 1..n {
        let (x1, y1) = THREE_PHASE_BREAKPOINTS[i];
        if x <= x1 {
            let (x0, y0) = THREE_PHASE_BREAKPOINTS[i - 1];
            let t = (x - x0) as f64 / (x1 - x0) as f64;
            return y0 + t * (y1 - y0);
        }
    }
    unreachable!("x is bounded by the first/last breakpoint checks above")
}
