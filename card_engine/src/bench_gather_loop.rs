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
//! What it measured (2026-08-03), stable to 1.07× / 1.02× / 1.04× across three runs where the
//! traffic fit could not pin the push rate off the boundary at all:
//!
//!     GATHER_CARD_PASS_NS        shipped 6.88   fitted 3.08-3.30
//!     GATHER_SCAN_PER_ROW_NS     shipped 2.06   fitted 2.44-2.48
//!     GATHER_PUSH_PER_MATCH_NS   shipped 2.24   fitted 0.91-0.95
//!
//! The shipped set over-predicts every cell (1.07-1.92 predicted/actual) and over-predicts MOST
//! on single-printing cards, least on wide card-mode ones — so `GatheredScan` is systematically
//! over-costed on card-heavy candidate sets, which is the direction that loses it to
//! `StreamedSelect` wrongly.
//!
//! Two limits on reading these as drop-in replacements. `all_match_known` is set, so the loop
//! never calls `card_pass` and the fitted card term is loop overhead with NO predicate evaluation
//! in it — correct for the #634 path that also skips it, but queries reaching `card_pass` pay more,
//! which is what the residual-tier term exists to cover. And `LIMIT` is fixed, so nothing here
//! constrains how the rates behave as the page grows. Traffic-level regret and latency matrices
//! remain the gate before any of this ships.
//!
//!     cargo test --release bench_gather_loop -- --ignored --nocapture
//!
//! Needs benchmarks/verify-order/real.store, shared with `bench_verify_cost` — rebuild it the
//! same way (see that module's docs) after any AOracleCard/APrinting layout change.

use std::hint::black_box;

use rkyv::Archived;

use super::{
    archive_header, archive_payload, exec_gathered_scan, take_phase_stats, CardData, CmpOp, FilterExpr, Mmap, NarrowedRepr,
    PreparedCandidates, QueryCtx, QueryParams, ARCHIVE_HEADER_LEN,
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
fn fit(cells: &[Cell]) -> Option<[f64; 3]> {
    let mut normal = [[0.0f64; 4]; 3];
    for c in cells {
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
    let (mut singleton, mut wide): (Vec<u32>, Vec<u32>) = (Vec::new(), Vec::new());
    for cid in 0..data.cards.len() {
        let span = u32::from(data.offsets[cid + 1]) as usize - u32::from(data.offsets[cid]) as usize;
        if span == 1 {
            singleton.push(cid as u32);
        } else if span >= WIDE_MIN_PRINTINGS {
            wide.push(cid as u32);
        }
    }
    println!(
        "\n{} oracle cards: {} with 1 printing, {} with >={WIDE_MIN_PRINTINGS}",
        data.cards.len(),
        singleton.len(),
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

    // all_match_known short-circuits `card_pass`, so the residual tier is exactly zero and this
    // filter is never evaluated — the loop is card visit + push + absorb and nothing else. That
    // isolates the three rates from the residual term, which is fitted separately.
    // `mask: 0` with `Ge` is unconditionally true, but nothing evaluates it either way.
    let filter = FilterExpr::TypeCmp { mask: 0, op: CmpOp::Ge };
    let mut cells: Vec<Cell> = Vec::new();

    // (label, source group, unique, prefer)
    //
    // A' repeats A last as a control. A and B are the same cards with one printing each and report
    // identical counters, so any time difference between them is either a real `unique` effect or an
    // artifact of A running first and paying first-touch on the mmap. A' vs A separates those: equal
    // means the difference is real, A' matching B means it was ordering.
    let designs: [(&'static str, &Vec<u32>, &str, &str); 5] = [
        ("A 1p  card/default", &singleton, "card", "default"),
        ("B 1p  print/default", &singleton, "printing", "default"),
        ("C wide card/oldest", &wide, "card", "oldest"),
        ("D wide print/default", &wide, "printing", "default"),
        ("A' 1p card/default ctl", &singleton, "card", "default"),
    ];

    println!(
        "\n{:<22}{:>7}{:>10}{:>11}{:>10}{:>12}{:>11}",
        "cell", "n", "cards", "printings", "matches", "ns_loop", "ns/card"
    );
    for n_req in CARD_COUNTS {
        for (label, group, unique, prefer) in &designs {
            if group.len() < n_req {
                println!("{label:<22}{n_req:>7}   SKIP (only {} cards in group)", group.len());
                continue;
            }
            let prep = PreparedCandidates {
                candidate_cards: Some(group[..n_req].to_vec()),
                all_match_known: true,
                narrowed_repr: NarrowedRepr::Cards,
            };
            let params = QueryParams::from_strs(unique, prefer, "name", "asc", LIMIT, 0);
            let mut best = f64::INFINITY;
            let mut stats = take_phase_stats();
            for _ in 0..ITERS {
                black_box(exec_gathered_scan(&ctx, &params, &filter, &prep, None));
                let s = take_phase_stats();
                if (s.ns_loop as f64) < best {
                    best = s.ns_loop as f64;
                    stats = s;
                }
            }
            let cell = Cell {
                label,
                n_cards_req: n_req,
                cards: stats.cards_visited as f64,
                printings: stats.printings_examined as f64,
                matches: stats.matches_pushed as f64,
                ns_loop: best,
            };
            println!(
                "{:<22}{:>7}{:>10.0}{:>11.0}{:>10.0}{:>12.0}{:>11.2}",
                cell.label,
                cell.n_cards_req,
                cell.cards,
                cell.printings,
                cell.matches,
                cell.ns_loop,
                cell.ns_loop / cell.cards
            );
            cells.push(cell);
        }
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

    let Some(k) = fit(&cells) else {
        println!("\nsystem still singular — the design did not separate the columns");
        return;
    };
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
