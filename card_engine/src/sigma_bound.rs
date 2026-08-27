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
/// Fit on THIS session's `real.store` (31,724 cards, 97,812 printings) -- needs re-fitting against
/// the production corpus/machine before any live use. Regenerate with `cargo test --release
/// three_phase_walk_rate_fit -- --ignored --nocapture`, which prints this table ready to paste.
/// Y-values are the raw measurements' running max (a handful of the lowest points measured a few
/// hundred ns of run-to-run noise around the floor, which would otherwise make the table locally
/// non-monotonic -- physically, more set printings should never measure as strictly faster).
// Ported ahead of step 5; not yet called from the dispatch path.
#[allow(dead_code)]
pub(crate) const THREE_PHASE_BREAKPOINTS: [(u32, f64); 14] = [
    (23, 1042.0),
    (55, 1042.0),
    (97, 1083.0),
    (215, 1333.0),
    (477, 2000.0),
    (963, 2166.0),
    (1960, 5042.0),
    (4878, 7916.0),
    (9918, 14334.0),
    (19441, 20000.0),
    (39008, 86416.0),
    (58671, 151000.0),
    (78165, 178917.0),
    (97812, 208292.0),
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
