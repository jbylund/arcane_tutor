//! Micro-benchmarks for the three narrowing allocations of items 15 and 16 in
//! docs/issues/00799-engine-simplicity-pass.md. Each is a same-output rewrite, so every
//! contender is asserted result-identical before anything is timed.
//!
//! **A. `and_all`'s bitmap seed (item 15).** `bit_sets.split_first().map(|(first, rest)| {
//! let mut acc = first.clone(); … })` clones a whole bitmap out of an owned
//! `Vec<Vec<u64>>`. `into_iter()` takes it instead. One allocation plus one memcpy of
//! `words_per_plane` words, against a chain that then does `(k-1) × words` of AND — so the
//! expected win is real but bounded, and shrinks as `k` grows.
//!
//! **B. The oracle-word sparse expansion (item 16).** For a needle matching `w` sparse
//! dictionary words, `sparse_text_ids` folds `union_sorted` once per word, rebuilding the
//! accumulator every time — quadratic in `w`. Concatenating then `sort_unstable` + `dedup`
//! is linearithmic, and item 16 proposed it as a straight improvement. It is not: this
//! section is why the fold still ships. The fold wins by 2-5x for `w` ≤ 7, which is 98.8%
//! of the needle population (the sweep below measures that distribution), and sort+dedup
//! only pulls ahead past `w` ≈ 24. Both contenders and the crossover are kept here rather
//! than deleted, because a threshold could serve both regimes if the needle mix ever shifts
//! — docs/issues/local-engine-sparse-union-threshold.md.
//!
//! **C. The regex-factor fold (item 16).** `acc.map_or(cand.clone(), |prev|
//! intersect_sorted(&prev, &cand))`. Note this is worse than "clones on the first factor":
//! `map_or` takes its default **by value**, so `cand.clone()` is evaluated on every factor
//! and thrown away on all but the first. A `match` clones on none of them. Driven by
//! trigram candidate sets for the literal factors of real regexes.
//!
//!     cargo test --release bench_narrow_alloc -- --ignored --nocapture
//!
//! B and C need benchmarks/verify-order/real.store (see bench_verify_cost.rs's module doc
//! for the one-time build command); A is synthetic and always runs.

use std::hint::black_box;
use std::time::Instant;

use rkyv::Archived;

use rand::RngExt;

use super::{
    and_bits_into, archive_header, archive_payload, intersect_sorted, regex_required_factors, scan_oracle_words, trigram_candidates,
    union_sorted, CardData, Mmap, OracleWordIndex, ARCHIVE_HEADER_LEN,
};

/// Real corpus dimensions (blue, 2026-07).
const N_CARDS: usize = 31_508;
const N_PRINTINGS: usize = 97_206;
/// Best-of rounds per contender, matching `bench_intersect_order`.
const ITERS: usize = 300;
/// Inputs consumed inside one timed window, for the consuming kernels (see `time_ns_owned`).
/// Kept small so the pool stays cache-resident: 32 printing-space bitmap chains of k=6 is
/// ~2.3 MB, already past L2 on this machine, and larger pools only measure the memory system.
const POOL: usize = 32;
/// Calls inside one timed window, for the borrowing kernels (see `time_ns`).
const REPS: usize = 200;
const STORE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../benchmarks/verify-order/real.store");

/// Best-of timing for a kernel that **consumes** its input — which is the whole point here:
/// one contender takes ownership where the other clones. The input therefore cannot be
/// reused across calls, and rebuilding it costs more than the kernel does, so a pool of
/// `POOL` inputs is built outside the timed window and one timed window drains it. Times
/// reported are per call, `best / POOL`. Both contenders drain an identically-shaped pool,
/// so whatever cache pressure the pool adds, it adds to both.
fn time_ns_owned<I, O>(mut make: impl FnMut() -> I, mut kernel: impl FnMut(I) -> O) -> f64 {
    let mut best = u128::MAX;
    for _ in 0..ITERS {
        let pool: Vec<I> = (0..POOL).map(|_| make()).collect();
        let t0 = Instant::now();
        for input in pool {
            black_box(kernel(black_box(input)));
        }
        best = best.min(t0.elapsed().as_nanos());
    }
    best as f64 / POOL as f64
}

/// Best-of timing for a kernel that only borrows. `REPS` calls per timed window, for the
/// same reason `POOL` exists above: `Instant` quantizes to ~41.7 ns on this machine, the
/// same order as the smallest kernels here. Times reported are per call.
fn time_ns(mut kernel: impl FnMut() -> usize) -> f64 {
    let mut best = u128::MAX;
    let mut out = 0;
    for _ in 0..ITERS {
        let t0 = Instant::now();
        for _ in 0..REPS {
            out = black_box(kernel());
        }
        best = best.min(t0.elapsed().as_nanos());
    }
    black_box(out);
    best as f64 / REPS as f64
}

// ---------------------------------------------------------------- A: and_all bitmap seed

/// What `and_all` did: borrow the first bitmap and clone it as the accumulator.
fn bits_chain_clone(bit_sets: Vec<Vec<u64>>) -> Option<Vec<u64>> {
    bit_sets.split_first().map(|(first, rest)| {
        let mut acc = first.clone();
        for b in rest {
            and_bits_into(&mut acc, b);
        }
        acc
    })
}

/// What it does now: take the first bitmap by value.
fn bits_chain_into_iter(bit_sets: Vec<Vec<u64>>) -> Option<Vec<u64>> {
    let mut it = bit_sets.into_iter();
    it.next().map(|mut acc| {
        for b in it {
            and_bits_into(&mut acc, &b);
        }
        acc
    })
}

fn random_bits(rng: &mut rand::rngs::SmallRng, domain: usize, fill: f64) -> Vec<u64> {
    let mut bits = vec![0u64; domain.div_ceil(64)];
    for _ in 0..(domain as f64 * fill) as usize {
        let id = rng.random::<u64>() as usize % domain;
        bits[id >> 6] |= 1u64 << (id & 63);
    }
    bits
}

fn bench_and_all_seed() {
    let mut rng: rand::rngs::SmallRng = rand::make_rng();
    println!("\nA. and_all's bitmap seed — clone vs into_iter (ITERS={ITERS} best-of)");
    println!("{:>16} {:>4} {:>13} {:>13} {:>9}", "domain", "k", "clone", "into_iter", "speedup");

    for &(space, domain) in &[("card", N_CARDS), ("printing", N_PRINTINGS)] {
        for k in [2usize, 3, 4, 6] {
            let base: Vec<Vec<u64>> = (0..k).map(|_| random_bits(&mut rng, domain, 0.2)).collect();

            let expect = bits_chain_clone(base.clone());
            assert_eq!(expect, bits_chain_into_iter(base.clone()), "{space} k={k}: contenders disagree");

            let clone = time_ns_owned(|| base.clone(), bits_chain_clone);
            let into_iter = time_ns_owned(|| base.clone(), bits_chain_into_iter);
            let words = domain.div_ceil(64);
            println!("{:>10} ({words:>4}w) {k:>4} {clone:>10.0} ns {into_iter:>10.0} ns {:>8.2}x", space, clone / into_iter);
        }
    }
}

// ------------------------------------------------------- B: oracle-word sparse expansion

/// The old fold: `union_sorted` once per matched dictionary word.
fn sparse_ids_fold(words: &Archived<OracleWordIndex>, sparse: &[u32]) -> Vec<u32> {
    let mut ids: Vec<u32> = Vec::new();
    for &s in sparse {
        let start = u32::from(words.sparse_offsets[s as usize]) as usize;
        let end = u32::from(words.sparse_offsets[s as usize + 1]) as usize;
        let row: Vec<u32> = words.sparse_postings[start..end].iter().map(|x| u32::from(u16::from(*x))).collect();
        ids = union_sorted(ids, row);
    }
    ids
}

/// The replacement: concatenate, then sort and dedup once.
fn sparse_ids_sort(words: &Archived<OracleWordIndex>, sparse: &[u32]) -> Vec<u32> {
    let mut ids: Vec<u32> = Vec::new();
    for &s in sparse {
        let start = u32::from(words.sparse_offsets[s as usize]) as usize;
        let end = u32::from(words.sparse_offsets[s as usize + 1]) as usize;
        ids.extend(words.sparse_postings[start..end].iter().map(|x| u32::from(u16::from(*x))));
    }
    ids.sort_unstable();
    ids.dedup();
    ids
}

/// Needles spanning the real distribution of "how many sparse dictionary words does this
/// hit": a bare handful (`hexproof`), a few dozen (`sacrifice`, `equip`), and the long tail
/// of a short common fragment that appears inside many longer words (`counter`, `creature`).
/// Only sparse-tier hits matter here — a needle whose matches are all dense skips this code
/// path entirely, so those are reported with w=0 and excluded from the comparison.
const NEEDLES: &[&str] = &["hexproof", "sacrifice", "equip", "counter", "creature", "damage", "target", "land", "enchant"];

/// Total sparse postings behind a set of matched dictionary words — the input size the fold
/// is quadratic in.
fn sparse_posting_count(words: &Archived<OracleWordIndex>, sparse: &[u32]) -> usize {
    sparse
        .iter()
        .map(|&s| {
            let start = u32::from(words.sparse_offsets[s as usize]) as usize;
            let end = u32::from(words.sparse_offsets[s as usize + 1]) as usize;
            end - start
        })
        .sum()
}

/// How bad can the fold's quadratic term get on this corpus? Every eligible dictionary word
/// is itself a legal needle (`o:sacrifice` is the ordinary way to reach this path), and a
/// needle that is a substring of many dictionary words matches at least as many words as any
/// of them do. So sweeping the dictionary bounds the realistic `w` from below, and the top
/// of that sweep is the case worth timing.
fn worst_case_needles(words: &Archived<OracleWordIndex>, n: usize) -> Vec<String> {
    let mut ranked: Vec<(usize, usize, String)> = words
        .sparse_words
        .iter()
        .filter(|w| w.len() > 3)
        .map(|w| {
            let needle = w.as_str().to_string();
            let scan = scan_oracle_words(words, &needle);
            (scan.sparse.len(), sparse_posting_count(words, &scan.sparse), needle)
        })
        .collect();
    // Both tails matter: total postings is the fold's quadratic *size* term, matched words is
    // its *step count*, and the two do not peak on the same needles.
    ranked.sort_unstable_by_key(|r| std::cmp::Reverse(r.1));
    let by_postings: Vec<String> = ranked.iter().take(n).map(|r| r.2.clone()).collect();
    ranked.sort_unstable_by_key(|r| std::cmp::Reverse(r.0));
    println!(
        "  dictionary sweep: {} eligible sparse words; worst needle by words matched = {} ({} words), by postings = {}",
        ranked.len(),
        ranked.first().map_or("", |r| r.2.as_str()),
        ranked.first().map_or(0, |r| r.0),
        by_postings.first().map_or("", String::as_str),
    );
    // Which regime a needle lands in is what decides the two contenders, so report how the
    // needle population is distributed across it.
    let buckets = [1usize, 2, 4, 8, 16, 32];
    let hist: Vec<String> = buckets
        .iter()
        .enumerate()
        .map(|(i, &lo)| {
            let hi = buckets.get(i + 1).copied().unwrap_or(usize::MAX);
            let n = ranked.iter().filter(|r| r.0 >= lo && r.0 < hi).count();
            format!("{lo}-{}: {n} ({:.1}%)", if hi == usize::MAX { "max".to_string() } else { (hi - 1).to_string() }, 100.0 * n as f64 / ranked.len() as f64)
        })
        .collect();
    println!("  words matched per needle — {}", hist.join(", "));
    let mut out = by_postings;
    out.extend(ranked.iter().take(n).map(|r| r.2.clone()));
    out.sort_unstable();
    out.dedup();
    out.retain(|w| !NEEDLES.contains(&w.as_str()));
    out
}

fn bench_sparse_expansion(data: &Archived<CardData>) {
    let words = &data.indexes.oracle_trigram.words;
    println!("\nB. oracle-word sparse expansion — union_sorted fold vs sort+dedup (ITERS={ITERS} best-of)");
    let worst = worst_case_needles(words, 5);
    let needles: Vec<&str> = NEEDLES.iter().copied().chain(worst.iter().map(String::as_str)).collect();
    println!("{:>12} {:>6} {:>10} {:>7} {:>13} {:>13} {:>9}", "needle", "words", "postings", "ids", "fold", "sort+dedup", "speedup");

    for needle in needles {
        let scan = scan_oracle_words(words, needle);
        let sparse = scan.sparse;
        let postings = sparse_posting_count(words, &sparse);

        let expect = sparse_ids_fold(words, &sparse);
        assert_eq!(expect, sparse_ids_sort(words, &sparse), "{needle}: contenders disagree");
        if sparse.is_empty() {
            println!("{needle:>12} {:>6} {postings:>10} {:>7}   (all-dense or absent — this path not taken)", 0, 0);
            continue;
        }

        let fold = time_ns(|| sparse_ids_fold(words, &sparse).len());
        let sorted = time_ns(|| sparse_ids_sort(words, &sparse).len());
        println!(
            "{needle:>12} {:>6} {postings:>10} {:>7} {fold:>10.0} ns {sorted:>10.0} ns {:>8.2}x",
            sparse.len(),
            expect.len(),
            fold / sorted
        );
    }
}

// ------------------------------------------------------------------- C: regex-factor fold

/// The old fold. `map_or`'s default is evaluated eagerly, so the clone happens on **every**
/// factor, not only the first.
fn factors_map_or(cands: Vec<Vec<u32>>) -> Option<Vec<u32>> {
    let mut acc: Option<Vec<u32>> = None;
    for cand in cands {
        acc = Some(acc.map_or(cand.clone(), |prev| intersect_sorted(&prev, &cand)));
    }
    acc
}

/// The replacement: the first factor's candidates are moved into the accumulator.
fn factors_match(cands: Vec<Vec<u32>>) -> Option<Vec<u32>> {
    let mut acc: Option<Vec<u32>> = None;
    for cand in cands {
        acc = Some(match acc {
            None => cand,
            Some(prev) => intersect_sorted(&prev, &cand),
        });
    }
    acc
}

/// Real regexes over the two fields that carry trigram indexes, chosen for factor counts of
/// one (where the whole fold *is* the clone) through three.
const REGEXES: &[(&str, bool)] = &[
    (r"destroy target creature", false),
    (r"draw (a|two) cards?", false),
    (r"counter target spell", false),
    (r"whenever .* enters the battlefield", false),
    (r"goblin", true),
    (r"^ancient .*dragon$", true),
];

fn bench_regex_factors(data: &Archived<CardData>) {
    println!("\nC. regex-factor fold — map_or(cand.clone()) vs match (ITERS={ITERS} best-of)");
    println!("{:>34} {:>3} {:>7} {:>13} {:>13} {:>9}", "regex", "f", "ids", "map_or", "match", "speedup");

    for &(pattern, is_name) in REGEXES {
        let factors = regex_required_factors(pattern);
        let cands: Vec<Vec<u32>> = factors
            .iter()
            .filter_map(|f| {
                if is_name {
                    trigram_candidates(&data.indexes.name_trigram, f)
                } else {
                    trigram_candidates(&data.indexes.oracle_trigram.trigrams, f)
                }
            })
            .collect();
        if cands.is_empty() {
            println!("{pattern:>34} {:>3}   (no trigram-indexable factor — full scan path)", factors.len());
            continue;
        }

        let expect = factors_map_or(cands.clone());
        assert_eq!(expect, factors_match(cands.clone()), "{pattern}: contenders disagree");

        let map_or = time_ns_owned(|| cands.clone(), factors_map_or);
        let matched = time_ns_owned(|| cands.clone(), factors_match);
        println!(
            "{pattern:>34} {:>3} {:>7} {map_or:>10.0} ns {matched:>10.0} ns {:>8.2}x",
            cands.len(),
            expect.as_ref().map_or(0, Vec::len),
            map_or / matched
        );
    }
}

#[test]
#[ignore = "micro-benchmark; B and C need benchmarks/verify-order/real.store (see module docs)"]
fn bench_narrow_alloc() {
    bench_and_all_seed();

    let Ok(file) = std::fs::File::open(STORE_PATH) else {
        eprintln!("SKIP B and C: {STORE_PATH} not found (see module docs)");
        return;
    };
    // Safety: same contract as get_mmap() in lib.rs — re-validated against the header below.
    let mmap = unsafe { Mmap::map(&file) }.expect("mmap real.store");
    if mmap.len() < ARCHIVE_HEADER_LEN || mmap[..ARCHIVE_HEADER_LEN] != archive_header() {
        eprintln!("SKIP B and C: {STORE_PATH} header mismatch (stale archive — rebuild it, see module docs)");
        return;
    }
    let data = unsafe { rkyv::access_unchecked::<Archived<CardData>>(archive_payload(&mmap)) };

    bench_sparse_expansion(data);
    bench_regex_factors(data);
}
