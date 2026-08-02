//! Evidence for collapsing `push_card_matches`' Card arm (#799 follow-up to
//! `bench_card_match_unify`).
//!
//! That arm used to be three blocks: one `if let Some(..) = existential_plane` block holding
//! both prefer cases behind a `satisfies` closure, then a no-plane default-prefer block, then a
//! no-plane custom-prefer block. The plane distinction is now a `const HAS_PLANE: bool`, so the
//! body is written once; the *prefer* distinction stays, because it is algorithmic — printings
//! are stored default-prefer-descending, so default prefer takes the first satisfying printing
//! and stops, while a custom prefer must score every one.
//!
//! `push_old` preserves the historical three-block shape verbatim. The shipped
//! `push_card_matches` is measured against it, with an old-vs-old control column for the noise
//! floor — without which none of these ratios can be read (see `bench_card_match_unify`, where
//! the floor on one row ran to ±12%).
//!
//! Emission, unlike counting, produces a `Vec<Match>`, so agreement here is checked on the
//! actual emitted tuples — sort key, cid and chosen pid — not just a count. Picking a
//! *different but equally valid* representative printing would change the rows a user sees, and
//! `Match` equality is what catches that.
//!
//!     cargo test --release bench_push_card_matches -- --ignored --nocapture
//!
//! Needs benchmarks/verify-order/real.store (same file/rebuild contract as bench_verify_cost.rs).

use std::hint::black_box;
use std::time::Instant;

use rkyv::Archived;

use super::{
    archive_header, archive_payload, prefer_score, sort_key_bits, AOracleCard, APrinting, AStrings, BitPlanes, CardData, CmpOp, FilterExpr,
    Match, Mmap, Mode, NumExpr, NumField, PlaneExpr, Prefer, SortCol, ARCHIVE_HEADER_LEN,
};

const ITERS: usize = 120;
const STORE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../benchmarks/verify-order/real.store");

/// The historical Card arm: three blocks, plane hoisted by duplicating the source. Printing and
/// artwork modes are omitted — the collapse did not touch them, so there is nothing to compare.
#[allow(clippy::too_many_arguments)]
#[allow(clippy::needless_range_loop)]
#[inline(always)]
fn push_old(
    card: &AOracleCard,
    cid: u32,
    printings: &[APrinting],
    start: usize,
    end: usize,
    all_match: bool,
    residual: &[&FilterExpr],
    residual_is_or: bool,
    prefer: Prefer,
    sort_col: SortCol,
    descending: bool,
    strings: &AStrings,
    existential_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)>,
    out: &mut Vec<Match>,
) {
    let chosen: Option<u32> = if let Some((pe, planes)) = existential_plane {
        let satisfies = |pid: usize| {
            super::eval_plane_expr_for_printing(pe, planes, cid, &printings[pid], strings)
                && (all_match || FilterExpr::residual_matches(card, &printings[pid], strings, residual, residual_is_or))
        };
        if matches!(prefer, Prefer::Default) {
            (start..end).find(|&pid| satisfies(pid)).map(|pid| pid as u32)
        } else {
            let mut chosen: Option<(u32, f64)> = None;
            for pid in start..end {
                if !satisfies(pid) {
                    continue;
                }
                let score = prefer_score(card, &printings[pid], prefer);
                if chosen.is_none_or(|(_, s)| score > s) {
                    chosen = Some((pid as u32, score));
                }
            }
            chosen.map(|(pid, _)| pid)
        }
    } else if matches!(prefer, Prefer::Default) {
        let mut found: Option<u32> = None;
        for pid in start..end {
            if all_match || FilterExpr::residual_matches(card, &printings[pid], strings, residual, residual_is_or) {
                found = Some(pid as u32);
                break;
            }
        }
        found
    } else {
        let mut chosen: Option<(u32, f64)> = None;
        for pid in start..end {
            let p = &printings[pid];
            if !all_match && !FilterExpr::residual_matches(card, p, strings, residual, residual_is_or) {
                continue;
            }
            let score = prefer_score(card, p, prefer);
            if chosen.is_none_or(|(_, s)| score > s) {
                chosen = Some((pid as u32, score));
            }
        }
        chosen.map(|(pid, _)| pid)
    };
    if let Some(pid) = chosen {
        out.push((sort_key_bits(card, &printings[pid as usize], sort_col, descending), cid, pid));
    }
}

/// Which shape to run. `OldControl` is `Old` again, measured separately: its ratio against `Old`
/// is the noise floor, and nothing else in the table is readable without it.
#[derive(Clone, Copy)]
enum Which {
    Old,
    Shipped,
    OldControl,
}

fn best_ns(mut kernel: impl FnMut() -> usize) -> (u128, usize) {
    let mut best = u128::MAX;
    let mut n = 0;
    for _ in 0..ITERS {
        let t0 = Instant::now();
        n = black_box(kernel());
        best = best.min(t0.elapsed().as_nanos());
    }
    (best, n)
}

#[test]
#[ignore = "micro-benchmark; needs benchmarks/verify-order/real.store (see module docs)"]
fn bench_push_card_matches() {
    let Ok(file) = std::fs::File::open(STORE_PATH) else {
        eprintln!("SKIP: {STORE_PATH} not found (see module docs)");
        return;
    };
    let mmap = unsafe { Mmap::map(&file) }.expect("mmap real.store");
    if mmap.len() < ARCHIVE_HEADER_LEN || mmap[..ARCHIVE_HEADER_LEN] != archive_header() {
        eprintln!("SKIP: {STORE_PATH} header mismatch (stale archive — rebuild it, see module docs)");
        return;
    }
    let data = unsafe { rkyv::access_unchecked::<Archived<CardData>>(archive_payload(&mmap)) };
    let (cards, printings, offsets) = (&data.cards, &data.printings, &data.offsets);
    let strings = &data.strings;
    let artwork_group_col = &data.indexes.artwork_group_col;
    let max_groups = usize::from(u16::from(data.indexes.max_artwork_groups));
    println!("\n{} printings, {} cards from {STORE_PATH}", printings.len(), cards.len());

    let cmc = FilterExpr::NumericCmp { lhs: NumExpr::Field(NumField::Cmc), op: CmpOp::Lt, rhs: NumExpr::Const(3.0) };
    let residual_leaf: [&FilterExpr; 1] = [&cmc];

    // As in bench_card_match_unify: the plane `Option` and `all_match` go through `black_box`, or
    // the optimizer folds away the branch the const generic exists to remove and every variant
    // measures as free.
    let sweep = |prefer: Prefer, all_match: bool, residual: &[&FilterExpr], which: Which| -> usize {
        let no_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)> = black_box(None);
        let all_match = black_box(all_match);
        let mut out: Vec<Match> = Vec::with_capacity(cards.len());
        let mut group_best: Vec<Option<(u32, f64)>> = vec![None; max_groups];
        let mut touched: Vec<u16> = Vec::new();
        for cid in 0..cards.len() {
            let card = &cards[cid];
            let start = u32::from(offsets[cid]) as usize;
            let end = u32::from(offsets[cid + 1]) as usize;
            match which {
                Which::Old | Which::OldControl => push_old(
                    card, cid as u32, printings, start, end, all_match, residual, false, prefer, SortCol::EdhrecRank, false, strings,
                    no_plane, &mut out,
                ),
                Which::Shipped => super::push_card_matches(
                    card, cid as u32, printings, artwork_group_col, start, end, all_match, residual, false, Mode::Card, prefer,
                    SortCol::EdhrecRank, false, strings, no_plane, &mut out, &mut group_best, &mut touched,
                ),
            }
        }
        out.len()
    };

    // The emitted rows themselves, for the agreement check below.
    let rows = |prefer: Prefer, all_match: bool, residual: &[&FilterExpr], which: Which| -> Vec<Match> {
        let no_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)> = black_box(None);
        let mut out: Vec<Match> = Vec::new();
        let mut group_best: Vec<Option<(u32, f64)>> = vec![None; max_groups];
        let mut touched: Vec<u16> = Vec::new();
        for cid in 0..cards.len() {
            let card = &cards[cid];
            let start = u32::from(offsets[cid]) as usize;
            let end = u32::from(offsets[cid + 1]) as usize;
            match which {
                Which::Old | Which::OldControl => push_old(
                    card, cid as u32, printings, start, end, all_match, residual, false, prefer, SortCol::EdhrecRank, false, strings,
                    no_plane, &mut out,
                ),
                Which::Shipped => super::push_card_matches(
                    card, cid as u32, printings, artwork_group_col, start, end, all_match, residual, false, Mode::Card, prefer,
                    SortCol::EdhrecRank, false, strings, no_plane, &mut out, &mut group_best, &mut touched,
                ),
            }
        }
        out
    };

    println!("\n  {:<40} {:>10} {:>10} {:>9} {:>8} {:>8}", "case (Mode::Card, no plane)", "old ns", "shipped ns", "ship/old", "FLOOR", "rows");
    let cases: &[(&str, Prefer, bool, &[&FilterExpr])] = &[
        ("prefer=default, all_match", Prefer::Default, true, &[]),
        ("prefer=default, residual cmc<3", Prefer::Default, false, &residual_leaf),
        ("prefer=usd_high, all_match", Prefer::UsdHigh, true, &[]),
        ("prefer=usd_high, residual cmc<3", Prefer::UsdHigh, false, &residual_leaf),
        ("prefer=oldest, residual cmc<3", Prefer::Oldest, false, &residual_leaf),
    ];
    for &(label, prefer, all_match, residual) in cases {
        // Agreement on the emitted tuples, not just the count: a different-but-valid
        // representative printing would change what a user sees and must fail here.
        let a = rows(prefer, all_match, residual, Which::Old);
        let b = rows(prefer, all_match, residual, Which::Shipped);
        assert_eq!(a, b, "{label}: SHIPPED push_card_matches emits different rows than the historical shape");

        let mut best = [u128::MAX; 3];
        let mut n = 0;
        for pass in 0..2 {
            for i in 0..3 {
                let w = [Which::Old, Which::Shipped, Which::OldControl][if pass == 0 { i } else { 2 - i }];
                let (ns, got) = best_ns(|| sweep(prefer, all_match, residual, w));
                let slot = match w {
                    Which::Old => 0,
                    Which::Shipped => 1,
                    Which::OldControl => 2,
                };
                best[slot] = best[slot].min(ns);
                n = got;
            }
        }
        println!(
            "  {label:<40} {:>10} {:>10} {:>8.3}x {:>7.3}x {n:>8}",
            best[0],
            best[1],
            best[1] as f64 / best[0] as f64,
            best[2] as f64 / best[0] as f64,
        );
    }
    println!("\n  ship/old > 1 means the collapsed version is slower. Read it against FLOOR (old vs old).");
}
