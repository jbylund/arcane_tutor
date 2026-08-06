//! What an O(1) printing-membership bit test actually costs, for
//! docs/issues/00856-engine-compose-membership-bittest.md.
//!
//! #856 replaces a per-printing residual evaluation with `bitmap_contains(pbits, pid)`. The residual
//! side is measured end to end at 5.8 ns/printing (`scripts/bench_membership_waste.py`); the bit-test
//! side was an ASSUMPTION of ~1 ns, and the whole 3.5x estimate rests on it, because the saving is
//! `residual_ns - bit_test_ns`. This measures it.
//!
//! Reproduces the real access pattern rather than a flat scan, because the pattern is what decides the
//! answer. `push_card_matches` / `card_match_count` walk CANDIDATE CARDS and bit-test each one's
//! contiguous printing span (`offsets[cid]..offsets[cid+1]`), so the bitmap is read in ascending runs
//! with gaps where non-candidate cards are skipped. Two axes matter and both are swept:
//!
//! - **candidate density** — what fraction of cards are candidates. Dense reads the bitmap
//!   sequentially; sparse strides it, touching more cache lines per useful test.
//! - **bitmap density** — what fraction of bits are set. This is a BRANCH-PREDICTION axis, not a
//!   memory one: at ~50% the `if` is unpredictable and a mispredict costs far more than the load.
//!   A number measured only at 0% or 100% density would be optimistic by exactly that amount.
//!
//! Corpus scale is swept 1x and 5x because the bitmap is 12 KB at production scale (97,206 printings,
//! 1,519 words) — comfortably L1-resident — and that stops being true as the corpus grows. A result
//! that only holds while the whole bitmap fits in L1 needs to say so.
//!
//! `walk_only` is the floor: identical loops, no bitmap access, so the difference isolates the test
//! from loop overhead. The inclusive `bit_test` column is the honest figure to compare against 5.8 ns,
//! since the residual figure also includes its loop.
//!
//!     cargo test --release bench_membership_bittest -- --ignored --nocapture

use std::hint::black_box;
use std::time::Instant;

use rand::RngExt;
use rand::SeedableRng as _;

use crate::planes::bitmap_contains;

/// Production corpus, from `benchmarks/bitplanes/corpus.jsonl`.
const N_CARDS_1X: usize = 31_508;
const N_PRINTINGS_1X: usize = 97_206;
/// Rounds per cell; min-of-N, matching every other bench here.
const ITERS: usize = 200;
/// Reciprocal of the candidate share. 1 = every card (sequential); 135 is the MEASURED production
/// value (234 candidate cards of 31,508 per query, realistic mode), which is the row to read.
const CANDIDATE_STRIDES: [usize; 6] = [1, 2, 4, 8, 32, 135];
/// Fraction of bits set, in percent. 9 is the MEASURED production density (matches/examined on the
/// #856 population, `scripts/bench_membership_waste.py`); 50 is the branch-prediction worst case.
const BITMAP_DENSITIES: [u32; 6] = [1, 9, 10, 50, 90, 99];

/// Card printing-span boundaries, `n_cards + 1` long, mirroring the store's `offsets`.
fn build_offsets(n_cards: usize, n_printings: usize) -> Vec<u32> {
    let mut offsets = Vec::with_capacity(n_cards + 1);
    // Spread printings across cards as evenly as the real ratio allows (~3.09/card). Exact per-card
    // counts do not matter here; span CONTIGUITY does, and that is structural.
    for i in 0..=n_cards {
        offsets.push((i * n_printings / n_cards) as u32);
    }
    offsets
}

fn build_bitmap(rng: &mut rand::rngs::SmallRng, n_printings: usize, density_pct: u32) -> Vec<u64> {
    let mut bits = vec![0u64; n_printings.div_ceil(64)];
    for pid in 0..n_printings {
        if rng.random::<u64>() % 100 < u64::from(density_pct) {
            bits[pid >> 6] |= 1u64 << (pid & 63);
        }
    }
    bits
}

/// Counting form: `if contains { hits += 1 }`.
///
/// **Do not read this as the answer.** The compiler predicates it into `hits += contains as u32` and
/// vectorizes, so it is branch-free and completely insensitive to bitmap density — which is how the
/// first version of this bench produced a flat 0.4 ns at every density and would have been believed.
/// Kept as the lower bound a *predicable* body could reach, and as the control that proves the branchy
/// number below is really measuring a branch.
fn bit_test_counting(bits: &[u64], offsets: &[u32], stride: usize) -> u32 {
    let mut hits = 0u32;
    let mut cid = 0;
    while cid + 1 < offsets.len() {
        let (start, end) = (offsets[cid] as usize, offsets[cid + 1] as usize);
        for pid in start..end {
            if bitmap_contains(bits, pid as u32) {
                hits += 1;
            }
        }
        cid += stride;
    }
    hits
}

/// The form the real loop takes: on a hit, append a row. `push_card_matches` does
/// `scratch.push((sort_key_bits(...), cid, pid))`, which is a conditional store the compiler cannot
/// predicate away — so this pays the branch, and the mispredict at ~50% density is a real cost the
/// counting form hides. `out` is pre-allocated and reused, so no allocation is timed.
fn bit_test_branchy(bits: &[u64], offsets: &[u32], stride: usize, out: &mut Vec<u32>) -> u32 {
    out.clear();
    let mut cid = 0;
    while cid + 1 < offsets.len() {
        let (start, end) = (offsets[cid] as usize, offsets[cid + 1] as usize);
        for pid in start..end {
            if bitmap_contains(bits, pid as u32) {
                out.push(pid as u32);
            }
        }
        cid += stride;
    }
    out.len() as u32
}

/// The same traversal with no bitmap access — loop overhead alone, so the difference is the test.
fn walk_only(offsets: &[u32], stride: usize) -> u32 {
    let mut acc = 0u32;
    let mut cid = 0;
    while cid + 1 < offsets.len() {
        let (start, end) = (offsets[cid] as usize, offsets[cid + 1] as usize);
        for pid in start..end {
            acc = acc.wrapping_add(pid as u32);
        }
        cid += stride;
    }
    acc
}

fn time_ns(mut kernel: impl FnMut() -> u32) -> f64 {
    let mut best = u128::MAX;
    let mut out = 0;
    for _ in 0..ITERS {
        let t0 = Instant::now();
        out = black_box(kernel());
        best = best.min(t0.elapsed().as_nanos());
    }
    black_box(out);
    best as f64
}

/// Printings actually visited at this stride — the denominator for a per-printing rate.
fn visited(offsets: &[u32], stride: usize) -> usize {
    let mut n = 0;
    let mut cid = 0;
    while cid + 1 < offsets.len() {
        n += (offsets[cid + 1] - offsets[cid]) as usize;
        cid += stride;
    }
    n
}

#[test]
#[ignore = "micro-benchmark; synthetic data, no external deps"]
fn bench_membership_bittest_cost() {
    let mut rng = rand::rngs::SmallRng::seed_from_u64(8_560_001);

    for scale in [1usize, 5] {
        let (n_cards, n_printings) = (N_CARDS_1X * scale, N_PRINTINGS_1X * scale);
        let offsets = build_offsets(n_cards, n_printings);
        let kb = n_printings.div_ceil(64) * 8 / 1024;
        println!("\n=== corpus {scale}x — {n_printings} printings, bitmap {kb} KB ===");
        println!(
            "{:>7}  {:>10}  {:>8}  {:>9}  {:>9}  {:>7}",
            "stride", "visited", "density", "BRANCHY", "counting", "walk"
        );

        let mut out: Vec<u32> = Vec::with_capacity(n_printings);
        for stride in CANDIDATE_STRIDES {
            let n = visited(&offsets, stride);
            let t_walk = time_ns(|| walk_only(&offsets, stride)) / n as f64;
            for density in BITMAP_DENSITIES {
                let bits = build_bitmap(&mut rng, n_printings, density);
                let t_branchy = time_ns(|| bit_test_branchy(&bits, &offsets, stride, &mut out)) / n as f64;
                let t_count = time_ns(|| bit_test_counting(&bits, &offsets, stride)) / n as f64;
                println!(
                    "{:>7}  {:>10}  {:>7}%  {:>9.2}  {:>9.2}  {:>7.2}",
                    stride, n, density, t_branchy, t_count, t_walk
                );
            }
        }
    }
    println!("\nns per printing tested, min of {ITERS} rounds.");
    println!("BRANCHY is the answer: it conditionally appends, as `push_card_matches` does, so it pays the");
    println!("branch. `counting` is predicated by the compiler and density-flat — the trap, not the result.");
    println!("Compare BRANCHY against the 5.8 ns/printing residual evaluation it would replace.");
}
