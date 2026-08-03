//! Micro-benchmark that decomposes `GatheredScan`'s match loop into its three rates.
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
//! Adding an artwork ARM (per-card and per-printing rates gated on `artwork_seen_cards`, which is
//! `eval_domain` in artwork mode and 0 otherwise) was tried next and measured:
//!
//!     card + printing pooled    3.41 ns/card   2.36 ns/printing   0.80 ns/match
//!     printing mode             3.41           2.36               0.76  (card/row shared)
//!     artwork mode              6.68           1.20               0.80  (push shared)
//!
//! Artwork does MORE per card (walk `touched`, push per group, reset `group_best`) and LESS per
//! printing (a group-id lookup and compare instead of a sort-key push), which is the mechanism behind
//! the sign flip. That arm FIXED artwork at the gate -- artwork went 52% -> 50% of lost time, mean
//! 3.40 -> 3.55 -- but total regret was still worse, 40.7 -> 44.3 ms, with the damage moving to
//! printing mode (mean 1.69 -> 2.10). Printing's own push rate fits 0.76 against 0.80, a 5% gap that
//! cannot account for it, so a third arm does not fix it either: lowering P4's cost simply wins it
//! more printing-mode queries where P3 was genuinely better. Reverted again.
//!
//! The structural obstacle found on the way is the durable result. No single mode can identify all
//! three rates, because in each one a column is a DUPLICATE: card mode pushes once per card, so
//! `matches == cards`; printing mode pushes every printing it examines, so `matches == printings`.
//! Only pooling card and printing separates the three, which is in direct tension with the rates
//! differing by mode. So "fit each mode separately" is not available, and the way out is to
//! REPARAMETERISE onto what each mode can identify:
//!
//!     card mode      (LOOP + PUSH) per card, SCAN per printing
//!     printing mode  LOOP per card, (SCAN + PUSH) per printing
//!     artwork mode   LOOP_art per card, SCAN_art per printing, PUSH per group
//!
//! Every parameter there is identifiable in the mode that uses it, and no mode carries a column that
//! duplicates another. That is the next thing to try; it is a different model, not a refit.
//!
//! Until then the shipped mode-blind constants are doing real work by being too high: they absorb
//! artwork's cost, and every attempt to lower them toward the measured card/printing truth has lost
//! more elsewhere than it gained. The constants are deliberately unchanged.
//!
//! One further limit on all of these: `LIMIT` is fixed, so nothing here constrains how the rates
//! behave as the page grows.
//!
//!     cargo test --release bench_gather_loop -- --ignored --nocapture
//!
//! Needs benchmarks/verify-order/real.store, shared with `bench_verify_cost` — rebuild it the
//! same way (see that module's docs) after any AOracleCard/APrinting layout change.

use std::hint::black_box;

use rkyv::Archived;

use super::{
    archive_header, archive_payload, exec_gathered_scan, take_phase_stats, CardData, CmpOp, FilterExpr, Mmap, NarrowedRepr,
    NumExpr, NumField, PreparedCandidates, QueryCtx, QueryParams, ARCHIVE_HEADER_LEN,
};

/// Timed repetitions per cell; the minimum is reported, as elsewhere in these harnesses.
const ITERS: usize = 200;
/// A "wide" card has at least this many printings. Sets the leverage between the card column and
/// the printing column: a bigger gap separates them better, but the corpus has a steep tail — the
/// group-size table this prints shows only 1,910 cards reach 8 printings, which is too few to fill
/// the larger cells. 4 keeps the leverage worth having and the wide group ~4× bigger.
const WIDE_MIN_PRINTINGS: usize = 4;
/// Card counts each cell runs at. Two sizes so linearity in the card term is measured, not assumed;
/// bounded above by the wide group, which is the scarce one. The single-printing cell runs 6.31 →
/// 7.67 ns/card between the two, so this is not a formality — the card term is mildly superlinear
/// and a one-size design would bake whichever size it used into the constant.
const CARD_COUNTS: [usize; 2] = [1_500, 4_500];
/// Page requested. Small and fixed: `sel.absorb()` runs INSIDE the loop and prunes toward
/// `offset + limit`, so this is part of what the per-match rate has to cover — keeping it constant
/// keeps that contribution proportional to matches rather than to the page.
const LIMIT: usize = 60;

const STORE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../benchmarks/verify-order/real.store");

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
            for k in col..4 {
                a[row][k] -= f * a[col][k];
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

/// Artwork's card and printing rates with the MATCH rate held at the value fitted for the other
/// modes. Fitting all three on artwork alone is ill-conditioned -- artwork groups grow with printings,
/// so `matches` and `printings` are collinear inside artwork, and the unconstrained solve answers with
/// a large printing rate against a NEGATIVE match rate (-60 ns) that interpolates the cells and means
/// nothing. Holding the push shared is not a convenience: it is the same `GatherSelect` push in every
/// mode, so the constraint is the physical claim, and it leaves 2 unknowns against 3 ratio levels.
fn fit_artwork(cells: &[Cell], push_ns: f64) -> Option<[f64; 2]> {
    let mut normal = [[0.0f64; 3]; 2];
    for c in cells.iter().filter(|c| !c.ran_card_pass && c.mode == "artwork") {
        let w = 1.0 / c.ns_loop;
        let x = [c.cards * w, c.printings * w];
        // The shared push comes off the target, exactly as `design_row` handles cost.rs's offsets.
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

/// Printing mode's push rate, with per-card and per-printing held at the card-mode values.
///
/// Only one unknown is available: printing mode pushes every printing it examines, so `matches ==
/// printings` exactly and those two columns are IDENTICAL within the mode -- no design over real cards
/// can separate them. The bit test is the same work in both modes, so the rate that is allowed to move
/// is the push, which is what actually differs (consecutive printings of one card push into the
/// selector back to back, where card mode pushes one per card).
fn fit_printing_push(cells: &[Cell], per_card: f64, per_row: f64) -> Option<f64> {
    let (mut num, mut den) = (0.0f64, 0.0f64);
    for c in cells.iter().filter(|c| !c.ran_card_pass && c.mode == "printing") {
        let w = 1.0 / c.ns_loop;
        let x = c.matches * w;
        num += x * (c.ns_loop - c.cards * per_card - c.printings * per_row) * w;
        den += x * x;
    }
    if den <= 0.0 {
        return None;
    }
    Some(num / den)
}

fn predict(c: &Cell, k: [f64; 3]) -> f64 {
    c.cards * k[0] + c.printings * k[1] + c.matches * k[2]
}

#[test]
#[ignore = "micro-benchmark; needs benchmarks/verify-order/real.store (see module docs)"]
fn bench_gather_loop() {
    let Ok(file) = std::fs::File::open(STORE_PATH) else {
        eprintln!("SKIP: {STORE_PATH} not found (see module docs)");
        return;
    };
    // Safety: same contract as bench_verify_cost / get_mmap() — written by rkyv::to_bytes and
    // replaced atomically, and the header is re-validated below before the payload is trusted.
    let mmap = unsafe { Mmap::map(&file) }.expect("mmap real.store");
    if mmap.len() < ARCHIVE_HEADER_LEN || mmap[..ARCHIVE_HEADER_LEN] != archive_header() {
        eprintln!("SKIP: {STORE_PATH} header mismatch (stale archive — rebuild it, see module docs)");
        return;
    }
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
        prep: PreparedCandidates,
        params: QueryParams,
        ran_card_pass: bool,
        mode: &'static str,
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
                prep: PreparedCandidates {
                    candidate_cards: Some(group[..n_req].to_vec()),
                    all_match_known: *all_match_known,
                    narrowed_repr: NarrowedRepr::Cards,
                },
                params: QueryParams::from_strs(unique, prefer, "name", "asc", LIMIT, 0),
                ran_card_pass: !all_match_known,
                mode: unique,
            });
        }
    }

    let mut best_setup: Vec<f64> = vec![f64::INFINITY; configs.len()];
    let mut best_ns: Vec<f64> = vec![f64::INFINITY; configs.len()];
    let mut counters: Vec<(f64, f64, f64)> = vec![(0.0, 0.0, 0.0); configs.len()];
    for _ in 0..ITERS {
        for (i, cfg) in configs.iter().enumerate() {
            black_box(exec_gathered_scan(&ctx, &cfg.params, &filter, &cfg.prep, None));
            let s = take_phase_stats();
            if (s.ns_loop as f64) < best_ns[i] {
                best_ns[i] = s.ns_loop as f64;
            }
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
            ns_loop: best_ns[i],
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

    // The two candidate shapes, side by side. If artwork's triple differs from the other modes' while
    // card and printing sit together under one triple, the model wants a two-way branch on mode
    // (artwork vs not) rather than one mode-blind triple, an additive artwork correction, or a
    // three-way branch. An additive correction cannot be right if artwork's per-printing rate comes
    // out LOWER, since the term would have to be negative; a three-way branch is only justified if
    // card and printing actually separate.
    if let Some(a2) = fit_artwork(&cells, k[2]) {
        let art = [a2[0], a2[1], k[2]];
        println!("\n{:<26}{:>14}{:>14}{:>14}", "fitted separately", "ns/card", "ns/printing", "ns/match");
        println!("{:<26}{:>14.2}{:>14.2}{:>14.2}", "card + printing pooled", k[0], k[1], k[2]);
        match fit_printing_push(&cells, k[0], k[1]) {
            Some(push) => println!("{:<26}{:>14.2}{:>14.2}{:>14.2}  (card/row shared)", "printing mode", k[0], k[1], push),
            None => println!("{:<26}  no printing cells", "printing mode"),
        }
        println!("{:<26}{:>14.2}{:>14.2}{:>14.2}  (push shared)", "artwork mode", art[0], art[1], art[2]);
        println!("\n  per-cell agreement under the triple fitted for that cell's OWN group:");
        for c in cells.iter().filter(|c| !c.ran_card_pass) {
            let own = if c.mode == "artwork" { art } else { k };
            println!(
                "    {:<24}{:>7}  own-group p/a {:>6.2}   card/printing-triple p/a {:>6.2}",
                c.label,
                c.n_cards_req,
                predict(c, own) / c.ns_loop,
                predict(c, k) / c.ns_loop
            );
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
