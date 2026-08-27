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
//!    corpus's `real.store`. Prints a ready-to-paste `const THREE_PHASE_BREAKPOINTS` block.
//! 2. **Run it at least twice and compare.** A single run on this session's machine showed real
//!    run-to-run variance of 5-15% at several points, not just the expected few-hundred-ns wobble
//!    near the floor (this machine is shared with other concurrent work) -- one bad run produced a
//!    table with a large spurious jump that a second run didn't reproduce. Take the ELEMENT-WISE MAX
//!    across runs, then a running max in `set_printings` order (never decrease moving right) -- both
//!    only ever move a value UP, the safe direction for a cost this table exists to avoid
//!    under-predicting.
//! 3. Paste the result in place of the block below.
//! 4. `cargo test --release three_phase_cost_ns_matches_breakpoints_and_is_monotonic` — this is NOT
//!    optional: it is what catches a new table that came out locally non-monotonic.
//! 5. `cargo test --release three_phase_cost_ns_predicts_held_out_densities -- --ignored --nocapture`
//!    to re-check prediction quality on densities the table was NOT fit from (interpolation is exact
//!    at the fitted points by construction, which says nothing about accuracy between them). This
//!    table (17 points): mean ratio 1.051, worst case 23.9% (safe-direction over-prediction at
//!    density≈0.35). The original 14-point table's worst case was 57.5%, at density≈0.28, squarely
//!    inside the widest, steepest-curvature gap in that table (19,441 to 39,008 set printings, cost
//!    jumping 4.3x); THIS table's 3 extra points (0.03/0.25/0.3 density) were placed specifically to
//!    bisect that gap and the second-worst one, not spread evenly -- 3 targeted points did more than a
//!    blind jump to 20-30 uniformly-spaced ones would have. If a re-fit still needs tighter accuracy,
//!    the same recipe applies: read this step's own output for whichever gap is worst NOW and bisect
//!    it, rather than re-sampling gaps that already interpolate well.

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
/// when to recalibrate, and the exact steps. Y-values are the ELEMENT-WISE MAX of two separate
/// fitting runs, then a running max in `set_printings` order -- one run alone showed real run-to-run
/// variance of 5-15% at several points (not just the expected few-hundred-ns wobble near the floor;
/// this machine is shared with other concurrent work), so a single run's numbers were not trusted
/// as-is. Combining two clean runs this way can only ever move a value UP, which is the safe direction
/// for a cost this table exists to avoid under-predicting.
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) const THREE_PHASE_BREAKPOINTS: [(u32, f64); 17] = [
    (23, 750.0),
    (55, 750.0),
    (97, 750.0),
    (215, 875.0),
    (477, 1333.0),
    (963, 2333.0),
    (1960, 3458.0),
    (2924, 4208.0),
    (4885, 5583.0),
    (9733, 9541.0),
    (19356, 19083.0),
    (24577, 25041.0),
    (29453, 40708.0),
    (39101, 68625.0),
    (58725, 138667.0),
    (78417, 175916.0),
    (97812, 228750.0),
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
