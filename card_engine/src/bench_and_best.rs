//! Micro-benchmark for the And arm's `best` bookkeeping — item 14 of
//! docs/issues/00799-engine-simplicity-pass.md.
//!
//! `narrow_rec`'s And arm reads `best` (the smallest set accumulated so far) at the top
//! of every child iteration, to decide whether the driver is already selective enough to
//! skip a costlier child. It used to recompute that from scratch:
//!
//! ```ignore
//! let best = card_sets.iter().chain(printing_sets.iter()).map(|n| n.set.len()).min();
//! ```
//!
//! `Candidates::len()` is O(1) for the vec variants but popcounts the **whole** bitmap for
//! `CardBits`/`PrintingBits`, so with `c` accumulated bitmap children the loop is
//! O(c² × words) for a value that only ever shrinks. The fix maintains the running minimum
//! as sets are pushed, reusing the length the broad-child check (`len > domain - domain/4`)
//! already computes — one popcount per child instead of `c²/2` of them.
//!
//! Both contenders below pay that broad-child `len()` call, because both variants of the
//! shipped code do; the only difference is the recomputed `min` over the accumulated sets.
//! They are asserted to agree on the final value before either is timed.
//!
//! What to expect: nothing at all for vec-shaped children (`len()` is `Vec::len`), and a
//! cost that grows quadratically in the number of *bitmap* children. Real Ands are 2-6
//! children wide, so this is a small absolute number — a bitmap popcount over the printing
//! space is ~1,520 words. The configs below bracket the realistic range and go a little
//! past it to show the shape.
//!
//! Synthetic bitmaps over the real corpus dimensions (31,508 cards / 97,206 printings):
//! popcount cost depends on the word count, not on which bits are set, so there is nothing
//! a corpus-derived set would add.
//!
//!     cargo test --release bench_and_best -- --ignored --nocapture

use std::hint::black_box;
use std::time::Instant;

use rand::RngExt;

use crate::{Candidates, Narrowed};

/// Real corpus dimensions (blue, 2026-07): the domains the two bitmap variants are sized to.
const N_CARDS: usize = 31_508;
const N_PRINTINGS: usize = 97_206;
/// Best-of rounds per contender, matching `bench_intersect_order`.
const ITERS: usize = 300;
/// Calls inside one timed window. `Instant` on this machine quantizes to ~41.7 ns, which is
/// the same order as the whole kernel for the small configs — timing one call at a time
/// reports nothing but that quantum. Times below are per call, `best / REPS`.
const REPS: usize = 200;
/// Child counts to sweep. Real Ands are 2-6 children; 8 and 12 show the curve's shape.
const CHILD_COUNTS: &[usize] = &[2, 3, 4, 6, 8, 12];

fn time_ns(sets: &[Narrowed], kernel: impl Fn(&[Narrowed]) -> usize) -> f64 {
    let mut best = u128::MAX;
    let mut out = 0;
    for _ in 0..ITERS {
        let t0 = Instant::now();
        for _ in 0..REPS {
            // Opaque input per call, so the repetition loop cannot be hoisted out of.
            out = black_box(kernel(black_box(sets)));
        }
        best = best.min(t0.elapsed().as_nanos());
    }
    black_box(out);
    best as f64 / REPS as f64
}

/// The shipped-before order: `min` over every set accumulated so far, recomputed at the top
/// of each child iteration, plus the one `len()` the broad-child check pays after the child
/// narrows. `sets[..i]` stands in for the accumulated `card_sets`/`printing_sets` — the
/// chained iteration order does not matter to a `min`, and neither vec is mutated inside the
/// timed region, so this isolates the popcounts and nothing else.
fn best_recomputed(sets: &[Narrowed]) -> usize {
    let mut running = usize::MAX;
    for i in 0..sets.len() {
        let best = sets[..i].iter().map(|n| n.set.len()).min();
        black_box(best);
        let len = sets[i].set.len(); // the broad-child check, paid by both contenders
        running = best.unwrap_or(usize::MAX).min(len);
    }
    running
}

/// The shipped-after order: one `len()` per child, folded into a running minimum.
fn best_maintained(sets: &[Narrowed]) -> usize {
    let mut best: Option<usize> = None;
    for n in sets {
        black_box(best);
        let len = n.set.len();
        best = Some(best.map_or(len, |b| b.min(len)));
    }
    best.unwrap_or(usize::MAX)
}

/// A bitmap over `domain` bits with roughly `fill` of them set. Density is irrelevant to
/// popcount cost; it varies only so the `min` has something non-degenerate to choose from.
fn random_bits(rng: &mut rand::rngs::SmallRng, domain: usize, fill: f64) -> Vec<u64> {
    let mut bits = vec![0u64; domain.div_ceil(64)];
    let target = (domain as f64 * fill) as usize;
    for _ in 0..target {
        let id = rng.random::<u64>() as usize % domain;
        bits[id >> 6] |= 1u64 << (id & 63);
    }
    bits
}

fn random_sorted_list(rng: &mut rand::rngs::SmallRng, domain: usize, size: usize) -> Vec<u32> {
    let mut set = std::collections::HashSet::with_capacity(size);
    while set.len() < size {
        set.insert((rng.random::<u64>() as usize % domain) as u32);
    }
    let mut v: Vec<u32> = set.into_iter().collect();
    v.sort_unstable();
    v
}

/// Set shapes an And can accumulate. Each child's `len()` is O(words) for the two bitmap
/// shapes and O(1) for the vec ones, which is the entire spread this benchmark measures.
#[derive(Clone, Copy)]
enum Shape {
    CardBits,
    PrintingBits,
    /// Alternating bitmap / vec children — the common real mix, where a plane-backed child
    /// (legality, rarity, colors) sits next to a posting-backed one (a range or a text word).
    Mixed,
    Vecs,
}

fn build(rng: &mut rand::rngs::SmallRng, shape: Shape, k: usize) -> Vec<Narrowed> {
    (0..k)
        .map(|i| {
            // Descending fill so the running minimum actually moves as children are pushed,
            // which is the case the recomputed `min` is least favorable in (it never gets to
            // short-circuit — not that `min` does anyway, but it keeps the two honest).
            let fill = 0.3 / (i + 1) as f64;
            let set = match (shape, i % 2) {
                (Shape::CardBits, _) | (Shape::Mixed, 0) => Candidates::CardBits(random_bits(rng, N_CARDS, fill)),
                (Shape::PrintingBits, _) => Candidates::PrintingBits(random_bits(rng, N_PRINTINGS, fill)),
                (Shape::Vecs, _) | (Shape::Mixed, _) => {
                    Candidates::Cards(random_sorted_list(rng, N_CARDS, (N_CARDS as f64 * fill) as usize))
                }
            };
            Narrowed { set, tight: true }
        })
        .collect()
}

#[test]
#[ignore = "micro-benchmark; synthetic data, no external deps"]
fn bench_and_best() {
    let mut rng: rand::rngs::SmallRng = rand::make_rng();

    println!("\nN_CARDS={N_CARDS} ({} words), N_PRINTINGS={N_PRINTINGS} ({} words), ITERS={ITERS} (best-of)", N_CARDS.div_ceil(64), N_PRINTINGS.div_ceil(64));
    println!("{:>14} {:>4} {:>14} {:>14} {:>9}", "children", "k", "recomputed", "maintained", "speedup");

    for &(name, shape) in &[
        ("card bits", Shape::CardBits),
        ("printing bits", Shape::PrintingBits),
        ("mixed", Shape::Mixed),
        ("vecs", Shape::Vecs),
    ] {
        for &k in CHILD_COUNTS {
            let sets = build(&mut rng, shape, k);

            // Agreement before timing: both are the same `min`, computed in a different order.
            let expect = best_recomputed(&sets);
            assert_eq!(expect, best_maintained(&sets), "{name} k={k}: contenders disagree on best");

            let recomputed = time_ns(&sets, best_recomputed);
            let maintained = time_ns(&sets, best_maintained);
            println!("{name:>14} {k:>4} {recomputed:>11.1} ns {maintained:>11.1} ns {:>8.2}x", recomputed / maintained);
        }
    }
}
