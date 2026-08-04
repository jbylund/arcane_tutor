//! Micro-benchmark that decomposes `GatheredScan`'s match loop into its three rates.
//!
//! **Cache state fixed, and the rates are corpus-size dependent (2026-08-03).** Chunk ROTATION (each
//! iteration walks a different slice) plus per-cell STAGGER (cells sharing a group walk different slices
//! in the same iteration) means no walk inherits another's cache lines, and the reported rate is the
//! MEDIAN over rotated chunks rather than the luckiest minimum. Both were needed: rotation alone still
//! had cell A at 10.50 ns/card against cell B's 6.11 on identical cards, because every cell on a group
//! walked the same chunk and the first paid all the misses. `scripts/upscale_corpus.py` supplies stores
//! big enough to rotate (the real corpus gives the wide group ONE chunk at 4,500 cards), selected with
//! `BENCH_LOOP_STORE`.
//!
//! Swept over 31,508 / 126,032 / 409,604 oracle cards:
//!
//!     P4  LOOP  ns/card         6.27   11.37   15.04     2.4x
//!     P4  SCAN  ns/printing     2.27    3.03    2.24     flat
//!     P4  PUSH  ns/match        1.51    4.94    6.98     4.6x
//!     P3  all_match ns/card     2.58    2.54    2.55     FLAT
//!     P3  residual  ns/card     5.08   11.33   18.15     3.6x
//!     P3  SCAN  ns/printing     3.30    5.57   10.85     3.3x
//!
//! P3's all_match arm being flat across a 13x corpus is the check that the method works: that path reads
//! only the card record and does offset arithmetic, so it has no misses to gain, while everything that
//! walks printings grows 3x+. A rate is therefore not a property of the code alone -- it is a property of
//! the code AND how much of the archive fits in cache, and `cost.rs` has no term for the second.
//!
//! At the production corpus size the shipped P4 constants are confirmed: LOOP 6.27 against 6.88 (9%
//! low), SCAN 2.27 against 2.06 (10% high), PUSH 1.51 against 2.24. That is the retraction closed from
//! the other side -- warm measurement said 2.98 and was wrong; cold measurement says 6.27 and agrees
//! with what ships.
//!
//! P3 does NOT agree: 2.58 against a shipped 5.05 per card, 5.08 against 11.63 with a residual, 3.30
//! against 5.97 per printing -- about 2x over-costed. Both plans were measured identically, so this is
//! not a cache artifact, but it is measured on `ns_loop` ONLY, and P3's arm may be absorbing setup or
//! finish cost that its loop never pays. That has to be ruled out before the gap is called an error.
//!
//! The routing consequence is the durable one: the plans' rates scale DIFFERENTLY with corpus size, so
//! the P3/P4 balance drifts as the corpus grows even with every constant left alone. Any refit is
//! calibrated to the corpus it was measured on.
//!
//! **RETRACTION (2026-08-03): every rate this harness reports is a WARM-CACHE rate, and the shipped
//! constants are not too high.** `ITERS` walks one card list repeatedly and keeps the minimum, so by the
//! second pass every card, printing and string it touches is resident. Production walks a candidate set
//! once. Measuring the first pass against the warm minimum on cells that run after the mmap has faulted
//! in gives 1.6-2.2x (A' 1.91x/1.74x, I 1.83x/1.59x, H 2.17x/2.14x; the 100x+ figures on the first cells
//! are first-touch page faults on the 68 MB store, not cache effects).
//!
//! That 1.6-2x is the whole discrepancy. Warm 2.98 ns/card against 6.34 fitted on traffic is 2.1x; P3's
//! warm 2.41 against a shipped 5.05 is 2.1x; the warm push 1.06 against 2.00 fitted is 1.9x. The
//! counter/feature ratios all read 1.00, so the features were never the gap -- the TIME was, and the
//! shipped constants include the miss cost this harness removes.
//!
//! So the five refits below did not fail because routing is a delicate joint surface. They failed
//! because they lowered rates toward a cache state production never reaches. Read every ns figure in
//! this file as "warm", useful for the SHAPE of the loop -- which terms exist, which are degenerate, how
//! artwork differs -- and not as a candidate constant. Those shape findings stand; the levels do not.
//!
//! `cost.rs` charges that loop three ways — `GATHER_CARD_PASS_NS` per card visited,
//! `GATHER_SCAN_PER_ROW_NS` per printing examined, `GATHER_PUSH_PER_MATCH_NS` per match
//! pushed — and notes the three "could NOT be fit ... a STRUCTURAL collinearity no corpus
//! size fixes", so the values came from a physical prior instead. The loop is ~90% of the
//! plan's dispatch, and P3-vs-P4 routing turns on the split rather than the total, so the
//! three rates are worth pinning down.
//!
//! Fitting them on sampled traffic was re-tried and fails for a sharper reason than corpus
//! size: measured on 5,986 all_match rows, the three counters correlate 0.94–1.00 **inside
//! every `unique` × `prefer` cell**, not merely pooled. `unique` and `prefer` do change the
//! ratio between the columns, but within any one cell all three still scale with query size,
//! and query size dominates the variance. `matches_pushed` fitted to exactly 0.00 on two
//! independent seeds — pinned to the non-negativity boundary, i.e. unidentified rather than
//! noisy. Balancing the cells made coefficient stability worse (1.06× → 1.46× across seeds),
//! because all it can do is discard rows. No arrangement of sampled traffic fixes this: traffic
//! never varies the ratio at FIXED SCALE, which is the only thing that separates the columns.
//!
//! So the design is built rather than sampled. Cards are grouped by printing count, which lets
//! printings-per-card be set independently of card count, and `unique`/`prefer` then set
//! matches independently of printings:
//!
//!     cell  printings/card  unique     prefer   cards  printings  matches
//!     A     1               card       default    N        N         N
//!     B     1               printing   default    N        N         N     (consistency check vs A)
//!     C     ~W              card       oldest     N       ~WN        N
//!     D     ~W              printing   default    N       ~WN       ~WN
//!
//! A, C and D give `[1 1 1; 1 W 1; 1 W W]`, whose determinant is nonzero — all three rates are
//! pinned. Cell C is why `prefer` is in the design: under the default prefer the card-mode
//! kernel breaks at the first match, so printings collapses back onto cards and the row stops
//! being independent (see `push_card_matches`). Each cell is also run at two card counts, so
//! linearity in the card term is checked rather than assumed.
//!
//! This calls the real `exec_gathered_scan` and reads `ns_loop` and the counters back off
//! `PhaseStats` — the same fenceposts and the same counters production publishes. Nothing is
//! reimplemented, so there is no second copy of the loop to keep in sync, and the fit is
//! against the instrumentation the cost model is actually checked with.
//!
//! What it measured (2026-08-03), across three runs, where the traffic fit could not pin the push
//! rate off the boundary at all:
//!
//!     GATHER_CARD_PASS_NS        shipped 6.88   fitted 3.15-3.33   (1.06x spread)
//!     GATHER_SCAN_PER_ROW_NS     shipped 2.06   fitted 2.25-2.26   (1.004x)
//!     GATHER_PUSH_PER_MATCH_NS   shipped 2.24   fitted 0.90        (1.000x)
//!     the card_pass call itself                 2.94-3.00 ns/card on singletons, 1.6-1.8 on wide
//!
//! Those last two lines say the shipped constant is not simply wrong: 3.24 of loop overhead plus
//! ~3.0 for the `card_pass` call is ~6.2 against a shipped 6.88, so `GATHER_CARD_PASS_NS` BUNDLES
//! the predicate call. It is therefore about right for queries that make that call, and about 2x too
//! high for the #634 `all_match_known` path, which skips `card_pass` entirely and is charged for it
//! anyway — a model-shape error rather than a mis-fitted constant, and it over-costs `GatheredScan`
//! precisely on the queries that should be cheapest for it.
//!
//! **Shipping those constants FAILED the acceptance test, and that is the more important result.**
//! Splitting the card term as measured and applying all three rates moved total routing regret
//! 36.2 → 51.7 ms, with `candidates / artwork` going 19% → 35% of all lost time. The design had no
//! artwork cell in it, and `cost.rs` applies one set of `GATHER_*` rates to all three modes.
//!
//! Adding artwork cells (E, F) shows why no single rate triple can work. Against the card/printing
//! fit, artwork measures +19% and +36% on single-printing cards but −29% and −19% on wide ones: the
//! surcharge FLIPS SIGN with printings-per-card. Artwork pushes ~2.4 matches/card where printing mode
//! pushes ~6.9, and its per-printing group bookkeeping is cheaper than the push path it replaces, so
//! it is dearer where cards are narrow and cheaper where they are wide. `ns_setup` is not the
//! explanation either — the `group_best` allocation measures 83-125 ns, negligible.
//!
//! Three shapes were then fitted and each taken through the regret gate. The final one, which is what
//! this harness now reports, fits each mode in the two-parameter space it can support and recovers the
//! three rates from the pair of pairs -- no pooling:
//!
//!     card mode      A_card  = LOOP + PUSH = 4.22    B_card  = SCAN        = 2.36
//!     printing mode  A_print = LOOP        = 2.98    B_print = SCAN + PUSH = 3.24
//!     artwork arm                            6.57                           1.09   + 1.06 ns/group
//!
//! so LOOP = A_print, SCAN = B_card, and PUSH twice over: `A_card - A_print` = 1.24 and
//! `B_print - B_card` = 0.88, two independent estimates from different columns of different modes. They
//! agree to 1.41x -- a real if mild failure of linearity in these three counters, and the honest error
//! bar on the push. PUSH comes out SHARED across modes (it is the same `GatherSelect` push), leaving
//! only LOOP and SCAN mode-dependent. Worst-cell agreement 25% off, against 46% for the mode-blind
//! triple.
//!
//! **Every one of the three regressed at the gate, and the pattern is the result:**
//!
//!     attempt                      LOOP ns/card   artwork arm   total regret
//!     mode-blind triple                    3.25   no                   +43%
//!     pooled + artwork arm                 3.67   yes                 +8.8%
//!     reparameterised (this one)           2.98   yes                  +40%
//!
//! With the artwork arm held fixed, regret tracks HOW FAR P4's per-card cost was lowered rather than how
//! accurate it is -- the most accurate description of the loop produced the second-worst routing. That
//! is not a parameterisation problem. `plan_cost` is only ever used comparatively, and P4's inflated arm
//! is absorbing an over-estimate on P3's side, so any accurate isolated fix to P4 must regress until P3
//! is measured the same way. Three successively better descriptions of P4's loop giving three
//! regressions is the evidence for that.
//!
//! So the constants are deliberately unchanged, and the next step is NOT another P4 fit: it is the same
//! built-design treatment for `run_query_streamed`'s loop, then applying both arms together. Independent
//! support for that ordering -- P3's dispatch median is 32 us against P4's 4 us, and its finish phase is
//! 12% of all measured ns at mean |log| 2.06.
//!
//! One further limit on all of these: `LIMIT` is fixed, so nothing here constrains how the rates
//! behave as the page grows.
//!
//!     cargo test --release bench_gather_loop -- --ignored --nocapture
//!
//! Needs benchmarks/verify-order/real.store, shared with `bench_verify_cost` — rebuild it the
//! same way (see that module's docs) after any AOracleCard/APrinting layout change.

use super::bench_loop_design::{store_path, CARD_COUNTS, ITERS, LIMIT, WIDE_MIN_PRINTINGS};
use std::hint::black_box;

use rkyv::Archived;

use super::{
    archive_header, archive_payload, exec_gathered_scan, take_phase_stats, CardData, CmpOp, FilterExpr, Mmap, NarrowedRepr,
    NumExpr, NumField, PreparedCandidates, QueryCtx, QueryParams, ARCHIVE_HEADER_LEN,
};

/// (offset, limit) pairs for the finish-phase sweep. `GATHER_SELECT_PER_PAGE_SLOT_NS` is charged against
/// `page_span = min(offset + limit, matches)`, and every cell above holds the page FIXED, so that term
/// has never been varied here -- the loop rates were measured while the one term keyed on the page was
/// held constant. These four move `page_span` ~40x at constant counters, which is what separates a
/// per-slot rate from a fixed cost the phase pays regardless.
const PAGE_SPECS: [(usize, usize); 4] = [(0, 15), (0, 60), (0, 600), (900, 60)];



/// One measured design point: the realized counters, and the loop time they were produced by.
struct Cell {
    label: &'static str,
    n_cards_req: usize,
    cards: f64,
    printings: f64,
    matches: f64,
    ns_loop: f64,
    /// False when `all_match_known` short-circuited `card_pass`. Only these rows feed the three-rate
    /// fit; the rows that DO call `card_pass` are used to price that call instead, and mixing them
    /// would fold a predicate evaluation into the card term.
    ran_card_pass: bool,
    /// The `unique` mode this cell ran in. All three loops differ, so rates are fitted per mode:
    /// pooling them smears mode-specific cost across rates that then fit none of the modes.
    mode: &'static str,
}

/// Solve a 3×3 system by Gaussian elimination with partial pivoting.
fn solve3(mut a: [[f64; 4]; 3]) -> Option<[f64; 3]> {
    for col in 0..3 {
        let piv = (col..3).max_by(|&i, &j| a[i][col].abs().total_cmp(&a[j][col].abs()))?;
        a.swap(col, piv);
        if a[col][col].abs() < 1e-12 {
            return None;
        }
        for row in 0..3 {
            if row == col {
                continue;
            }
            let f = a[row][col] / a[col][col];
            let pivot = a[col];
            for (k, cell) in a[row].iter_mut().enumerate().skip(col) {
                *cell -= f * pivot[k];
            }
        }
    }
    Some([a[0][3] / a[0][0], a[1][3] / a[1][1], a[2][3] / a[2][2]])
}

/// Relative least squares over every cell: each equation is divided by its own measured time, so a
/// cell running 50 µs does not outweigh one running 5 µs. Absolute OLS here would fit the largest
/// cell and ignore the rest, which is the same mistake that inflated the finish-phase rate.
/// Fitted over the modes named, and it has to be more than one of them.
///
/// No single mode can identify all three rates, because in each one a column is a DUPLICATE: card mode
/// pushes once per card, so `matches == cards`; printing mode pushes every printing it examines, so
/// `matches == printings`. Pooling card and printing is what makes the three separable, which sits in
/// direct tension with the rates differing by mode -- the tension is resolved by reparameterising
/// (see the module docs), not by fitting each mode alone.
fn fit(cells: &[Cell], modes: &[&str]) -> Option<[f64; 3]> {
    let mut normal = [[0.0f64; 4]; 3];
    for c in cells.iter().filter(|c| !c.ran_card_pass && modes.contains(&c.mode)) {
        let w = 1.0 / c.ns_loop;
        let x = [c.cards * w, c.printings * w, c.matches * w];
        for i in 0..3 {
            for j in 0..3 {
                normal[i][j] += x[i] * x[j];
            }
            normal[i][3] += x[i] * (c.ns_loop * w);
        }
    }
    solve3(normal)
}

/// Per-card and per-printing rates for ONE mode, with any per-match charge subtracted off the target
/// first. Two unknowns, which is what a single mode can support.
///
/// Fitting a mode alone in the three-rate space is impossible, not merely noisy: card mode pushes once
/// per card so `matches == cards`, and printing mode pushes every printing it examines so
/// `matches == printings`. In each mode one column duplicates another. Collapsing to two parameters is
/// what makes each mode independently fittable, and the collapsed pair means something different in
/// each mode -- see `recover_rates`.
fn fit_mode(cells: &[Cell], mode: &str, push_ns: f64) -> Option<[f64; 2]> {
    let mut normal = [[0.0f64; 3]; 2];
    for c in cells.iter().filter(|c| !c.ran_card_pass && c.mode == mode) {
        let w = 1.0 / c.ns_loop;
        let x = [c.cards * w, c.printings * w];
        // Any shared per-match charge comes off the target, the way `design_row` handles cost.rs's
        // non-linear offsets rather than trying to make them columns.
        let y = (c.ns_loop - c.matches * push_ns) * w;
        for i in 0..2 {
            for j in 0..2 {
                normal[i][j] += x[i] * x[j];
            }
            normal[i][2] += x[i] * y;
        }
    }
    let det = normal[0][0] * normal[1][1] - normal[0][1] * normal[1][0];
    if det.abs() < 1e-12 {
        return None;
    }
    Some([
        (normal[0][2] * normal[1][1] - normal[0][1] * normal[1][2]) / det,
        (normal[0][0] * normal[1][2] - normal[1][0] * normal[0][2]) / det,
    ])
}

/// The three original rates, recovered from the two collapsed pairs without ever pooling the modes.
///
///     card mode      A_card = LOOP + PUSH      B_card  = SCAN
///     printing mode  A_print = LOOP            B_print = SCAN + PUSH
///
/// so `LOOP = A_print`, `SCAN = B_card`, and PUSH drops out TWICE -- `A_card - A_print` and
/// `B_print - B_card`. Those two are independent estimates of the same quantity, computed from
/// different columns of different modes, so their agreement is a test of whether the loop is linear in
/// these three counters at all. Wide disagreement would mean the model shape is wrong, not that a
/// constant needs nudging.
///
/// Returns `(loop_ns, scan_ns, push_from_card_term, push_from_row_term)`.
fn recover_rates(card: [f64; 2], printing: [f64; 2]) -> (f64, f64, f64, f64) {
    (printing[0], card[1], card[0] - printing[0], printing[1] - card[1])
}

fn predict(c: &Cell, k: [f64; 3]) -> f64 {
    c.cards * k[0] + c.printings * k[1] + c.matches * k[2]
}

#[test]
#[ignore = "micro-benchmark; needs benchmarks/verify-order/real.store (see module docs)"]
fn bench_gather_loop() {
    let path = store_path();
    let Ok(file) = std::fs::File::open(&path) else {
        eprintln!("SKIP: {path} not found (see module docs)");
        return;
    };
    // Safety: same contract as bench_verify_cost / get_mmap() — written by rkyv::to_bytes and
    // replaced atomically, and the header is re-validated below before the payload is trusted.
    let mmap = unsafe { Mmap::map(&file) }.expect("mmap real.store");
    if mmap.len() < ARCHIVE_HEADER_LEN || mmap[..ARCHIVE_HEADER_LEN] != archive_header() {
        eprintln!("SKIP: {path} header mismatch (stale archive — rebuild it, see module docs)");
        return;
    }
    println!("\nstore {path}  ({:.0} MB)", mmap.len() as f64 / (1024.0 * 1024.0));
    let data = unsafe { rkyv::access_unchecked::<Archived<CardData>>(archive_payload(&mmap)) };
    let ctx = QueryCtx::from(data);

    // Group cards by printing count. This is the knob sampled traffic does not have: it sets
    // printings-per-card independently of how many cards the loop visits.
    let (mut singleton, mut medium, mut wide): (Vec<u32>, Vec<u32>, Vec<u32>) = (Vec::new(), Vec::new(), Vec::new());
    for cid in 0..data.cards.len() {
        let span = u32::from(data.offsets[cid + 1]) as usize - u32::from(data.offsets[cid]) as usize;
        if span == 1 {
            singleton.push(cid as u32);
        } else if span >= WIDE_MIN_PRINTINGS {
            wide.push(cid as u32);
        } else {
            medium.push(cid as u32);
        }
    }
    println!(
        "\n{} oracle cards: {} with 1 printing, {} with 2..{WIDE_MIN_PRINTINGS}, {} with >={WIDE_MIN_PRINTINGS}",
        data.cards.len(),
        singleton.len(),
        medium.len(),
        wide.len()
    );
    // How much headroom `WIDE_MIN_PRINTINGS` and `CARD_COUNTS` have. The tail is steep, and the
    // largest cell cannot exceed the wide group, so this is the table those two constants are
    // chosen from rather than guessed at.
    print!("cards with >=n printings:");
    for threshold in 2..=10usize {
        let n = (0..data.cards.len())
            .filter(|&cid| u32::from(data.offsets[cid + 1]) as usize - u32::from(data.offsets[cid]) as usize >= threshold)
            .count();
        print!("  {threshold}:{n}");
    }
    println!();

    // `cmc >= -1` holds for every card, and cmc is a CARD field, so `card_pass` resolves it to
    // Tri::True with an empty residual — the tier stays 0 and `push_card_matches` still gets
    // `all_match = true`. On the `all_match_known` cells nothing evaluates it at all, so the loop is
    // card visit + push + absorb and the three rates come out clean of any predicate cost. On the
    // cells that clear that flag it IS evaluated, which is what prices the call.
    //
    // A real evaluated predicate rather than `FilterExpr::True`, deliberately: `True` short-circuits
    // to nearly free and would price the call at ~0, and `NumericCmp` is what `bench_verify_cost`
    // already classes as tier 0, so this is the cheapest predicate production actually runs.
    let filter =
        FilterExpr::NumericCmp { lhs: NumExpr::Field(NumField::Cmc), op: CmpOp::Ge, rhs: NumExpr::Const(-1.0) };
    let mut cells: Vec<Cell> = Vec::new();

    // (label, source group, unique, prefer, all_match_known)
    //
    // A' repeats A last as a control. A and B are the same cards with one printing each and report
    // identical counters, so any time difference between them is either a real `unique` effect or an
    // artifact of A running first and paying first-touch on the mmap. A' vs A separates those: equal
    // means the difference is real, A' matching B means it was ordering.
    //
    // The `false` rows are the same cells with `all_match_known` cleared, so `card_pass` actually
    // runs. The filter answers `Tri::True` and leaves the residual empty, so `push_card_matches`
    // still gets `all_match = true` and every counter is identical — the ONLY difference is the
    // `card_pass` call itself. Their gap over the matching `true` row prices that call, which is what
    // `GATHER_RESIDUAL_FLOOR` is for; without it, lowering the card term would silently remove charge
    // that was covering a predicate evaluation on every non-all_match query.
    // The E/F rows are artwork mode, and they are here because leaving them out is what made the
    // first attempt at these constants fail. cost.rs applies one set of GATHER_* rates to all three
    // modes, but the artwork loop also maintains `group_best` and `touched` per printing, and P4 has
    // no artwork term to carry that (P3 has STREAM_ARTWORK_SEEN_PER_CARD_NS). So the extra work was
    // absorbed into the shipped rates, and a design covering only card and printing mode measured
    // them low, under-costing artwork. Shipping that moved total routing regret 36.2 -> 51.7 ms, with
    // candidates/artwork going 19% -> 35% of all lost time. Artwork has to be in the design for these
    // rates to mean anything.
    let designs: [(&'static str, &Vec<u32>, &str, &str, bool); 12] = [
        ("A 1p  card/default", &singleton, "card", "default", true),
        ("B 1p  print/default", &singleton, "printing", "default", true),
        ("C wide card/oldest", &wide, "card", "oldest", true),
        ("D wide print/default", &wide, "printing", "default", true),
        ("E 1p  artwork/default", &singleton, "artwork", "default", true),
        ("F wide artwork/default", &wide, "artwork", "default", true),
        ("G med artwork/default", &medium, "artwork", "default", true),
        ("H med card/oldest", &medium, "card", "oldest", true),
        ("I med print/default", &medium, "printing", "default", true),
        ("A' 1p card/default ctl", &singleton, "card", "default", true),
        ("A+ 1p  card, card_pass", &singleton, "card", "default", false),
        ("C+ wide card, card_pass", &wide, "card", "oldest", false),
    ];

    println!(
        "\n{:<22}{:>7}{:>10}{:>11}{:>10}{:>12}{:>11}{:>11}",
        "cell", "n", "cards", "printings", "matches", "ns_loop", "ns/card", "ns_setup"
    );
    // Every cell is built up front, then all of them are run inside ONE iteration loop, so each
    // cell's minimum is drawn from the same time window as every other cell's. Running a cell to
    // completion before starting the next one lets machine drift between cells enter the fit as if it
    // were a rate difference: doing exactly that moved the card term to 4.34 while three interleaved
    // runs held it inside 3.08-3.30, and left two cells with identical counters differing 1.8×. Same
    // reason the query-level harnesses interleave A/B/A/B instead of running all of A then all of B.
    struct Config {
        label: &'static str,
        n_cards_req: usize,
        /// The WHOLE group, not one slice of it. Each iteration walks a different chunk of `n_cards_req`
        /// cards, so consecutive walks of a cell share no cards and cannot inherit each other's cache
        /// lines. Holding one fixed slice and taking the minimum over 200 passes is what made every rate
        /// here a warm-cache number, 1.6-2.2x under what the first pass costs.
        group: Vec<u32>,
        all_match_known: bool,
        params: QueryParams,
        ran_card_pass: bool,
        mode: &'static str,
    }
    impl Config {
        /// Candidates for one iteration: a rotating window over the group, wrapping when it runs out. A
        /// full rotation touches every card in the group before returning to the first chunk.
        /// `stagger` is the cell's own index, so cells sharing a group walk DIFFERENT chunks within one
        /// iteration. Without it every cell on a group walked the same chunk, the first paid all the
        /// misses and the rest inherited warm lines -- which read as cell A costing 10.50 ns/card against
        /// cell B's 6.11 on identical cards. `chunks` therefore has to exceed the number of cells sharing
        /// a group, which the real corpus cannot supply at the larger card counts (the wide group holds
        /// 8,036 cards, so one chunk at 4,500). That is what `scripts/upscale_corpus.py` is for.
        fn prep_for(&self, iter: usize, stagger: usize) -> PreparedCandidates {
            let chunks = (self.group.len() / self.n_cards_req).max(1);
            let start = ((iter + stagger) % chunks) * self.n_cards_req;
            let end = (start + self.n_cards_req).min(self.group.len());
            PreparedCandidates {
                candidate_cards: Some(self.group[start..end].to_vec()),
                all_match_known: self.all_match_known,
                narrowed_repr: NarrowedRepr::Cards,
            }
        }
    }
    let mut configs: Vec<Config> = Vec::new();
    for n_req in CARD_COUNTS {
        for (label, group, unique, prefer, all_match_known) in &designs {
            if group.len() < n_req {
                println!("{label:<22}{n_req:>7}   SKIP (only {} cards in group)", group.len());
                continue;
            }
            configs.push(Config {
                label,
                n_cards_req: n_req,
                group: (*group).clone(),
                all_match_known: *all_match_known,
                params: QueryParams::from_strs(unique, prefer, "name", "asc", LIMIT, 0),
                ran_card_pass: !all_match_known,
                mode: unique,
            });
        }
    }

    // Iteration 0, kept because min-of-ITERS is a WARM-CACHE number: the same card list is walked 200
    // times, so every card, printing and string it touches is resident by the second pass. Production
    // walks a candidate set once. If the first pass is materially slower than the minimum, these rates
    // describe a cache state production never sees, and that -- not a wrong feature -- is why shipping
    // them regresses. The counter/feature ratios all read 1.00, so the features are not the gap.
    // Chunks per cell. Fewer than the number of cells sharing a group means some of them collide on a
    // chunk within an iteration and the later one measures a warm walk, so this is printed rather than
    // assumed -- it is the check that the store is big enough for the design.
    println!("\nchunks available per cell (want more than the cells sharing each group):");
    for cfg in &configs {
        println!("  {:<24}{:>7}  {:>3} chunks of {} from {} cards", cfg.label, cfg.n_cards_req, (cfg.group.len() / cfg.n_cards_req).max(1), cfg.n_cards_req, cfg.group.len());
    }
    let mut first_loop: Vec<f64> = vec![0.0; configs.len()];
    let mut best_setup: Vec<f64> = vec![f64::INFINITY; configs.len()];
    let mut best_ns: Vec<f64> = vec![f64::INFINITY; configs.len()];
    let mut counters: Vec<(f64, f64, f64)> = vec![(0.0, 0.0, 0.0); configs.len()];
    // One per-card sample per iteration. With chunk rotation every pass walks unfamiliar cards, so this
    // distribution is the real one and its MEDIAN is the honest rate; the minimum is just its luckiest draw.
    let mut per_card_samples: Vec<Vec<f64>> = vec![Vec::with_capacity(ITERS); configs.len()];
    for iter in 0..ITERS {
        for (i, cfg) in configs.iter().enumerate() {
            let prep = cfg.prep_for(iter, i);
            black_box(exec_gathered_scan(&ctx, &cfg.params, &filter, &prep, None));
            let s = take_phase_stats();
            if iter == 0 {
                first_loop[i] = s.ns_loop as f64;
            }
            if (s.ns_loop as f64) < best_ns[i] {
                best_ns[i] = s.ns_loop as f64;
            }
            // Per-card, since rotating chunks vary slightly in length at the group's tail. The MEDIAN of
            // these is what the fit uses: with rotation every pass walks unfamiliar cards, so the
            // distribution is the real one and the minimum is just its luckiest draw.
            per_card_samples[i].push(s.ns_loop as f64 / (s.cards_visited.max(1)) as f64);
            // Tracked because artwork mode allocates and zeroes a `max_artwork_groups`-long buffer
            // here, which is O(corpus) work no loop rate can represent and which P4's cost arm carries
            // only in a flat GATHER_FIXED_COST_NS.
            if (s.ns_setup as f64) < best_setup[i] {
                best_setup[i] = s.ns_setup as f64;
            }
            // Counters are deterministic per cell, so any iteration's are the cell's; recording them
            // every time rather than only on the fastest keeps them independent of which run won.
            counters[i] = (s.cards_visited as f64, s.printings_examined as f64, s.matches_pushed as f64);
        }
    }
    for (i, cfg) in configs.iter().enumerate() {
        let (cards, printings, matches) = counters[i];
        let cell = Cell {
            label: cfg.label,
            n_cards_req: cfg.n_cards_req,
            cards,
            printings,
            matches,
            // Median per-card time x cards, so every downstream fit sees a typical rotated walk rather
            // than the warmest one. `best_ns` is kept only to report the warm/typical gap.
            ns_loop: {
                let v = &mut per_card_samples[i];
                v.sort_by(f64::total_cmp);
                v[v.len() / 2] * cards
            },
            ran_card_pass: cfg.ran_card_pass,
            mode: cfg.mode,
        };
        println!(
            "{:<22}{:>7}{:>10.0}{:>11.0}{:>10.0}{:>12.0}{:>11.2}{:>11.0}",
            cell.label,
            cell.n_cards_req,
            cell.cards,
            cell.printings,
            cell.matches,
            cell.ns_loop,
            cell.ns_loop / cell.cards,
            best_setup[i]
        );
        cells.push(cell);
    }

    assert!(cells.len() >= 3, "need at least 3 design points to identify 3 rates, got {}", cells.len());

    // The design's leverage, stated rather than assumed: printings-per-card and matches-per-card
    // must differ across cells or the system is the degenerate one sampled traffic gives.
    println!("\nper-card ratios by cell (these differing IS the design):");
    for c in &cells {
        println!(
            "  {:<22}{:>7} printings/card {:>6.2}   matches/card {:>6.2}",
            c.label,
            c.n_cards_req,
            c.printings / c.cards,
            c.matches / c.cards
        );
    }

    // What a `card_pass` call costs per card, from the cells that differ ONLY by whether it ran.
    // This is the floor `GATHER_RESIDUAL_FLOOR` has to cover for a tier-0 predicate, and it has to be
    // read alongside the card term rather than after it: the two are set together or the total drifts.
    println!("\ncost of the card_pass call itself (paired cells, identical counters):");
    for c in cells.iter().filter(|c| c.ran_card_pass) {
        // The `+` label marks the card_pass variant of the cell whose label shares its first letter.
        let base = cells
            .iter()
            .find(|b| !b.ran_card_pass && b.n_cards_req == c.n_cards_req && b.label.as_bytes()[0] == c.label.as_bytes()[0]);
        match base {
            Some(b) => println!(
                "  {:<26}{:>7}  {:>9.0} ns vs {:>9.0} ns   {:>7.2} ns/card for card_pass",
                c.label,
                c.n_cards_req,
                c.ns_loop,
                b.ns_loop,
                (c.ns_loop - b.ns_loop) / c.cards
            ),
            None => println!("  {:<26}{:>7}  no paired all_match cell", c.label, c.n_cards_req),
        }
    }

    let Some(k) = fit(&cells, &["card", "printing"]) else {
        println!("\nsystem still singular — the design did not separate the columns");
        return;
    };
    // What artwork mode costs ON TOP of the three fitted rates. cost.rs has no artwork term for P4,
    // so today this sits inside the shared rates, which is why measuring them without artwork in the
    // design and then lowering them under-costs artwork queries.
    // Finish-phase sweep. Runs the SAME candidate set at four pages, so the loop's work is identical
    // across rows and every difference in `ns_finish` is the select-and-collect. Chunk-rotated like
    // everything else, and the median is reported.
    //
    // `page_span` is `min(offset + limit, matches)`, so the (900, 60) row is capped by matches rather
    // than by the page: with 4,500 matches it asks for span 960, where (0, 600) asks for 600. That is
    // the row that separates a genuine per-slot rate from `offset` merely being large.
    if singleton.len() >= CARD_COUNTS[1] {
        println!("\nfinish phase vs page_span (same cards, four pages -- the loop is identical across rows):");
        println!("  {:>7}{:>8}{:>12}{:>12}{:>14}", "offset", "limit", "page_span", "ns_finish", "ns per slot");
        let mut rows: Vec<(f64, f64)> = Vec::new();
        for (offset, limit) in PAGE_SPECS {
            let params = QueryParams::from_strs("card", "default", "name", "asc", limit, offset);
            let mut samples: Vec<f64> = Vec::with_capacity(ITERS);
            let mut span = 0.0f64;
            let chunks = (singleton.len() / CARD_COUNTS[1]).max(1);
            for iter in 0..ITERS {
                let start = (iter % chunks) * CARD_COUNTS[1];
                let end = (start + CARD_COUNTS[1]).min(singleton.len());
                let prep = PreparedCandidates {
                    candidate_cards: Some(singleton[start..end].to_vec()),
                    all_match_known: true,
                    narrowed_repr: NarrowedRepr::Cards,
                };
                black_box(exec_gathered_scan(&ctx, &params, &filter, &prep, None));
                let st = take_phase_stats();
                span = (offset + limit).min(st.matches_pushed as usize) as f64;
                samples.push(st.ns_finish as f64);
            }
            samples.sort_by(f64::total_cmp);
            let ns = samples[samples.len() / 2];
            println!("  {offset:>7}{limit:>8}{span:>12.0}{ns:>12.0}{:>14.2}", ns / span.max(1.0));
            rows.push((span, ns));
        }
        // Two-point solve across the widest span gap, which is the cheapest way to see whether the phase
        // has a fixed component. Through the origin the slope has to absorb any constant, which inflates
        // it on the small-page rows -- the same error that made `GATHER_SELECT_PER_PAGE_SLOT_NS` look 5x
        // wrong earlier in this branch.
        if let (Some(lo), Some(hi)) = (rows.first(), rows.iter().max_by(|a, b| a.0.total_cmp(&b.0)))
            && hi.0 > lo.0
        {
            {
                let slope = (hi.1 - lo.1) / (hi.0 - lo.0);
                println!(
                    "  two-point: {:.2} ns/slot + {:.0} ns fixed   (shipped GATHER_SELECT_PER_PAGE_SLOT_NS 3.51, no fixed term)",
                    slope,
                    lo.1 - slope * lo.0
                );
            }
        }
    }

    // The warm/cold gap, per cell. `cost.rs`'s constants are fitted on production traffic, which pays
    // this; every rate this harness reports is a min-of-200 and does not.
    println!("\nfirst pass vs warm minimum (min-of-{ITERS} hides whatever the cold walk costs):");
    let (mut sum_first, mut sum_best) = (0.0f64, 0.0f64);
    for (i, c) in cells.iter().enumerate() {
        println!(
            "  {:<24}{:>7}  first {:>9.2} ns/card   warm min {:>8.2}   ratio {:>5.2}x",
            c.label,
            c.n_cards_req,
            first_loop[i] / c.cards,
            c.ns_loop / c.cards,
            first_loop[i] / c.ns_loop
        );
        sum_first += first_loop[i];
        sum_best += c.ns_loop;
    }
    println!("  overall first/warm {:.2}x", sum_first / sum_best);

    println!("\nartwork surcharge over the card/printing fit (P4 has no artwork term today):");
    for c in cells.iter().filter(|c| c.mode == "artwork") {
        let base = predict(c, k);
        println!(
            "  {:<26}{:>7}  measured {:>8.0} ns vs fit {:>8.0} ns   +{:>6.2} ns/printing  +{:>6.2} ns/card",
            c.label,
            c.n_cards_req,
            c.ns_loop,
            base,
            (c.ns_loop - base) / c.printings,
            (c.ns_loop - base) / c.cards
        );
    }

    // A per-query FIXED cost, measured as the intercept of the loop phase rather than taken from a
    // whole-arm traffic fit. `fit_cost_model.py` fits one equation per query against total dispatch, so
    // its intercept absorbs every mis-specification the other columns cannot express -- it read 84 and
    // then 85 while COLLECT_PER_PAGE_ROW moved 15.0 -> 9.79 beneath it, which is exactly that. Here the
    // intercept is identified by cells that differ ONLY in size, so nothing else can hide in it.
    //
    // Grouped by CELL LABEL, and least-squares over that label's three sizes. Both matter, and the
    // first is a correction: this used to filter to a shape and two-point solve on the smallest and
    // largest cells matching it, which crossed cell boundaries whenever two labels shared a shape --
    // it paired `A` at 400 cards with `A'` (the same shape on a different chunk stagger) at 4,500 and
    // reported -1,305 ns, where `A` against itself gives -780. The sign was right either way, but half
    // the magnitude was stagger noise, and the magnitude is what a refit decision needs. One label
    // across sizes holds the chunk stagger fixed, so only the size varies; printing per label rather
    // than per mode then shows the spread instead of hiding it inside one number.
    {
        println!("\nloop-phase fixed cost and curvature per cell, single-printing shapes:");
        println!(
            "  {:<26}{:>10}{:>12}{:>10}{:>10}{:>9}{:>12}",
            "cell", "ns/card", "intercept", "small", "large", "curve", "fixed"
        );
        // First occurrence of each label, in design order. `dedup()` would not do: cells are emitted
        // size-major, so every label recurs once per size and only CONSECUTIVE repeats would collapse.
        let mut labels: Vec<&str> = Vec::new();
        for c in &cells {
            if !labels.contains(&c.label) {
                labels.push(c.label);
            }
        }
        for label in labels {
            // Single-printing shapes only. A fit on `cards` alone is interpretable only where printings
            // track cards: a wide cell's cost is mostly per-PRINTING, so its slope reads 22-26 ns/card
            // instead of ~11 and its intercept collects whatever that mis-attribution leaves over
            // (`D wide print/default` read +6,498 ns). Same filter the per-mode version used, kept.
            let same: Vec<&Cell> =
                cells.iter().filter(|c| c.label == label && !c.ran_card_pass && (c.printings / c.cards) < 1.5).collect();
            // Least squares on `ns_loop = intercept + slope * cards`, over every size this label ran.
            let n = same.len() as f64;
            if n < 2.0 {
                continue;
            }
            let (sx, sy) = (same.iter().map(|c| c.cards).sum::<f64>(), same.iter().map(|c| c.ns_loop).sum::<f64>());
            let sxx = same.iter().map(|c| c.cards * c.cards).sum::<f64>();
            let sxy = same.iter().map(|c| c.cards * c.ns_loop).sum::<f64>();
            let denom = n * sxx - sx * sx;
            if denom.abs() < f64::EPSILON {
                continue;
            }
            let slope = (n * sxy - sx * sy) / denom;
            let intercept = (sy - slope * sx) / n;
            // The whole-range line is reported for continuity with earlier runs, but it is NOT the
            // number to read: the two smallest and two largest sizes are dominated by different effects,
            // and a single line through them lands between the two with a negative intercept. Solve them
            // apart. The small pair is where a per-query fixed cost is visible at all (at 4,500 cards a
            // 170 ns constant is 0.04 ns/card, far under the cell-to-cell spread); the large pair is
            // where the per-card rate has grown, which is the curvature.
            let mut by_size: Vec<&&Cell> = same.iter().collect();
            by_size.sort_by(|a, b| a.cards.total_cmp(&b.cards));
            let secant = |lo: &Cell, hi: &Cell| (hi.ns_loop - lo.ns_loop) / (hi.cards - lo.cards);
            let (small, large) = match (by_size.first(), by_size.get(1), by_size.iter().rev().nth(1), by_size.last()) {
                (Some(a), Some(b), Some(c), Some(d)) => (secant(a, b), secant(c, d)),
                _ => (f64::NAN, f64::NAN),
            };
            let small_intercept = by_size.first().map_or(f64::NAN, |c| c.ns_loop - small * c.cards);
            println!(
                "  {:<26}{:>10.2}{:>12.0}{:>10.2}{:>10.2}{:>9.2}{:>12.0}",
                label, slope, intercept, small, large, large / small, small_intercept
            );
        }
        println!("  shipped GATHER_FIXED_COST_NS 169.6; a whole-arm traffic fit puts it at 85.");
        println!("  Read the last two columns, not the first two. `large/small` is the CURVATURE the arm has");
        println!("  no term for; `fixed` is what the small pair says a per-query cost is, and the whole-range");
        println!("  intercept goes NEGATIVE only because one line cannot hold both ends at once.");
    }

    // The reparameterised fit: each mode fitted in the two-parameter space it can actually support,
    // then the three original rates recovered from the pair of pairs.
    let card_pair = fit_mode(&cells, "card", 0.0);
    let print_pair = fit_mode(&cells, "printing", 0.0);
    if let (Some(cp), Some(pp)) = (card_pair, print_pair) {
        let (loop_ns, scan_ns, push_a, push_b) = recover_rates(cp, pp);
        println!("\n{:<30}{:>14}{:>16}", "collapsed pair per mode", "ns/card", "ns/printing");
        println!("{:<30}{:>14.2}{:>16.2}   = (LOOP+PUSH), SCAN", "card mode", cp[0], cp[1]);
        println!("{:<30}{:>14.2}{:>16.2}   = LOOP, (SCAN+PUSH)", "printing mode", pp[0], pp[1]);
        println!("\nrecovered, without pooling the modes:");
        println!("  LOOP  = A_print          {loop_ns:>7.2} ns/card");
        println!("  SCAN  = B_card           {scan_ns:>7.2} ns/printing");
        println!("  PUSH  = A_card - A_print {push_a:>7.2} ns/match   <-- two independent estimates;");
        println!("  PUSH  = B_print - B_card {push_b:>7.2} ns/match       agreement tests the model shape");
        let (lo, hi) = (push_a.min(push_b), push_a.max(push_b));
        let spread = if lo > 0.0 { hi / lo } else { f64::INFINITY };
        println!("  spread {spread:>.2}x  (a linear loop in these three counters requires these to agree)");

        // Artwork keeps all three terms: its matches are artwork GROUPS, ~1.2-2.4 per card, so the
        // column is genuinely distinct there rather than a duplicate. The push is held at the recovered
        // value because artwork groups grow with printings -- an unconstrained artwork solve answers
        // with a negative match rate that interpolates the cells and means nothing.
        let push = 0.5 * (push_a + push_b);
        if let Some(ap) = fit_mode(&cells, "artwork", push) {
            println!("\n{:<30}{:>14}{:>16}{:>14}", "artwork arm", "ns/card", "ns/printing", "ns/group");
            println!("{:<30}{:>14.2}{:>16.2}{:>14.2}", "artwork mode", ap[0], ap[1], push);

            // Agreement per cell under the reparameterised model, which is the only thing that decides
            // whether this is better than the mode-blind triple it replaces.
            println!("\n  per-cell agreement, reparameterised (predicted/actual):");
            let mut worst = 0.0f64;
            for c in cells.iter().filter(|c| !c.ran_card_pass) {
                let pred = match c.mode {
                    "artwork" => c.cards * ap[0] + c.printings * ap[1] + c.matches * push,
                    "printing" => c.cards * pp[0] + c.printings * pp[1],
                    _ => c.cards * cp[0] + c.printings * cp[1],
                };
                let ratio = pred / c.ns_loop;
                worst = worst.max((ratio.ln()).abs());
                println!("    {:<24}{:>7}{:>10.2}", c.label, c.n_cards_req, ratio);
            }
            println!("  worst |log ratio| {:.3}  ({:.0}% off at the worst cell)", worst, (worst.exp() - 1.0) * 100.0);
        }
    }

    println!("\n{:<34}{:>10}{:>10}", "rate", "shipped", "fitted");
    for (name, shipped, got) in [
        ("GATHER_CARD_PASS_NS", 6.88, k[0]),
        ("GATHER_SCAN_PER_ROW_NS", 2.06, k[1]),
        ("GATHER_PUSH_PER_MATCH_NS", 2.24, k[2]),
    ] {
        println!("{name:<34}{shipped:>10.2}{got:>10.2}");
    }

    // Per-cell agreement under both constant sets. The fitted set should beat the shipped one on
    // every cell; if it only wins on average, the split is still not doing real work.
    println!("\n{:<22}{:>7}{:>14}{:>14}", "cell", "n", "shipped p/a", "fitted p/a");
    let shipped = [6.88, 2.06, 2.24];
    for c in &cells {
        println!(
            "{:<22}{:>7}{:>14.2}{:>14.2}",
            c.label,
            c.n_cards_req,
            predict(c, shipped) / c.ns_loop,
            predict(c, k) / c.ns_loop
        );
    }
}
