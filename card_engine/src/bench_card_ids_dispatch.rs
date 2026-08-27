//! Micro-benchmark for perf-audit finding #1: `PreparedCandidates::card_ids` returned
//! `Box<dyn ExactSizeIterator<Item = u32>>`, so every `.next()` in P3's and P4's per-card match
//! loops (`run_query_streamed`, `exec_gathered_scan` — up to `n_cards` iterations each) went
//! through a vtable instead of a monomorphized call the compiler can inline.
//!
//! `bench_iter_dispatch.rs` measured the same class of overhead for `run_query`'s card_ids
//! before the `PreparedCandidates` split; that call site has since moved (this module targets
//! the current one) and its benchmark needs a corpus store this repo doesn't ship, so this one
//! is self-contained — synthetic ids, no archive.
//!
//! **First attempt at this benchmark was wrong.** Constructing the `Box<dyn ...>` and consuming
//! it in the same closure let LLVM see straight through the allocation and devirtualize both
//! arms identically -- the "boxed" arm came back tied with, and sometimes faster than, the
//! concrete one, which is not physically possible (the boxed arm does strictly more work: a heap
//! allocation plus indirect calls). In production the box crosses a real function boundary
//! (`prep.card_ids(ctx)` is built in one function, `run_query_streamed`/`exec_gathered_scan`
//! consume it in another, neither trivially inlined into the other), so this version forces the
//! same shape with `#[inline(never)]` consumer functions that take the iterator as an opaque
//! parameter, matching bench_iter_dispatch's cross-function pattern.
//!
//! Two shapes, matching the two arms of `CardIdIter`: a narrowed `List` (the common case once a
//! query narrows) and a full-corpus `Range` (the `None` narrowing arm).
//!
//!     cargo test --release bench_card_ids_dispatch -- --ignored --nocapture

use std::hint::black_box;
use std::time::Instant;

use super::CardIdIter;

const ITERS: usize = 300;
const N: u32 = 31_508; // production corpus card count, per other bench modules' comments

#[inline(never)]
fn consume_boxed(it: Box<dyn ExactSizeIterator<Item = u32> + '_>) -> u64 {
    let mut acc = 0u64;
    for cid in it {
        acc = acc.wrapping_add(u64::from(black_box(cid)));
    }
    acc
}

#[inline(never)]
fn consume_concrete(it: CardIdIter<'_>) -> u64 {
    let mut acc = 0u64;
    for cid in it {
        acc = acc.wrapping_add(u64::from(black_box(cid)));
    }
    acc
}

fn time_ns(mut kernel: impl FnMut() -> u64) -> f64 {
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
fn bench_card_ids_dispatch() {
    let ids: Vec<u32> = (0..N).collect();

    // ─── List shape (narrowed candidates) ───
    let boxed_list = time_ns(|| consume_boxed(Box::new(black_box(ids.iter().copied()))));
    let concrete_list = time_ns(|| consume_concrete(CardIdIter::List(black_box(ids.iter().copied()))));
    println!(
        "List   (narrowed):   boxed={boxed_list:>9.0} ns   concrete={concrete_list:>9.0} ns   ratio={:.2}x   overhead={:.3} ns/card",
        boxed_list / concrete_list,
        (boxed_list - concrete_list) / f64::from(N),
    );

    // ─── Range shape (unnarrowed: every card) ───
    let boxed_range = time_ns(|| consume_boxed(Box::new(black_box(0..N))));
    let concrete_range = time_ns(|| consume_concrete(CardIdIter::Range(black_box(0..N))));
    println!(
        "Range  (unnarrowed): boxed={boxed_range:>9.0} ns   concrete={concrete_range:>9.0} ns   ratio={:.2}x   overhead={:.3} ns/card",
        boxed_range / concrete_range,
        (boxed_range - concrete_range) / f64::from(N),
    );
}
