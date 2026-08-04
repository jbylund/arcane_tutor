//! The design parameters `bench_gather_loop` (P4) and `bench_streamed_loop` (P3) must share.
//!
//! The two harnesses exist as a pair for one reason: P4 could not be fixed alone. Three successively
//! better descriptions of its loop each REGRESSED routing (+43%, +8.8%, +40%), because `plan_cost` is only
//! ever used comparatively and P4's inflated arm was absorbing an over-estimate on P3's side. So both arms
//! get the same built-design treatment and are meant to be read side by side.
//!
//! That reading is only valid if the cells match. They had drifted: `CARD_COUNTS` was `[100, 400, 1500,
//! 4500]` on the gather side and `[600, 1500, 4500]` on the streamed side, sharing just two sizes, so any
//! P3-vs-P4 rate difference at the small end was confounded with a design difference. Living here, one
//! definition, they cannot drift again — which is the same argument `divergent_formats_of` and
//! `perm_primary_key` are single functions.
//!
//! Neither harness compares the two plans itself; each decomposes one plan's own loop. The per-query
//! comparison is `explain_analyze`'s (predicted and measured for every plan at once) — see
//! `docs/workflows/diagnosing-a-plan-cost-error.md`.

/// Timed repetitions; the minimum per cell is reported, and all cells run inside this loop so every
/// cell's minimum is drawn from the same time window. Running cells to completion one at a time let
/// machine drift enter the fit as a rate difference — 1.8× between cells with identical counters — so
/// both harnesses interleave from the start.
pub(crate) const ITERS: usize = 200;

/// A "wide" card has at least this many printings; below it and above 1 is "medium". Three levels of
/// printings-per-card is what identifies two rates per mode with a degree of freedom left over, and it
/// sets the leverage between the card column and the printing column. 4 keeps that leverage worth having
/// while leaving the wide group ~4× bigger than at 8, where only 1,910 cards qualify.
pub(crate) const WIDE_MIN_PRINTINGS: usize = 4;

/// Card counts every cell runs at, on both harnesses. The UNION of what each needed separately, because
/// each size is load-bearing for a different question and dropping any of them loses coverage:
///
/// - **100, 400** — the INTERCEPT. A per-query fixed cost only exists as the y-intercept of cells that
///   vary in size, and `intercept = t(n) - slope * n` multiplies any slope error by `n`: at a 400-card
///   floor a 1 ns/card slope error is already 400 ns against a shipped `GATHER_FIXED_COST_NS` of 169.6.
///   Adding 100 is also what revealed the per-card average is U-SHAPED rather than monotone, which
///   retracted the "negative intercept ⇒ the loop is convex" finding.
/// - **600** — BELOW `STREAM_MIN_MATCHES` (1,024) in card mode, where matches == cards. P3's finish phase
///   branches there: at or under the threshold the small-total gather scans `0..n_cards`, above it the
///   permutation walk steps until the page fills. Every earlier cell exceeded it, so that branch and
///   `STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS` with it had never been measured.
/// - **1,500, 4,500** — the linearity range, bounded above by the scarce wide group.
///
/// Five sizes on both sides costs the gather harness 25% more cells and the streamed one 67%, against
/// runtimes of under a second and a few seconds respectively.
pub(crate) const CARD_COUNTS: [usize; 5] = [100, 400, 600, 1_500, 4_500];

/// Page requested. Small and fixed on both harnesses so the finish phase stays out of the loop
/// measurement — P4's `sel.absorb()` prunes toward `offset + limit` INSIDE its loop, so holding it
/// constant keeps that contribution proportional to matches rather than to the page, and P3's `ns_finish`
/// branches on `total` rather than on the page at all.
pub(crate) const LIMIT: usize = 60;

/// Default store for both harnesses. `BENCH_LOOP_STORE` overrides it, which is how the corpus-size sweep
/// runs: build upscaled stores with `scripts/upscale_corpus.py` and point this at each in turn.
///
/// The real corpus is only ~68 MB, small enough that a full chunk rotation can stay resident in the
/// system-level cache, so the rates it yields are still partly warm however the walk is ordered.
const DEFAULT_STORE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../benchmarks/verify-order/real.store");

pub(crate) fn store_path() -> String {
    std::env::var("BENCH_LOOP_STORE").unwrap_or_else(|_| DEFAULT_STORE_PATH.to_string())
}
