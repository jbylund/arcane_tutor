//! Micro-benchmark for the operand *order* of a sorted-merge intersection chain —
//! item 12 of docs/issues/00799-engine-simplicity-pass.md.
//!
//! `and_all` and `intersect_operands` both sort their operands by ascending length,
//! then seed the working set with `swap_remove(0)`. That returns the shortest operand,
//! as intended, but moves the **longest** into slot 0, so the remaining chain runs
//! roughly longest-first. Both call sites carry a comment saying ascending.
//!
//! The result is identical either way (intersection is commutative and associative),
//! so this measures only the cost of the order. Two contenders:
//! - `chain_swap_remove`: reproduces the shipped order — `swap_remove(0)` for the seed,
//!   then iterate the mutated vec, which now begins with the longest operand.
//! - `chain_ascending`: `into_iter()`, so the chain stays ascending.
//!
//! What to expect, and why the honest answer is "bounded": each merge step costs
//! O(|result| + |operand|), and `sum(|operand|)` is order-invariant. Order only moves
//! the `sum(|result|)` term, and `|result|` starts at the shortest operand's length and
//! only shrinks. So the ceiling on the win is roughly `(k - 1) x |shortest|`, which is
//! small next to `sum(|operand|)` whenever the operands are much larger than the
//! shortest one. Configs below are chosen to bracket that: a wide spread (where the
//! shortest is a small share of the total) and a narrow one (where it is not).
//!
//! Synthetic sorted id lists over a 31,500-id universe — this corpus's card count —
//! following `bench_posting_intersect`'s reasoning: the question is a cost curve
//! across operand-size mixes, not the behavior of one specific query.
//!
//!     cargo test --release bench_intersect_order -- --ignored --nocapture

use std::hint::black_box;
use std::time::Instant;

use rand::RngExt;

use crate::intersect_sorted;

/// Card-space universe: this corpus's card count.
const UNIVERSE: usize = 31_500;
/// Best-of rounds per contender, matching `bench_posting_intersect`.
const ITERS: usize = 300;

fn time_ns(mut kernel: impl FnMut() -> Vec<u32>) -> f64 {
    let mut best = u128::MAX;
    let mut out = Vec::new();
    for _ in 0..ITERS {
        let t0 = Instant::now();
        out = black_box(kernel());
        best = best.min(t0.elapsed().as_nanos());
    }
    black_box(out.len());
    best as f64
}

fn random_sorted_list(rng: &mut rand::rngs::SmallRng, size: usize) -> Vec<u32> {
    let mut set = std::collections::HashSet::with_capacity(size);
    while set.len() < size {
        set.insert((rng.random::<u64>() as usize % UNIVERSE) as u32);
    }
    let mut v: Vec<u32> = set.into_iter().collect();
    v.sort_unstable();
    v
}

/// Merge `sorted[0]` against `sorted[i]` for each `i` in `order`, keeping the shipped
/// early exit. `sorted` is ascending by length, so `sorted[0]` is the seed either way —
/// only `order` differs between the contenders, which is the entire point: no operand is
/// cloned and no sort runs inside the timed region, so the measurement is the order and
/// nothing else.
fn chain(sorted: &[Vec<u32>], order: &[usize]) -> Vec<u32> {
    let mut result = sorted[0].clone();
    for &i in order {
        if result.is_empty() {
            break;
        }
        result = intersect_sorted(&result, &sorted[i]);
    }
    result
}

/// What `swap_remove(0)` leaves behind. It returns `sorted[0]` (the shortest — the seed
/// the comment intends) but moves `sorted[k - 1]` into slot 0, so the chain visits the
/// **longest** operand first and the genuinely ascending middle afterwards.
fn swap_remove_order(k: usize) -> Vec<usize> {
    let mut order = vec![k - 1];
    order.extend(1..k - 1);
    order
}

/// Ascending throughout: `1, 2, …, k - 1`.
fn ascending_order(k: usize) -> Vec<usize> {
    (1..k).collect()
}

#[test]
#[ignore = "micro-benchmark; synthetic data, no external deps"]
fn bench_intersect_order() {
    let mut rng: rand::rngs::SmallRng = rand::make_rng();

    // Operand-size mixes. The first group spreads sizes widely (the shortest is a
    // small share of the total, so order should barely matter); the last two keep the
    // operands close in size, where the moved `sum(|result|)` term is a larger share.
    let configs: &[(&str, &[usize])] = &[
        ("3 wide", &[200, 4_000, 20_000]),
        ("4 wide", &[200, 2_000, 8_000, 20_000]),
        ("6 wide", &[150, 800, 2_000, 6_000, 12_000, 24_000]),
        ("3 narrow", &[4_000, 5_000, 6_000]),
        ("6 narrow", &[4_000, 4_400, 4_800, 5_200, 5_600, 6_000]),
    ];

    println!("\nUNIVERSE={UNIVERSE}, ITERS={ITERS} (best-of), times in ns");
    println!("{:>10} {:>6} {:>14} {:>14} {:>9} {:>7}", "config", "k", "swap_remove", "ascending", "speedup", "rows");

    for &(name, sizes) in configs {
        let mut sorted: Vec<Vec<u32>> = sizes.iter().map(|&s| random_sorted_list(&mut rng, s)).collect();
        sorted.sort_unstable_by_key(Vec::len);
        let k = sorted.len();
        let (swap_ord, asc_ord) = (swap_remove_order(k), ascending_order(k));

        // Agreement before timing: the whole premise is that order cannot change the
        // result, so a divergence here means the benchmark is wrong, not the ordering.
        let expect = chain(&sorted, &swap_ord);
        assert_eq!(expect, chain(&sorted, &asc_ord), "{name}: operand order changed the result");

        let swap = time_ns(|| chain(&sorted, &swap_ord));
        let asc = time_ns(|| chain(&sorted, &asc_ord));
        println!("{:>10} {:>6} {:>14.0} {:>14.0} {:>8.2}x {:>7}", name, k, swap, asc, swap / asc, expect.len());
    }
}
