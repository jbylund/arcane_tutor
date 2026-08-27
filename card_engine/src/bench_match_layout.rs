//! Scoping probe for perf-audit finding #5, which shipped as a follow-up commit: `Match` used to
//! be `(u128, u32, u32)`, padded to 32 bytes (u128's 16-byte alignment), even though `page_cmp`
//! only ever read the top 64 bits of the key -- the low 32 (`sc`/prefer_score) were computed and
//! stored but never read by any comparator (verified two ways before removing it: a static trace
//! of every `Match` consumer, all of which go through `page_cmp`; then, because that trace turned
//! out to have a wrinkle worth not trusting blindly -- a test helper's doc comment claimed a full
//! 3-key comparison was "the exact comparator select_page uses" -- an empirical check: zeroing
//! the third key and running the full 160-test suite, which passed unchanged). `sort_key_bits`
//! now returns `u64` and `Match` is `(u64, u32, u32)`, 16 bytes with no padding.
//!
//! This measures whether that size halving actually moves the two operations
//! `select_page`/`prune_to_smallest` perform on `Vec<Match>` (`select_nth_unstable_by`,
//! `sort_unstable_by`) at the sizes they really run at -- run BEFORE the fix landed, against a
//! synthetic wide/narrow pair standing in for the old and new layouts, to decide whether the
//! fix was worth the risk in the first place; kept as a regression check.
//!
//!     cargo test --release bench_match_layout -- --ignored --nocapture

use std::hint::black_box;
use std::time::Instant;

use rand::rngs::SmallRng;
use rand::{RngExt, SeedableRng};

const ITERS: usize = 200;
// GATHER_PRUNE_CHUNK is 4096; a page-plus-chunk buffer at a typical page size is in this range.
const N: usize = 4096 + 175;
const K: usize = 175; // typical page size (limit), per other bench modules' comments

fn gen_wide(n: usize, seed: u64) -> Vec<(u128, u32, u32)> {
    let mut rng = SmallRng::seed_from_u64(seed);
    (0..n as u32).map(|i| (u128::from(rng.random::<u64>()), i / 8, i)).collect()
}

fn gen_narrow(wide: &[(u128, u32, u32)]) -> Vec<(u64, u32, u32)> {
    // Same (key, cid, pid) content, key truncated to the top 64 bits -- exactly what
    // sort_key_bits would produce directly instead of packing into a u128.
    wide.iter().map(|&(k, c, p)| ((k >> 64) as u64, c, p)).collect()
}

fn wide_cmp(a: &(u128, u32, u32), b: &(u128, u32, u32)) -> std::cmp::Ordering {
    (a.0 >> 64).cmp(&(b.0 >> 64)).then_with(|| a.1.cmp(&b.1)).then_with(|| a.2.cmp(&b.2))
}

fn narrow_cmp(a: &(u64, u32, u32), b: &(u64, u32, u32)) -> std::cmp::Ordering {
    a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)).then_with(|| a.2.cmp(&b.2))
}

fn time_ns(mut kernel: impl FnMut() -> usize) -> f64 {
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

#[test]
#[ignore]
fn bench_match_layout() {
    println!(
        "sizes: wide (u128,u32,u32) = {} B, narrow (u64,u32,u32) = {} B",
        std::mem::size_of::<(u128, u32, u32)>(),
        std::mem::size_of::<(u64, u32, u32)>(),
    );

    // Base data generated ONCE; each timed iteration clones it (the per-iteration cost a real
    // caller would also pay in some form — moving/copying elements — so it's fair to both
    // arms, and reflects the size difference the same way production swaps/memmoves would).
    let base_wide_n = gen_wide(N, 1);
    let base_narrow_n = gen_narrow(&base_wide_n);
    let base_wide_k = gen_wide(K, 2);
    let base_narrow_k = gen_narrow(&base_wide_k);

    // select_nth_unstable_by, as prune_to_smallest / select_page's first quickselect does.
    let wide_select = time_ns(|| {
        let mut v = base_wide_n.clone();
        v.select_nth_unstable_by(K, wide_cmp);
        v.len()
    });
    let narrow_select = time_ns(|| {
        let mut v = base_narrow_n.clone();
        v.select_nth_unstable_by(K, narrow_cmp);
        v.len()
    });
    println!(
        "select_nth_unstable_by(k={K}, n={N}): wide={wide_select:.0} ns   narrow={narrow_select:.0} ns   ratio={:.2}x",
        wide_select / narrow_select
    );

    // sort_unstable_by over the page slice, as select_page's final sort does.
    let wide_sort = time_ns(|| {
        let mut v = base_wide_k.clone();
        v.sort_unstable_by(wide_cmp);
        v.len()
    });
    let narrow_sort = time_ns(|| {
        let mut v = base_narrow_k.clone();
        v.sort_unstable_by(narrow_cmp);
        v.len()
    });
    println!(
        "sort_unstable_by(n={K}):               wide={wide_sort:.0} ns   narrow={narrow_sort:.0} ns   ratio={:.2}x",
        wide_sort / narrow_sort
    );
}
