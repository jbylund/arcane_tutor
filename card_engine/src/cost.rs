//! Per-plan cost model (#702 step 3b).
//!
//! Parametric cost formulas — one per `PhysicalPlan` — whose constants are FIT
//! to the `plan_cost_calibration` bench (src/tests.rs) measured on the real
//! corpus archive (`benchmarks/verify-order/real.store`: 31508 cards, 97206
//! printings). The routing decision this feeds is `argmin_plan plan_cost`; the
//! objective the constants were fit against is that `argmin` reproduces the
//! empirically-fastest ("gold") plan per query × mode × page depth.
//!
//! `run_query_routed` calls `plan_cost` on every query (it IS the plan selector).
//! It is also validated by the `plan_cost_model_matches_gold` test (src/tests.rs),
//! which computes real `PlanFeatures` (via `prepare_candidates` + `verify_cost_tier`)
//! and checks the model's argmin against re-measured gold.
//!
//! ## Units and provenance
//!
//! Constants are in nanoseconds (or ns per unit of work), fit from the
//! calibration table dated 2026-07-19 on this machine (min-of-60, warmup 5, real
//! corpus). Per the "Keeping costs/plans current" section of
//! docs/issues/00702-engine-plan-selection-layer.md: `argmin` cares about the
//! *ratios* between plans, so a uniform hardware speed change preserves the
//! choice; recalibrate on non-uniform changes (a plan reimplemented, a new index
//! shifting a predicate class, a new plan). Each constant's doc-comment names the
//! data point(s) it was fit from, mirroring `verify_cost_tier`'s provenance style.
//!
//! ## Predicate cost is common-mode
//!
//! The per-card verify tier (`residual_tier_ns100`) is added to BOTH the gather
//! and stream per-card terms, so it largely cancels in their argmin — cardinality
//! and plan structure do the deciding (see #702 "Cost model" §). Popcount (P2)
//! and range-scan (P1) run only when the residual is `True`/absent, so they carry
//! no verify term at all.
//!
//! ## Calibration scope: operating-space via `scan_units` (card + printing)
//!
//! The P3/P4 per-card work was originally fit on CARD mode alone, where the loop
//! breaks at the first matching printing, and it under-predicted printing/artwork
//! P3/P4 by ~`n_printings/n_cards` (≈3.09) because those modes scan EVERY printing
//! of every candidate. The fix is `PlanFeatures::scan_units` (not a `mode` branch):
//! the per-card `card_pass` term is driven by `eval_domain` (candidate cards) and
//! the per-row residual scan + its verify `tier` by `scan_units` (printings under the
//! candidate cards). One mode-agnostic formula, and `scan_units()` no longer branches
//! on mode at all: the `printings_scanned` counter shows the scan plans walk the full
//! printing span of their candidates in CARD mode too, not one row each. The `_CARD_PASS`/`_SCAN` split of the old lumped
//! `VISIT` constants was fit to hold card unchanged while correcting printing (see
//! each constant's doc). Artwork rides the printing path (same all-printings scan);
//! its confirming validation is still pending a bench run.
//!
//! A 1200-query designed refit (`plan_cost_refit`, weighted LSQ, 70/30 train/test)
//! VALIDATED rather than beat these: P1's fitted STEP=4.14 ≈ 4.5 (test 1.38× ≈
//! train); P3/P4 could NOT be fit — `SCAN` goes negative because `scan_units` and
//! `matches` both scale with printing count in the workload, a STRUCTURAL
//! collinearity no corpus size fixes (P2 stays data-starved: pure-plane queries are
//! rare). The `_CARD_PASS`/`_SCAN`/`PUSH` split is a physical prior resolving what
//! data alone cannot. Model sits at ~1.4× absolute (slow bucket), ordering-correct
//! (argmin==gold 87/88) — the identifiable ceiling for this workload.

use super::*;

/// Cheap, per-query features the cost model consumes, built once per query by
/// `run_query_routed`'s `acquire` step. All counts are exact or cheap-exact (plane
/// popcount / range `k` / candidate count), never estimated.
#[derive(Clone)]
pub(crate) struct PlanFeatures {
    /// Distinct cards in the corpus (card-space universe).
    pub n_cards: u32,
    /// Distinct printings in the corpus (printing-space universe).
    pub n_printings: u32,
    /// Result cardinality in the plan's operating space (card total for card
    /// mode, printing total for printing/artwork mode). Use measured truth here.
    pub matches: u32,
    /// Candidate CARDS the loop iterates (one `card_pass` each): the narrowed
    /// candidate count when `prepare_candidates` produced a list, else `n_cards`.
    pub eval_domain: u32,
    /// Printings under the candidate cards — the dominant P3/P4 driver, and the same
    /// quantity in all three distinct-ons. Card mode was long assumed to break at the
    /// first matching printing (`scan_units ≈ eval_domain`); measured against
    /// `printings_scanned`, that read 0.25-0.33 for both GatheredScan and StreamedSelect
    /// while `eval_domain · n_printings/n_cards` reads 0.90-1.02 in every mode.
    pub scan_units: u32,
    /// Per-card verify cost of the residual, ns×100 (`verify_cost_tier`); `0`
    /// when `all_match_known` (the walk skips `card_pass` entirely).
    pub residual_tier_ns100: u32,
    /// Cards `run_query_streamed` visits in ARTWORK mode, i.e. `eval_domain` there and `0` in card and
    /// printing mode. Charged at `STREAM_ARTWORK_SEEN_PER_CARD_NS`.
    pub artwork_seen_cards: u32,
    /// Printings compose's **Gather** paging branch bit-tests, which is NOT `scan_units`.
    ///
    /// `scan_units` is every printing under a candidate card — right for GatheredScan and
    /// StreamedSelect, which must test each one. Compose walks the set bits of the composed bitmap
    /// instead, so it touches `printing_matches`. Measured against `printings_scanned`, compose reads
    /// 1.00 on `matches` in printing mode and 1.00-1.01 on `project_printings` in artwork (the same
    /// value), while `scan_units` reads 2.0-2.8 for it.
    ///
    /// Sharing one feature between the two forced a compromise that was ~2x wrong for whichever arm
    /// lost: with the value GatheredScan needs, compose over-counts 2x; with compose's, the scan plans
    /// under-count 3.3x.
    /// Printings compose's **Gather** paging branch bit-tests, which is NOT `scan_units`.
    ///
    /// `scan_units` is every printing under a candidate card -- right for GatheredScan and
    /// StreamedSelect, which must test each one. Compose walks the set bits of the bitmap it just
    /// built, so it touches `printing_matches`. Measured against `printings_scanned`, compose reads
    /// 1.00 on `matches` in printing mode and 1.00-1.01 on `project_printings` in artwork (the same
    /// value), while `scan_units` reads 2.0-2.8 for it.
    ///
    /// Sharing one feature between the two forced a compromise ~2x wrong for whichever arm lost.
    pub compose_scan_printings: u32,
    /// Page size (`limit`).
    pub limit: u32,
    /// Page offset.
    pub offset: u32,
    /// Printings the legality **broadcast-down** synthesizes (card ∃-plane → printing bitmap) in
    /// `PrintingCompose`. `0` for border/rarity (precomputed planes) and for bare ranges (no broadcast).
    /// Costed at `LINEAR_PASS_PER_PRINTING_NS`.
    pub broadcast_printings: u32,
    /// The range index's in-range slice `k` — the printings a range leaf contributes. Charged at a
    /// DIFFERENT rate per plan (same `k`, different physical op): `PrintingCompose` scatters it into a
    /// printing bitmap (`RANGE_SCATTER_PER_PRINTING_NS`, cheap, then a separate `project_printings` pass),
    /// while `CardRangePopcount` fuses scatter+project in one pass (`CARD_RANGE_BUILD_PER_PRINTING_NS`).
    /// Set by both range-plan acquire branches so the shared feats cost either winner honestly — the
    /// fused op being cheaper than compose's two passes is why a bare range routes to CardRangePopcount.
    pub scatter_printings: u32,
    /// Printings scattered in `PrintingCompose`'s **projection pass** — printing bitmap → card/artwork
    /// existence, a second O(set) pass on top of the build. `0` for printing mode (no projection) and
    /// for non-compose plans. Costed at `LINEAR_PASS_PER_PRINTING_NS`.
    pub project_printings: u32,
    /// 64-bit words of the **result-space** bitmap the total popcount + skip-scan touches — the field
    /// that keeps the popcount term honest across distinct-ons: `n_printings/64` (printing),
    /// `n_cards/64` (card), `n_artworks/64` (artwork). Set by `PrintingCompose`; `0` elsewhere.
    pub popcount_words: u32,
    /// Which of `PrintingCompose`'s three paging strategies will actually run (see `ComposePaging`),
    /// decided the same way `printing_compose_fastpath` decides. The three have different cost shapes
    /// — the permutation walk and the #744 orderby-index walk are both offset-dependent (fill the page
    /// in ~`page_span/selectivity` steps), while the permutation-free gather visits every match — so
    /// the formula branches on this rather than assuming one. Ignored by every other plan.
    pub compose_paging: super::ComposePaging,
}

// ─── P1: PrintingRangeScan ──────────────────────────────────────────────────
// A bare broad range predicate under unique=printing: total from the range
// index's binary search, page from an early-stopping permutation walk. Cost is
// dominated by how far the walk must go to fill the page, which is
// (offset+limit) matches at density `match_rate` printings.

const RANGE_WALK_STEP_NS: f64 = 4.5;
/// Fixed P1 setup (binary searches + walk init). Fit from usd<5 printing shallow
/// (666ns − 82 steps × RANGE_WALK_STEP_NS ≈ 150ns).
const RANGE_FIXED_COST_NS: f64 = 150.0;
/// Floor on match_rate so a (near-)empty range can't divide by ~0.
const MATCH_RATE_FLOOR: f64 = 1.0 / 1_000_000.0;

// ─── P2: PlanePopcountOrder ─────────────────────────────────────────────────
// unique=card, filter fully consumed to True: the plane bitmap IS the exact
// match set. Scatter the match bits through the inverse permutation (O(matches)),
// scan words for the page (O(N/64)), emit the page. Flat in page depth.

/// ns per match scattered through the inverse permutation. ~0.65 observed:
/// color(bit3) card 6606 matches → 4208ns, t:creature card 17317 → 11375ns both
/// land near 0.65 ns/match with a small floor.
const PLANE_POPCOUNT_SCATTER_PER_MATCH_NS: f64 = 0.65;
/// ns per 64-card word scanned for the page boundary (N/64 = 492 words on this
/// corpus). Small — the popcount word scan is cheap next to the scatter; fit as
/// a modest floor component alongside PLANE_POPCOUNT_FIXED_COST_NS (color3
/// t:creature card ≈4250ns at 4001 matches leaves ~1600ns of floor).
const PLANE_POPCOUNT_PER_WORD_NS: f64 = 1.0;
/// ns per emitted page card. Small; folded into the floor.
const PLANE_POPCOUNT_EMIT_PER_CARD_NS: f64 = 2.0;
/// Fixed P2 setup (plane eval into the bitmap, buffers).
const PLANE_POPCOUNT_FIXED_COST_NS: f64 = 200.0;



/// Per-printing cost of `CardRangePopcount`'s **fused build** — `build_card_range_bits` sets the printing
/// bit AND the card bit (via `printing_to_card`) in one pass over the range slice, fusing compose's
/// scatter+project (0.4 + 1.5 = 1.9) into a single ~1.2 ns/printing pass (`card_range_build_cost_split`'s
/// C 98333ns / 80527 = 1.22). Carries `scatter_printings` in CardRangePopcount's arm — the same `k` as
/// compose's scatter but a cheaper op, which is exactly why a bare range routes here, not to compose.
///
/// Retuned 1.22 -> 0.93 from END-TO-END measurement, which disagrees with that kernel figure. The arm
/// over-costed by a near-uniform 1.20 (p10 0.99, p50 1.20, p90 1.43 — a spread of only 1.4, the
/// signature of a plain rate error), and this term is 80% of it. Its four other constants are shared
/// with PlanePopcountOrder, which is slightly UNDER-costed at 0.92, so they cannot absorb it.
///
/// The disagreement is real, not a sampling artifact, and was checked for exactly that: the implied
/// end-to-end rate is FLAT in k — 0.97 / 0.81 / 0.91 / 0.92 / 0.99 across k bins from 1.5k to 81k — so
/// no single-k distribution is doing the work. At k≈81,479, the same slice size the kernel benchmark
/// uses, end-to-end still implies 0.99 against its 1.26. The kernel times the build in isolation;
/// `plan_cost` predicts end-to-end time, so end-to-end is the figure it should carry. Re-running
/// `card_range_build_cost_split` today still reports 1.26 (101500/80527), so the kernel has not drifted
/// — the two simply measure different things.
pub(crate) const CARD_RANGE_BUILD_PER_PRINTING_NS: f64 = 0.93;

// ─── Candidate materialization ──────────────────────────────────────────────
// `plan_cost` prices only what happens AFTER the acquire step: `eval_domain` and `matches` are its
// inputs, not its outputs. The acquire step is therefore unpriced, and it is where the model's error
// lives — over 40 sampled queries the median `(measured - predicted) / acquire_ns` is 1.09, so
// adding the MEASURED acquire time roughly closes the gap.
//
// `materialize_cost` below is NOT that term, and does not close that gap. It prices one component of
// acquire — the candidate `collect` + `sort_unstable` — which measurement puts at a median 5% of
// acquire (quartiles 2%–40%, n=167 candidate-acquired queries): the rest is index walks, the
// narrowing recursion, `memoize_text_predicates` and feature building. Adding it to `predicted_ns`
// measurably makes absolute accuracy slightly WORSE (73.1% -> 74.9% mean error), because it is a
// small piece of a large omission and does not change which plans compare equal.
//
// So it is REPORTED BY `explain`, NOT ADDED TO `plan_cost`, for three reasons: it is validated as
// too small to help; it is identical for the plans that actually compete (`StreamedSelect` and
// `GatheredScan` call the same `prepare_candidates`), so it cannot change an argmin; and its real
// purpose is to price the bitmap-versus-sort question in
// docs/issues/local-engine-candidate-materialize.md, which is what it does measure exactly.

/// `Vec::with_capacity` plus the run walk, before any comparison work
/// (`bench_candidate_materialize`, axis A).
pub(crate) const MATERIALIZE_SORT_FIXED_NS: f64 = 143.0;
/// pdqsort on `u32`, per candidate — **linear**, not `c·log2 c`. `sort_unstable` is a full pdqsort
/// so it is asymptotically `n log n`, but measured per-element cost is flat across the sizes this
/// engine sees (4.39 ns at 1,024 rising only to 5.09 at 31,508, where an `n log n` fit predicts
/// 4.39 → 6.57). Fit on the rows bracketing the crossover. Re-fit rather than extrapolating past
/// ~3M cards, where the log factor does start to show.
pub(crate) const MATERIALIZE_SORT_PER_CAND_NS: f64 = 4.95;

/// Modelled cost of producing the candidate list a materializing plan consumes, in ns. `0.0` for
/// plans that never build one — those walk or read a plane bitmap directly, and charging them this
/// would invert exactly the comparison the term is meant to inform.
///
/// Prices today's behavior: a `collect` + `sort_unstable`. It does **not** model the bitmap
/// alternative that doc proposes; if that ships, this needs the domain term as well.
///
/// The match below is deliberately NOT `PhysicalPlan::materializing()`, which means something
/// else — "runnable off a materialized prep", and so includes `PlanePopcountOrder`, which reads
/// the plane bitmap directly and builds no candidate list. Charging it here would invert exactly
/// the plane-against-materializing comparison this term exists to inform.
pub(crate) fn materialize_cost(plan: PhysicalPlan, f: &PlanFeatures) -> f64 {
    match plan {
        PhysicalPlan::StreamedSelect | PhysicalPlan::GatheredScan => {
            MATERIALIZE_SORT_FIXED_NS + MATERIALIZE_SORT_PER_CAND_NS * f64::from(f.eval_domain)
        }
        PhysicalPlan::PrintingRangeScan
        | PhysicalPlan::PrintingCompose
        | PhysicalPlan::PlanePopcountOrder
        | PhysicalPlan::CardRangePopcount => 0.0,
    }
}

// ─── P3: StreamedSelect ─────────────────────────────────────────────────────
// Match phase walks eval_domain cards computing per-card counts, then either
// walks the sort permutation to the page (broad) OR — when total <=
// STREAM_MIN_MATCHES — gathers via a `for cid in 0..n_cards` scan and
// quickselects (run_query_streamed, lib.rs). That small-total gather is the
// O(n_cards) FLOOR that makes P3 lose badly on narrow queries: a 5-row query
// forced onto P3 measured ~52µs = n_cards × ~1.65ns.

/// P3 match phase, split into a per-CANDIDATE-CARD term (`card_pass`, driven by
/// `eval_domain`) and a per-SCANNED-ROW term (`scan_units`, below). The old lumped
/// `STREAM_MATCH_PHASE_PER_CARD_NS = 3.0` was fit on CARD mode, where the loop
/// early-stops at the first matching printing (`scan_units ≈ eval_domain`) so the
/// two terms are indistinguishable; the sum stays 3.0 there. Printing/artwork scan
/// EVERY printing of each candidate (`scan_units ≈ eval_domain · n_printings/n_cards`),
/// which the lumped constant under-priced ~2× (fidelity 0.5, the eval_domain-counts-
/// cards bug). Split fit: card sum pins `CARD_PASS + SCAN = 3.0`; printing's ~2×
/// under-prediction at ratio ~3.09 pins the split (`CARD_PASS + 3.09·SCAN ≈ 6.0`).
/// Refit 2026-07-30 by `scripts/fit_cost_model.py` — non-negative Gauss-Newton on the LOG ratio
/// (symmetric in over/under, unlike a relative-error fit, which shrinks every rate toward zero),
/// ridge-anchored to the previous values because several columns barely vary on this corpus and
/// are collinear with the intercept. Fitted on ~10k distinct feature vectors, stable to <3% across
/// independent seeds. Median measured/predicted moved 1.78 -> 1.00 (P4) and 1.69 -> 1.06 (P3).
const STREAM_CARD_PASS_NS: f64 = 6.46;

/// P3's per-scanned-row cost, charged ONLY when a residual is present.
///
/// Unlike P4, P3 merely COUNTS matches, and `card_match_count` is O(1) offset arithmetic whenever
/// `all_match` holds (the artwork-group count is a build-time constant). So with no residual it does
/// no per-printing work at all — but with one it must walk the printings testing each. Regressing
/// per-card match-loop time on printings-scanned-per-card, split by tier, shows exactly that split:
///
/// | residual | slope (ns per printing/card) |
/// |----------|-----------------------------:|
/// | none (all_match) | **0.02** |
/// | MASK_COMPARE | 2.83 |
/// | SET_LOOKUP | 2.19 |
/// | TEXT_SCAN | 1.90 |
///
/// An earlier revision removed this term outright, on the O(1) argument above. That argument is
/// right for the `all_match` half and wrong for the other, and an ungated fit drove the rate to 0
/// because the tier-0 rows (the majority) dominated it. Gating on the residual separates them.
///
/// This one is NOT common-mode with P4 — P4 pays `GATHER_SCAN_PER_ROW_NS` unconditionally — so
/// unlike the verify tier it can move the argmin between the two.
const STREAM_SCAN_PER_ROW_NS: f64 = 5.53;


/// Per-card cost of RUNNING `card_pass` at all, on top of whatever the residual's own nodes cost.
/// The tri walk has to set up, populate the reused `residual` vec, branch on the `Tri`, and drive
/// the per-printing loop; none of that is a filter node, so `verify_cost_tier` does not describe it
/// and should not be asked to.
///
/// This replaces a multiplicative `*_VERIFY_TIER_SCALE` of 2.87/2.65. The multiplier was wrong in
/// form, not just in value. `bench_verify_cost` (cargo test --release bench_verify_cost -- --ignored)
/// times the real `FilterExpr::matches()` path per node and VALIDATES the tier constants:
/// MASK_COMPARE claims 4.0 ns against 2.08-3.60 measured, SET_LOOKUP 9.0 against 2.12-8.69,
/// TEXT_SCAN 23.0 against 21.5, REGEX_MACHINERY 50 against 45.8-47.5. They are right, slightly
/// conservative. So there was no per-node calibration error for a multiplier to correct — there was
/// an unmodelled fixed per-card overhead, and scaling by 2.7x happened to approximate it for the
/// common cheap tiers while badly over-costing text (2.7 x 23 = 62 ns against a real 23 + 17).
///
/// It is a FLOOR, not an offset: `max(tier_ns, this)`. Cheap residuals are dominated by the walk,
/// expensive ones dominate it, and the two do not add. Measured by regressing per-card match-loop
/// time on printings-scanned-per-card, separately per tier class, over the real query population
/// (the earlier additive fit used single-predicate queries, whose tiny sample disagreed):
///
/// | tier | claims | P3 excess over no-residual | P4 excess |
/// |------|-------:|---------------------------:|----------:|
/// | MASK_COMPARE | 4 ns | 8.9 | 15.4 |
/// | SET_LOOKUP | 9 ns | 10.4 | 11.2 |
/// | TEXT_SCAN | 23 ns | 20.7 | 19.9 |
///
/// The excess is roughly INDEPENDENT of the tier for the cheap classes and non-monotonic across them
/// (P4's MASK excess exceeds its SET_LOOKUP excess), which additive cannot produce. `max` fits all
/// three within ~2 ns for P3 and ~4 for P4, where `tier + 11` over-costs TEXT_SCAN by 14 ns — the
/// text-heavy over-costing that showed up as GatheredScan/candidates at 0.67.
///
/// That same regression also shows the residual's cost is per CARD, not per printing scanned: the
/// SLOPE against printings-per-card is ~3.5 for every tier including none (P4) and ~2 for P3, i.e.
/// independent of what the residual is. So the tier belongs on `eval_domain`, as it now sits.
const STREAM_RESIDUAL_FLOOR_NS: f64 = 8.18;
/// ns per match, for the permutation-walk emit. Small — P3 measured nearly flat
/// in match count once eval_domain is fixed (see STREAM_MATCH_PHASE_PER_CARD_NS),
/// so this is a minor term.
const STREAM_EMIT_PER_MATCH_NS: f64 = 0.13;
/// ns per candidate card that ARTWORK mode pays and the other two do not.
///
/// `card_match_count`'s artwork arm keeps a per-card `seen_words` bitmask to dedupe artwork groups,
/// and that costs a fixed amount per card regardless of how many printings the card has: a
/// `seen_words.fill(0)` before the loop and a `seen_words.iter().map(count_ones).sum()` after it,
/// over `ARTWORK_GROUP_WORDS = 8` u64s. Card mode returns on the first matching printing and printing
/// mode just counts, so neither pays it. (The `all_match` + `have_group_counts` shortcut in
/// `run_query_streamed` skips the helper entirely, which is why this lands on residual-bearing
/// candidates in particular.)
///
/// Fitting the arm separately per distinct-on is what surfaced it, over three seeds:
///
///     StreamedSelect    artwork / printing        seed21      seed22      seed23
///     CARD_PASS                              5.92/3.36   5.76/3.20   5.89/3.17
///     RESIDUAL_FLOOR                        11.75/9.21  11.81/9.39  11.86/9.81
///
/// Two independently fitted per-card terms, both elevated in artwork by ~2.4 ns and never flipping
/// sign. One mechanism showing up twice, so it belongs in its own term rather than as a mode-specific
/// copy of each rate. ~16 word-ops per card is the right order for 2.4 ns.
const STREAM_ARTWORK_SEEN_PER_CARD_NS: f64 = 1.36;
/// ns per card scanned in the small-total gather (`for cid in 0..n_cards`,
/// counts[cid]==0 check). Cheaper than a match-phase visit (no filter work). Fit
/// from the narrow-query floor: cmc>=15 / o:annihilator / cmc==7 card SHALLOW all
/// ~52µs = 31508 × 1.65. Only added when `matches <= STREAM_MIN_MATCHES`, the
/// exact condition that routes P3 into that gather branch. The 1.65 above was fit on three hand
/// picked narrow queries; across the sampled space the floor measures ~31µs, not 52µs.
const STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS: f64 = 1.64;
/// Per-card cost P3 pays over the WHOLE corpus regardless of how narrow the query is, charged on
/// `n_cards` rather than `eval_domain`. The thread-local counts buffer is resized and cleared to
/// `cards.len()` every query — a 126 kB memset on this corpus — and the emission walk is over the
/// corpus-sized sort permutation. Fit lands at ~9 µs total, more than a memset alone accounts for,
/// so this lumps the two; a single corpus cannot separate them (both are exactly `n_cards`). Kept
/// as a per-card RATE rather than the previous flat constant so it tracks corpus size at all.
const STREAM_CORPUS_PASS_PER_CARD_NS: f64 = 0.02;
/// Fixed P3 setup, net of the O(n_cards) work above.
const STREAM_FIXED_COST_NS: f64 = 217.5;

// ─── P4: GatheredScan ───────────────────────────────────────────────────────
// The universal fallback: per-card loop pushes every match's sort key into a
// Vec (O(matches)), then select_page quickselects the page. Visits eval_domain
// cards, each paying the residual verify tier.

/// P4 gathered loop, split per-CANDIDATE-CARD (`card_pass`, `eval_domain`) and
/// per-SCANNED-ROW (`scan_units`), same rationale as STREAM_CARD_PASS_NS. The old
/// lumped `GATHER_VISIT_PER_CARD_NS = 5.5` was fit on card mode (all-match broad,
/// eval_domain==matches, tier=0, sum ≈ 6.3-6.9 with GATHER_PUSH); card keeps
/// `CARD_PASS + SCAN = 5.5`. Printing's ~2× under-prediction at ratio ~3.09 splits
/// it (`CARD_PASS + 3.09·SCAN ≈ 11`).
/// Refit 2026-07-30 by `scripts/fit_cost_model.py` — non-negative Gauss-Newton on the LOG ratio
/// (symmetric in over/under, unlike a relative-error fit, which shrinks every rate toward zero),
/// ridge-anchored to the previous values because several columns barely vary on this corpus and
/// are collinear with the intercept. Fitted on ~10k distinct feature vectors, stable to <3% across
/// independent seeds. Median measured/predicted moved 1.78 -> 1.00 (P4) and 1.69 -> 1.06 (P3).
const GATHER_CARD_PASS_NS: f64 = 6.88;
/// ns per printing scanned in the gathered loop (residual test per row). The verify `tier`
/// does NOT ride this term; see GATHER_VERIFY_TIER_SCALE and STREAM_SCAN_PER_ROW_NS.
const GATHER_SCAN_PER_ROW_NS: f64 = 2.06;

/// P4's counterpart to STREAM_RESIDUAL_FLOOR_NS — see there for the form and its derivation.
const GATHER_RESIDUAL_FLOOR_NS: f64 = 18.89;
/// ns per match pushed into the sort-key Vec + quickselected.
const GATHER_PUSH_PER_MATCH_NS: f64 = 2.24;
/// ns per page slot materialized. Fit from the deep-vs-shallow gap on broad
/// queries (cmc>=0 card: 225708−216667 ≈ 9041ns over 10000 extra offset ≈ 0.9),
/// bounded by matches: narrow deep pages (offset > matches) measured ≈ shallow
/// (select_page returns early), so the term uses min(offset+limit, matches).
const GATHER_SELECT_PER_PAGE_SLOT_NS: f64 = 3.51;
/// Fixed P4 setup. Fit from the narrowest query (cmc>=15 card shallow 208ns at
/// eval_domain=5: 208 − 5×(GATHER_VISIT_PER_CARD_NS+GATHER_PUSH_PER_MATCH_NS) −
/// 5×GATHER_SELECT_PER_PAGE_SLOT_NS ≈ 170).
const GATHER_FIXED_COST_NS: f64 = 169.6;

// --- PrintingCompose's own rates -------------------------------------------------------------
//
// This arm borrowed every constant it used from plans fitted against DIFFERENT physical operations,
// because until now nothing fitted it: `design_row` returned None for PrintingCompose, so the one arm
// carrying ~75% of measured routing regret was the one arm no tool calibrated. Fitting it (11,332
// rows, 5,996 distinct shapes) moved within-25% agreement from 39% to 55% and p10 from 0.30 to 0.52,
// the largest single gain of the exercise -- and showed the borrowed values are genuinely wrong here:
//
//     term                    borrowed from            was    fitted
//     BROADCAST / PROJECT     LINEAR_PASS             1.50      1.93
//     SCATTER                 RANGE_SCATTER           0.36      0.48
//     WALK_STEP               RANGE_WALK_STEP          4.5      0.58
//     GATHER_CARD_PASS        GATHER_CARD_PASS        6.80      9.81
//     GATHER_PUSH_PER_MATCH   GATHER_PUSH_PER_MATCH   2.81      3.39
//     FIXED                   RANGE_FIXED_COST       150.0    163.56
//
// The two gather rates are the informative ones: fitted on the SAME sample, GatheredScan wants 6.58
// and 2.54 for what the comments called "the same operation". They are not the same operation --
// compose walks a bitmap it just built, GatheredScan walks the printing array -- so the sharing was an
// assumption, not a measurement. WALK_STEP at 7.7x is the largest error; RANGE_WALK_STEP stays at 4.5
// for PrintingRangeScan, which has too few rows here to refit and should not inherit this.

/// Legality broadcast-down and the printing→card/artwork projection pass, both linear over the set.
pub(crate) const COMPOSE_LINEAR_PASS_PER_PRINTING_NS: f64 = 1.93;
/// Range-slice scatter into the printing bitmap during build.
pub(crate) const COMPOSE_SCATTER_PER_PRINTING_NS: f64 = 0.48;
/// Result-space bitmap words popcounted for the total.
const COMPOSE_POPCOUNT_PER_WORD_NS: f64 = 1.07;
/// Per printing stepped over by the Perm / OrderbyWalk page fill.
const COMPOSE_WALK_STEP_NS: f64 = 0.58;
/// Per row emitted by the Perm / OrderbyWalk page fill.
const COMPOSE_WALK_EMIT_PER_ROW_NS: f64 = 2.19;
/// Per candidate card visited by `gather_composed_page`.
const COMPOSE_GATHER_CARD_PASS_NS: f64 = 9.81;
/// Per printing bit-tested against `pbits` inside the gather.
const COMPOSE_GATHER_BITTEST_PER_PRINTING_NS: f64 = 0.38;
/// Per match pushed into the bounded GatherSelect accumulator.
const COMPOSE_GATHER_PUSH_PER_MATCH_NS: f64 = 3.39;
/// Per-query setup for the compose fastpath.
const COMPOSE_FIXED_COST_NS: f64 = 163.56;

/// Printings a forward-permutation / orderby walk steps over to fill one page: `page_span` result
/// rows at density `match_rate`. Derived rather than stored, and exposed so a harness can check it
/// against the `printings_scanned` counter -- the Perm and OrderbyWalk paging branches are priced
/// entirely on this quantity and nothing else validates them.
pub(crate) fn printings_walked(f: &PlanFeatures) -> f64 {
    let page_span = f64::from((f.offset.saturating_add(f.limit)).min(f.matches));
    let match_rate = (f64::from(f.matches) / f64::from(f.n_printings.max(1))).max(MATCH_RATE_FLOOR);
    page_span / match_rate
}

pub(crate) fn plan_cost(plan: PhysicalPlan, f: &PlanFeatures) -> f64 {
    let n_cards = f64::from(f.n_cards);
    let n_printings = f64::from(f.n_printings);
    let matches = f64::from(f.matches);
    let eval_domain = f64::from(f.eval_domain);
    let scan_units = f64::from(f.scan_units);
    let tier_ns = f64::from(f.residual_tier_ns100) / 100.0;
    let limit = f64::from(f.limit);
    let page_span = f64::from((f.offset.saturating_add(f.limit)).min(f.matches));

    // Printings walked to fill the page in a forward-permutation walk (both printing-space plans):
    // roughly `page_span` rows at density `match_rate`.
    let match_rate = (matches / n_printings).max(MATCH_RATE_FLOOR);
    let printings_walked = page_span / match_rate;
    match plan {
        // #695 bare range, unique=printing: total is the range index's `k` (no synth, no popcount pass),
        // page is a forward permutation walk. So just the walk + fixed setup.
        PhysicalPlan::PrintingRangeScan => {
            printings_walked * RANGE_WALK_STEP_NS  // forward-perm walk to fill the page
                + RANGE_FIXED_COST_NS              // per-query setup
        }
        // #724 unified compose, any distinct-on. One term per build operation, plus a paging term
        // that depends on which strategy `printing_compose_fastpath` will actually use (see
        // `compose_has_perm`'s doc) — the permutation walk and the permutation-free gather fallback
        // have different cost shapes, so this must not just assume the walk.
        PhysicalPlan::PrintingCompose => {
            let build = f64::from(f.broadcast_printings) * COMPOSE_LINEAR_PASS_PER_PRINTING_NS  // legality broadcast-down into the printing bitmap (border/rarity read a plane → 0)
                + f64::from(f.scatter_printings) * COMPOSE_SCATTER_PER_PRINTING_NS  // range-slice scatter into the printing bitmap (cheap: no card cursor)
                + f64::from(f.project_printings) * COMPOSE_LINEAR_PASS_PER_PRINTING_NS  // second pass: project printing→card/artwork (0 for printing mode) — the pass CardRangePopcount fuses away
                + f64::from(f.popcount_words) * COMPOSE_POPCOUNT_PER_WORD_NS; // popcount the result-space bitmap for the total (printing/card/artwork words)
            let page = match f.compose_paging {
                // Perm (forward grouped walk) and OrderbyWalk (#744 value-index/plane walk) share the
                // offset-dependent walk shape: fill the page in ~page_span/selectivity steps, then emit
                // one page. OrderbyWalk terminates at page_offset+limit just like the permutation walk,
                // which is exactly why the COMPOSE_GATHER breadth gate is bypassed for it — broad is its
                // best case, not its worst.
                super::ComposePaging::Perm | super::ComposePaging::OrderbyWalk => {
                    printings_walked * COMPOSE_WALK_STEP_NS  // walk to fill the page
                        + limit * COMPOSE_WALK_EMIT_PER_ROW_NS  // emit one page of rows
                }
                // gather_composed_page: visits every candidate (eval_domain, same rate GatheredScan's
                // own permutation-free walk pays per card), tests `pbits` membership per printing
                // (scan_units — a cheap bit test, not a real residual scan, so the cheap
                // RANGE_SCATTER_PER_PRINTING_NS rate applies, not GATHER_SCAN_PER_ROW_NS + tier_ns),
                // and pushes each surviving match into the bounded GatherSelect accumulator (matches,
                // same per-match rate GatheredScan pays for the same operation). Offset-independent —
                // unlike the walk above, it costs the same regardless of how deep the page is.
                super::ComposePaging::Gather => {
                    eval_domain * COMPOSE_GATHER_CARD_PASS_NS
                        + f64::from(f.compose_scan_printings) * COMPOSE_GATHER_BITTEST_PER_PRINTING_NS
                        + matches * COMPOSE_GATHER_PUSH_PER_MATCH_NS
                }
                // The fastpath will refuse this query, so there is no page term to charge. Infinity
                // keeps the plan out of the argmin entirely — routing to a plan that returns `None`
                // pays the detour and then runs something else anyway.
                super::ComposePaging::Decline => return f64::INFINITY,
            };
            build + page + COMPOSE_FIXED_COST_NS // per-query setup
        }
        // #634 plane popcount-skip order walk (precomputed bitmap ⇒ no synth):
        PhysicalPlan::PlanePopcountOrder => {
            matches * PLANE_POPCOUNT_SCATTER_PER_MATCH_NS  // scatter matches through the inverse permutation
                + (n_cards / 64.0) * PLANE_POPCOUNT_PER_WORD_NS  // popcount the card bitmap + skip-scan to the offset
                + limit * PLANE_POPCOUNT_EMIT_PER_CARD_NS  // emit one page of cards
                + PLANE_POPCOUNT_FIXED_COST_NS  // per-query setup
        }
        // #725 bare range, unique=card: PlanePopcountOrder's popcount-skip walk over a card bitmap
        // *built at query time* from the range slice — same walk terms, plus the build synth.
        PhysicalPlan::CardRangePopcount => {
            f64::from(f.scatter_printings) * CARD_RANGE_BUILD_PER_PRINTING_NS  // fused one-pass build: scatter+project the range slice straight into card bits
                + matches * PLANE_POPCOUNT_SCATTER_PER_MATCH_NS  // scatter matches through the inverse permutation
                + (n_cards / 64.0) * PLANE_POPCOUNT_PER_WORD_NS  // popcount the card bitmap + skip-scan to the offset
                + limit * PLANE_POPCOUNT_EMIT_PER_CARD_NS  // emit one page of cards
                + PLANE_POPCOUNT_FIXED_COST_NS  // per-query setup
        }
        PhysicalPlan::StreamedSelect => {
            // The small-total gather branch (run_query_streamed) scans all
            // n_cards when total <= STREAM_MIN_MATCHES — the O(N) floor that
            // sinks P3 on narrow queries.
            // Mirrors `run_query_streamed`'s own guard: it returns at `total == 0 || page_offset >=
            // total` BEFORE reaching the small-total gather, so those queries never scan n_cards and
            // must not be charged for it. Charging them anyway over-costs by the whole floor --
            // measured est/real of 55.7 at p50 on zero-match queries, which really take 0.62 us
            // against a ~35 us estimate, and 1,265 of 33k StreamedSelect rows land there.
            let runs_small_gather = u64::from(f.matches) <= *super::STREAM_MIN_MATCHES as u64
                && f.matches > 0
                && u64::from(f.offset) < u64::from(f.matches);
            let floor = if runs_small_gather { n_cards * STREAM_SMALL_TOTAL_FLOOR_PER_CARD_NS } else { 0.0 };
            // card_pass — and the verify tier that prices it — is per candidate CARD: the loop
            // calls `filter.card_pass` once per `cid`, and only the cheaper printing-dependent
            // residual is re-checked per row inside `push_card_matches`. Charging `tier_ns` per
            // scanned ROW instead is invisible in card mode (scan_units ≈ eval_domain) and
            // overcharges printing/artwork by the whole printings-per-card ratio.
            eval_domain * (STREAM_CARD_PASS_NS + if tier_ns > 0.0 { tier_ns.max(STREAM_RESIDUAL_FLOOR_NS) } else { 0.0 })
                // Only with a residual does P3 walk printings; see STREAM_SCAN_PER_ROW_NS.
                + if tier_ns > 0.0 { scan_units * STREAM_SCAN_PER_ROW_NS } else { 0.0 }
                + matches * STREAM_EMIT_PER_MATCH_NS
                + f64::from(f.artwork_seen_cards) * STREAM_ARTWORK_SEEN_PER_CARD_NS
                + floor
                + n_cards * STREAM_CORPUS_PASS_PER_CARD_NS
                + STREAM_FIXED_COST_NS
        }
        PhysicalPlan::GatheredScan => {
            // Per-CARD verify tier, for the reason spelled out in the StreamedSelect arm above.
            eval_domain * (GATHER_CARD_PASS_NS + if tier_ns > 0.0 { tier_ns.max(GATHER_RESIDUAL_FLOOR_NS) } else { 0.0 })
                + scan_units * GATHER_SCAN_PER_ROW_NS
                + matches * GATHER_PUSH_PER_MATCH_NS
                + page_span * GATHER_SELECT_PER_PAGE_SLOT_NS
                + GATHER_FIXED_COST_NS
        }
    }
}
