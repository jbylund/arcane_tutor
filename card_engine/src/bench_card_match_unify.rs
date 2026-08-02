//! Re-test of the measurement that put `card_match_count`/`push_card_matches` on #799's
//! "what not to touch" list.
//!
//! `card_match_count` is split in two: an `existential_plane == None` arm that is a plain
//! `match mode` over three specialized loops, and an `existential_plane == Some` arm that
//! builds a `satisfies` closure and runs the same three shapes through it. Its doc explains
//! the split as load-bearing:
//!
//! > A prior version of this function routed both cases through one closure-based
//! > `satisfies` helper regardless of `existential_plane`; measured as a real (~15%)
//! > regression on `banned:modern`/`restricted:vintage` (full-candidate-set scans,
//! > unaffected by `existential_plane` in outcome but paying its indirection anyway) via
//! > the broad survey, not the targeted benchmark
//!
//! Two reasons that number deserves a re-check. It was taken with the coarsest instrument
//! available (end-to-end survey latency, which the doc itself flags), and it was taken at
//! #676 (2026-07-13) — before #737 gave the artwork arms their skip-repped shortcut and
//! before the group id moved to a columnar side array, both of which changed these loops.
//!
//! This bench isolates the kernel. `count_split` is today's shipped shape; `count_unified`
//! is the deduplicated one — a single `satisfies` closure that folds the plane test in, one
//! loop per mode instead of two. Both are compiled here rather than A/B'd across builds, so
//! the same binary, same store, and same candidate order produce both numbers.
//!
//! What `count_unified` must NOT give up, or it would be measuring the wrong thing: the
//! blind `all_match` shortcut (`Card` returns `start < end`, `Printing` returns
//! `end - start`, no loop at all) is a real algorithmic shortcut, not duplication. It is
//! only sound when there is no plane to check, so the unified version keeps it under an
//! explicit `existential_plane.is_none()` guard. The `banned:modern` shape this bench is
//! named for is exactly that case: legality planes (#679) make the narrowing tight, so
//! `all_match` is true and the residual is empty.
//!
//!     cargo test --release bench_card_match_unify -- --ignored --nocapture
//!
//! Needs benchmarks/verify-order/real.store (same file/rebuild contract as bench_verify_cost.rs).

use std::hint::black_box;
use std::time::Instant;

use rkyv::Archived;

use super::{
    archive_header, archive_payload, AOracleCard, APrinting, AStrings, BitPlanes, CardData, CmpOp, FilterExpr, Mmap, Mode, NumExpr,
    NumField, PlaneExpr, ARCHIVE_HEADER_LEN, ARTWORK_GROUP_WORDS,
};

const ITERS: usize = 200;

/// Which shape of the counting kernel to run.
#[derive(Clone, Copy)]
enum Variant {
    /// Today's shipped `card_match_count`: plane check hoisted by duplicating the source.
    Split,
    /// One body, plane check as a runtime branch inside the shared closure.
    Unified,
    /// One body, plane check as a const generic — compiler-generated specializations.
    Generic,
    /// One body, const generic, AND slice iteration rather than indexing by `pid` — the form
    /// `Split`'s no-plane arms use. `eval_plane_expr_for_printing` takes `&Archived<Printing>`,
    /// not an index, so the plane path never needed `pid` either.
    GenericIter,
    /// `Split` again, measured independently. Its ratio against `Split` is the noise floor:
    /// no column below is meaningful unless it clears this.
    SplitControl,
}
const STORE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../benchmarks/verify-order/real.store");

/// Today's shipped shape, copied verbatim from `card_match_count` so the comparison is
/// against what actually runs rather than a paraphrase of it.
#[allow(clippy::too_many_arguments)]
#[allow(clippy::needless_range_loop)]
#[inline(always)]
fn count_split(
    card: &AOracleCard,
    cid: u32,
    printings: &[APrinting],
    artwork_group_col: &Archived<Vec<u16>>,
    start: usize,
    end: usize,
    all_match: bool,
    residual: &[&FilterExpr],
    residual_is_or: bool,
    mode: Mode,
    strings: &AStrings,
    existential_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)>,
    seen_words: &mut [u64; ARTWORK_GROUP_WORDS],
) -> u32 {
    let Some((pe, planes)) = existential_plane else {
        return match mode {
            Mode::Card => {
                if all_match {
                    return u32::from(start < end);
                }
                for p in &printings[start..end] {
                    if FilterExpr::residual_matches(card, p, strings, residual, residual_is_or) {
                        return 1;
                    }
                }
                0
            }
            Mode::Printing => {
                if all_match {
                    return (end - start) as u32;
                }
                let mut n = 0u32;
                for p in &printings[start..end] {
                    if FilterExpr::residual_matches(card, p, strings, residual, residual_is_or) {
                        n += 1;
                    }
                }
                n
            }
            Mode::Artwork => {
                seen_words.fill(0);
                for pid in start..end {
                    let gid = u16::from(artwork_group_col[pid]) as usize;
                    let (word, bit) = (gid / 64, 1u64 << (gid % 64));
                    if seen_words[word] & bit != 0 {
                        continue;
                    }
                    if !all_match && !FilterExpr::residual_matches(card, &printings[pid], strings, residual, residual_is_or) {
                        continue;
                    }
                    seen_words[word] |= bit;
                }
                seen_words.iter().map(|w| w.count_ones()).sum()
            }
        };
    };
    let satisfies = |pid: usize| {
        super::eval_plane_expr_for_printing(pe, planes, cid, &printings[pid], strings)
            && (all_match || FilterExpr::residual_matches(card, &printings[pid], strings, residual, residual_is_or))
    };
    match mode {
        Mode::Card => {
            for pid in start..end {
                if satisfies(pid) {
                    return 1;
                }
            }
            0
        }
        Mode::Printing => {
            let mut n = 0u32;
            for pid in start..end {
                if satisfies(pid) {
                    n += 1;
                }
            }
            n
        }
        Mode::Artwork => {
            seen_words.fill(0);
            for pid in start..end {
                let gid = u16::from(artwork_group_col[pid]) as usize;
                let (word, bit) = (gid / 64, 1u64 << (gid % 64));
                if seen_words[word] & bit != 0 || !satisfies(pid) {
                    continue;
                }
                seen_words[word] |= bit;
            }
            seen_words.iter().map(|w| w.count_ones()).sum()
        }
    }
}

/// The deduplicated shape: one `satisfies` closure covering both plane cases, one loop per
/// mode. The blind `all_match` shortcut is kept, guarded on there being no plane to check —
/// dropping it would make this slower for reasons that have nothing to do with indirection.
#[allow(clippy::too_many_arguments)]
#[allow(clippy::needless_range_loop)]
#[inline(always)]
fn count_unified(
    card: &AOracleCard,
    cid: u32,
    printings: &[APrinting],
    artwork_group_col: &Archived<Vec<u16>>,
    start: usize,
    end: usize,
    all_match: bool,
    residual: &[&FilterExpr],
    residual_is_or: bool,
    mode: Mode,
    strings: &AStrings,
    existential_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)>,
    seen_words: &mut [u64; ARTWORK_GROUP_WORDS],
) -> u32 {
    let satisfies = |pid: usize| {
        existential_plane.is_none_or(|(pe, planes)| super::eval_plane_expr_for_printing(pe, planes, cid, &printings[pid], strings))
            && (all_match || FilterExpr::residual_matches(card, &printings[pid], strings, residual, residual_is_or))
    };
    // Only sound with no plane: with one, every printing still needs its plane test.
    let blind = all_match && existential_plane.is_none();
    match mode {
        Mode::Card => {
            if blind {
                return u32::from(start < end);
            }
            for pid in start..end {
                if satisfies(pid) {
                    return 1;
                }
            }
            0
        }
        Mode::Printing => {
            if blind {
                return (end - start) as u32;
            }
            let mut n = 0u32;
            for pid in start..end {
                if satisfies(pid) {
                    n += 1;
                }
            }
            n
        }
        Mode::Artwork => {
            seen_words.fill(0);
            for pid in start..end {
                let gid = u16::from(artwork_group_col[pid]) as usize;
                let (word, bit) = (gid / 64, 1u64 << (gid % 64));
                if seen_words[word] & bit != 0 || !satisfies(pid) {
                    continue;
                }
                seen_words[word] |= bit;
            }
            seen_words.iter().map(|w| w.count_ones()).sum()
        }
    }
}

/// The third option, and the interesting one: the body is written **once**, but `HAS_PLANE` is a
/// const generic, so each instantiation folds the plane test away at compile time. That is what the
/// hand-written split achieves by duplicating the source — here the compiler duplicates it instead,
/// from one copy the reader maintains. Callers dispatch once per query, not per printing.
#[allow(clippy::too_many_arguments)]
#[allow(clippy::needless_range_loop)]
#[inline(always)]
fn count_generic<const HAS_PLANE: bool>(
    card: &AOracleCard,
    cid: u32,
    printings: &[APrinting],
    artwork_group_col: &Archived<Vec<u16>>,
    start: usize,
    end: usize,
    all_match: bool,
    residual: &[&FilterExpr],
    residual_is_or: bool,
    mode: Mode,
    strings: &AStrings,
    existential_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)>,
    seen_words: &mut [u64; ARTWORK_GROUP_WORDS],
) -> u32 {
    let satisfies = |pid: usize| {
        (!HAS_PLANE
            || {
                let (pe, planes) = existential_plane.expect("HAS_PLANE implies Some");
                super::eval_plane_expr_for_printing(pe, planes, cid, &printings[pid], strings)
            })
            && (all_match || FilterExpr::residual_matches(card, &printings[pid], strings, residual, residual_is_or))
    };
    // Folds to `all_match` in the false instantiation and to `false` in the true one.
    let blind = all_match && !HAS_PLANE;
    match mode {
        Mode::Card => {
            if blind {
                return u32::from(start < end);
            }
            for pid in start..end {
                if satisfies(pid) {
                    return 1;
                }
            }
            0
        }
        Mode::Printing => {
            if blind {
                return (end - start) as u32;
            }
            let mut n = 0u32;
            for pid in start..end {
                if satisfies(pid) {
                    n += 1;
                }
            }
            n
        }
        Mode::Artwork => {
            seen_words.fill(0);
            for pid in start..end {
                let gid = u16::from(artwork_group_col[pid]) as usize;
                let (word, bit) = (gid / 64, 1u64 << (gid % 64));
                if seen_words[word] & bit != 0 || !satisfies(pid) {
                    continue;
                }
                seen_words[word] |= bit;
            }
            seen_words.iter().map(|w| w.count_ones()).sum()
        }
    }
}

/// `count_generic`, but iterating `&printings[start..end]` instead of indexing `printings[pid]`.
/// Isolates how much of the split's advantage is the *loop form* rather than the plane branch:
/// only `Mode::Artwork` genuinely needs the index (for `artwork_group_col`), and it keeps one.
#[allow(clippy::too_many_arguments)]
#[allow(clippy::needless_range_loop)]
#[inline(always)]
fn count_generic_iter<const HAS_PLANE: bool>(
    card: &AOracleCard,
    cid: u32,
    printings: &[APrinting],
    artwork_group_col: &Archived<Vec<u16>>,
    start: usize,
    end: usize,
    all_match: bool,
    residual: &[&FilterExpr],
    residual_is_or: bool,
    mode: Mode,
    strings: &AStrings,
    existential_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)>,
    seen_words: &mut [u64; ARTWORK_GROUP_WORDS],
) -> u32 {
    let satisfies = |p: &APrinting| {
        (!HAS_PLANE
            || {
                let (pe, planes) = existential_plane.expect("HAS_PLANE implies Some");
                super::eval_plane_expr_for_printing(pe, planes, cid, p, strings)
            })
            && (all_match || FilterExpr::residual_matches(card, p, strings, residual, residual_is_or))
    };
    let blind = all_match && !HAS_PLANE;
    match mode {
        Mode::Card => {
            if blind {
                return u32::from(start < end);
            }
            for p in &printings[start..end] {
                if satisfies(p) {
                    return 1;
                }
            }
            0
        }
        Mode::Printing => {
            if blind {
                return (end - start) as u32;
            }
            let mut n = 0u32;
            for p in &printings[start..end] {
                if satisfies(p) {
                    n += 1;
                }
            }
            n
        }
        Mode::Artwork => {
            seen_words.fill(0);
            for pid in start..end {
                let gid = u16::from(artwork_group_col[pid]) as usize;
                let (word, bit) = (gid / 64, 1u64 << (gid % 64));
                if seen_words[word] & bit != 0 || !satisfies(&printings[pid]) {
                    continue;
                }
                seen_words[word] |= bit;
            }
            seen_words.iter().map(|w| w.count_ones()).sum()
        }
    }
}

fn best_ns(mut kernel: impl FnMut() -> u64) -> (u128, u64) {
    let mut best = u128::MAX;
    let mut out = 0;
    for _ in 0..ITERS {
        let t0 = Instant::now();
        out = black_box(kernel());
        best = best.min(t0.elapsed().as_nanos());
    }
    (best, out)
}

#[test]
#[ignore = "micro-benchmark; needs benchmarks/verify-order/real.store (see module docs)"]
fn bench_card_match_unify() {
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
    println!("\n{} printings, {} cards from {STORE_PATH}", printings.len(), cards.len());

    // `banned:modern`'s shape is (all_match=true, residual=[]) — the legality planes make the
    // narrowing tight, so every candidate takes the blind shortcut. The residual case is the
    // other half of the space, where the closure is actually called per printing.
    let cmc = FilterExpr::NumericCmp { lhs: NumExpr::Field(NumField::Cmc), op: CmpOp::Lt, rhs: NumExpr::Const(3.0) };
    let residual_leaf: [&FilterExpr; 1] = [&cmc];

    // Full candidate set: every card, exactly the "full-candidate-set scan" the claim names.
    //
    // `existential_plane` and `all_match` go through `black_box` before the loop. This is what
    // makes the comparison faithful rather than flattering: passing a literal `None` would let
    // the optimizer constant-fold away exactly the per-printing branch the unified version adds,
    // and measure a version that does not exist. In `run_query` both are computed once per query
    // and are opaque runtime values at the call site, which is what this reproduces — the branch
    // is loop-invariant and perfectly predicted, but it is really there.
    let sweep_all = |mode: Mode, all_match: bool, residual: &[&FilterExpr], variant: Variant| -> u64 {
        let no_plane: Option<(&PlaneExpr, &Archived<BitPlanes>)> = black_box(None);
        let all_match = black_box(all_match);
        let mut seen = [0u64; ARTWORK_GROUP_WORDS];
        let mut acc = 0u64;
        for cid in 0..cards.len() {
            let card = &cards[cid];
            let start = u32::from(offsets[cid]) as usize;
            let end = u32::from(offsets[cid + 1]) as usize;
            let n = match variant {
                // `SplitControl` is `Split`, measured separately to expose the noise floor.
                Variant::Split | Variant::SplitControl => count_split(
                    card, cid as u32, printings, artwork_group_col, start, end, all_match, residual, false, mode, strings, no_plane, &mut seen,
                ),
                Variant::Unified => count_unified(
                    card, cid as u32, printings, artwork_group_col, start, end, all_match, residual, false, mode, strings, no_plane, &mut seen,
                ),
                // The dispatch a real caller would do: once per query, on the `Option`, not per
                // printing. `false` here because this sweep is the no-plane case.
                Variant::Generic => count_generic::<false>(
                    card, cid as u32, printings, artwork_group_col, start, end, all_match, residual, false, mode, strings, no_plane, &mut seen,
                ),
                Variant::GenericIter => count_generic_iter::<false>(
                    card, cid as u32, printings, artwork_group_col, start, end, all_match, residual, false, mode, strings, no_plane, &mut seen,
                ),
            };
            acc += u64::from(n);
        }
        acc
    };

    println!("\n  {:<38} {:>10} {:>9} {:>9} {:>11} {:>9} {:>9}", "case (existential_plane = None)", "split ns", "unif/spl", "gen/spl", "gen+it/spl", "FLOOR", "rows");
    let cases: &[(&str, Mode, bool, &[&FilterExpr])] = &[
        ("Card,    all_match (banned:modern)", Mode::Card, true, &[]),
        ("Printing, all_match", Mode::Printing, true, &[]),
        ("Artwork,  all_match", Mode::Artwork, true, &[]),
        ("Card,    residual cmc<3", Mode::Card, false, &residual_leaf),
        ("Printing, residual cmc<3", Mode::Printing, false, &residual_leaf),
        ("Artwork,  residual cmc<3", Mode::Artwork, false, &residual_leaf),
    ];
    for &(label, mode, all_match, residual) in cases {
        // Agreement before timing: a dedupe that changes an answer is not a dedupe.
        let a = sweep_all(mode, all_match, residual, Variant::Split);
        let b = sweep_all(mode, all_match, residual, Variant::Unified);
        let c = sweep_all(mode, all_match, residual, Variant::Generic);
        let d = sweep_all(mode, all_match, residual, Variant::GenericIter);
        assert_eq!(a, d, "{label}: const-generic+iter disagrees with split ({a} vs {d})");
        assert_eq!(a, b, "{label}: split and unified disagree ({a} vs {b})");
        assert_eq!(a, c, "{label}: split and const-generic disagree ({a} vs {c})");

        // Each variant timed twice, in both relative orders. Whichever contender runs second
        // inherits the other's cache and frequency state, so a one-order measurement cannot tell
        // "slower function" apart from "ran second" — and at these magnitudes that confound is the
        // same size as the effect. Best-over-both-orders removes it.
        let mut best = [u128::MAX; 5];
        let mut rows = 0;
        for pass in 0..2 {
            for i in 0..5 {
                let all = [Variant::Split, Variant::Unified, Variant::Generic, Variant::GenericIter, Variant::SplitControl];
                let v = all[if pass == 0 { i } else { 4 - i }];
                let (ns, r) = best_ns(|| sweep_all(mode, all_match, residual, v));
                let slot = match v {
                    Variant::Split => 0,
                    Variant::Unified => 1,
                    Variant::Generic => 2,
                    Variant::GenericIter => 3,
                    Variant::SplitControl => 4,
                };
                best[slot] = best[slot].min(ns);
                rows = r;
            }
        }
        println!(
            "  {label:<38} {:>10} {:>8.3}x {:>8.3}x {:>10.3}x {:>8.3}x {rows:>9}",
            best[0],
            best[1] as f64 / best[0] as f64,
            best[2] as f64 / best[0] as f64,
            best[3] as f64 / best[0] as f64,
            best[4] as f64 / best[0] as f64,
        );
    }
    println!("\n  ratio > 1 means slower than the shipped split. The claim under test is ~1.15x.");
    println!("  Each variant is timed in both relative orders and the best taken, so a ratio here is");
    println!("  the function's cost and not an artifact of which one ran second.");
}
