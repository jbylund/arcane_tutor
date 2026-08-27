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
