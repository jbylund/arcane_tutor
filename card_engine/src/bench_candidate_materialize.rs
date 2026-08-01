//! Three ways to turn selected postings rows into one sorted candidate list, as a
//! function of how many candidates come back and how large the candidate space is.
//!
//! Context: `arith_tuple_narrow` (and any other arm that unions several postings
//! rows) has sorted rows but no globally sorted output, because combination order
//! is not card order. It currently concatenates the selected rows and sorts. Two
//! alternatives cost nothing extra in information: a k-way merge, since every run
//! is already sorted, and a bitmap scatter followed by reading the set bits back,
//! which is sorted by construction.
//!
//! The three have different cost shapes, which is why this sweeps two axes:
//!
//! - `concat+sort` — O(c log c) in the candidate count, no dependence on the domain.
//! - `merge` — O(c log k) in the count and the number of runs, no dependence on the
//!   domain.
//! - `bitmap+extract` — O(domain/64 + c): a fixed cost proportional to the *space*
//!   plus a linear pass, so it wins broad results and loses as the domain grows
//!   relative to the answer.
//!
//! Runs are disjoint (each card belongs to exactly one combination) and their ids
//! are scattered through the domain rather than contiguous, which is the real
//! situation: a combination's cards sit wherever they sit in store order.
//!
//! All three are asserted to produce the identical `Vec<u32>` before anything is
//! timed.
//!
//!     cargo test --release bench_candidate_materialize -- --ignored --nocapture
//!
//! Needs no store — the inputs are synthetic so the domain can be varied.

use std::cmp::Reverse;
use std::collections::BinaryHeap;
use std::hint::black_box;
use std::time::Instant;

use super::cost::{MATERIALIZE_SORT_FIXED_NS as SORT_FIXED_NS, MATERIALIZE_SORT_PER_CAND_NS as SORT_PER_CAND_NS};

const ITERS: usize = 60;

/// Real corpus size, so one row of every table is the case the engine actually has.
const REAL_DOMAIN: usize = 31_508;
/// Distinct arith-tuple combinations over that corpus; the natural run count there.
const REAL_RUNS: usize = 564;
/// The engine's current sorted-vec/bitmap switch, for reference in the output.
const BITS_PROMOTE_REF: usize = 4_096;
/// Printing-space domain at the same corpus, the other space a narrowing arm can land in.
const PRINTING_DOMAIN: usize = 97_206;

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

/// `runs` disjoint sorted id lists drawn from `0..domain`, holding `count` ids in
/// total, each run's ids scattered rather than contiguous.
fn make_runs(domain: usize, count: usize, runs: usize) -> Vec<Vec<u32>> {
    assert!(count <= domain && runs >= 1);
    // Deterministic xorshift64: reproducible without a dev-dependency.
    let mut state = 0x2545_F491_4F6C_DD1Du64;
    let mut next = move || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    // Pick `count` distinct ids by striding with a jitter, then sorting — cheaper
    // than rejection sampling and still scattered.
    let stride = domain / count.max(1);
    let mut ids: Vec<u32> = (0..count)
        .map(|i| {
            let base = i * stride;
            let j = if stride > 1 { (next() as usize) % stride } else { 0 };
            (base + j).min(domain - 1) as u32
        })
        .collect();
    ids.sort_unstable();
    ids.dedup();
    let mut out: Vec<Vec<u32>> = vec![Vec::new(); runs];
    for id in ids {
        out[(next() as usize) % runs].push(id);
    }
    for r in &mut out {
        r.sort_unstable();
    }
    out
}

fn concat_sort(runs: &[Vec<u32>], total: usize) -> Vec<u32> {
    let mut out: Vec<u32> = Vec::with_capacity(total);
    for r in runs {
        out.extend_from_slice(r);
    }
    out.sort_unstable();
    out
}

/// k-way merge over the already-sorted runs. Heap entries are (value, run, pos).
fn kway_merge(runs: &[Vec<u32>], total: usize) -> Vec<u32> {
    let mut out: Vec<u32> = Vec::with_capacity(total);
    let mut heap: BinaryHeap<Reverse<(u32, usize, usize)>> = BinaryHeap::with_capacity(runs.len());
    for (i, r) in runs.iter().enumerate() {
        if let Some(&v) = r.first() {
            heap.push(Reverse((v, i, 0)));
        }
    }
    while let Some(Reverse((v, i, p))) = heap.pop() {
        out.push(v);
        if let Some(&nv) = runs[i].get(p + 1) {
            heap.push(Reverse((nv, i, p + 1)));
        }
    }
    out
}

fn bitmap_extract(runs: &[Vec<u32>], total: usize, domain: usize) -> Vec<u32> {
    let mut bits = vec![0u64; domain.div_ceil(64)];
    for r in runs {
        for &id in r {
            bits[id as usize / 64] |= 1u64 << (id % 64);
        }
    }
    let mut out: Vec<u32> = Vec::with_capacity(total);
    for (w, &word) in bits.iter().enumerate() {
        let mut word = word;
        while word != 0 {
            let b = word.trailing_zeros();
            out.push((w * 64) as u32 + b);
            word &= word - 1;
        }
    }
    out
}

/// The scatter half alone — what the `CardBits` branch above `BITS_PROMOTE` already
/// pays. Subtracting this from `bitmap_extract` prices the extract pass, which is the
/// only thing lowering `BITS_PROMOTE` would save.
fn bitmap_scatter(runs: &[Vec<u32>], domain: usize) -> Vec<u64> {
    let mut bits = vec![0u64; domain.div_ceil(64)];
    for r in runs {
        for &id in r {
            bits[id as usize / 64] |= 1u64 << (id % 64);
        }
    }
    bits
}

// ─── Cost model ───────────────────────────────────────────────────────────────
// Both plans' inputs are known before either structure is allocated: the caller's
// first pass already sums the selected postings' lengths, and the rows are disjoint
// (each card belongs to exactly one combination), so that sum is the exact candidate
// count rather than an upper bound; the domain is `n_cards`. So the choice can be
// modelled from two numbers already in hand, and only the winning structure ever
// gets built. These constants are fit against axis A and B above, not assumed.

// The sort side's two constants are not defined here: `SORT_FIXED_NS` / `SORT_PER_CAND_NS` are
// `cost::materialize_cost`'s own, imported at the top of the file rather than restated. This bench
// exists to check that model, and a private copy of its constants can drift out from under it
// without any test failing.

/// The zeroed `vec![0u64; words]` allocation.
const BITMAP_FIXED_NS: f64 = 200.0;
/// Word scan on the extract pass. Fit beyond L1 (0.46–0.53 ns/word across axis B's
/// 100k–3M domains); inside L1 the real rate is ~0.23, so this over-prices small
/// domains, which biases the choice toward sort exactly where sort is already fine.
const BITMAP_PER_WORD_NS: f64 = 0.50;
/// One scatter store plus one `trailing_zeros` extraction per candidate.
const BITMAP_PER_CAND_NS: f64 = 0.83;

fn model_sort_ns(count: usize) -> f64 {
    SORT_FIXED_NS + SORT_PER_CAND_NS * count as f64
}

/// Both plans are linear in the candidate count, so the crossover has a closed form:
/// solving `model_sort_ns(c) == model_bitmap_ns(c, domain)` for `c` gives an *affine*
/// function of the word count. Reported next to the measured crossover so the shape of
/// the boundary is checked, not just its value at a few domains.
///
/// The closed form only exists while the bitmap is cheaper PER CANDIDATE than the sort — that
/// difference is the denominator. `SORT_PER_CAND_NS` is imported and flagged for a re-fit, so the
/// tripwire below is live: re-fit it under `BITMAP_PER_CAND_NS` and there is no crossover at all
/// (sort wins at every size), which this would otherwise print as a negative count.
fn model_crossover(domain: usize) -> f64 {
    let per_cand_gain = SORT_PER_CAND_NS - BITMAP_PER_CAND_NS;
    debug_assert!(
        per_cand_gain > 0.0,
        "no crossover exists: sort is {SORT_PER_CAND_NS} ns/cand against the bitmap's {BITMAP_PER_CAND_NS}, so the bitmap never catches up",
    );
    let words = domain.div_ceil(64) as f64;
    (BITMAP_FIXED_NS - SORT_FIXED_NS + BITMAP_PER_WORD_NS * words) / per_cand_gain
}

fn model_bitmap_ns(count: usize, domain: usize) -> f64 {
    BITMAP_FIXED_NS + BITMAP_PER_WORD_NS * domain.div_ceil(64) as f64 + BITMAP_PER_CAND_NS * count as f64
}

/// Candidate counts bracketing the crossover finely enough to locate it.
const CROSSOVER_COUNTS: [usize; 12] = [16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 1_024];

/// Fine sweep step for locating the crossover, as a percentage. The coarse
/// `CROSSOVER_COUNTS` grid steps by ~1.5x, which cannot resolve whether the boundary
/// collapses to a fixed domain:count ratio — that question needs steps finer than the
/// ratio drift being tested for.
const FINE_STEP_PCT: usize = 12;
/// Consecutive bitmap wins required to call a crossover, so one noisy sample at these
/// sub-microsecond sizes cannot declare it early.
const FINE_CONFIRM_STEPS: usize = 2;
/// Where the fine sweep starts; below this the bitmap has never won at any domain.
const FINE_START: usize = 32;

/// The measured crossover, located by a fine geometric sweep rather than read off the
/// coarse grid. Returns the first count with `FINE_CONFIRM_STEPS` consecutive bitmap
/// wins, or None if the bitmap never wins over the swept range.
fn fine_crossover(domain: usize, runs_n: usize) -> Option<usize> {
    let mut count = FINE_START;
    // The first count of the current unbroken win streak, and its length. The crossover is the
    // FIRST win of a confirmed streak, not the count that happened to confirm it.
    let mut streak_start: Option<usize> = None;
    let mut streak_len = 0usize;
    while count <= domain {
        let runs = make_runs(domain, count, runs_n);
        let total: usize = runs.iter().map(Vec::len).sum();
        let sort_ns = time_ns(|| concat_sort(&runs, total).len());
        let bitmap_ns = time_ns(|| bitmap_extract(&runs, total, domain).len());
        if bitmap_ns < sort_ns {
            streak_start = streak_start.or(Some(total));
            streak_len += 1;
            if streak_len >= FINE_CONFIRM_STEPS {
                return streak_start;
            }
        } else {
            streak_start = None;
            streak_len = 0;
        }
        count = (count * (100 + FINE_STEP_PCT) / 100).max(count + 1);
    }
    None
}

/// Measured vs modelled for one (domain, count) cell, plus the regret from following
/// the model: how much worse the model's pick is than the better plan, measured.
struct Cell {
    count: usize,
    sort_ns: f64,
    bitmap_ns: f64,
    model_picks_bitmap: bool,
    regret_ns: f64,
}

fn cells(domain: usize, runs_n: usize) -> Vec<Cell> {
    CROSSOVER_COUNTS
        .iter()
        .filter(|&&c| c <= domain)
        .map(|&count| {
            let runs = make_runs(domain, count, runs_n);
            let total: usize = runs.iter().map(Vec::len).sum();
            let sort_ns = time_ns(|| concat_sort(&runs, total).len());
            let bitmap_ns = time_ns(|| bitmap_extract(&runs, total, domain).len());
            let model_picks_bitmap = model_bitmap_ns(total, domain) < model_sort_ns(total);
            let chosen = if model_picks_bitmap { bitmap_ns } else { sort_ns };
            Cell { count: total, sort_ns, bitmap_ns, model_picks_bitmap, regret_ns: chosen - sort_ns.min(bitmap_ns) }
        })
        .collect()
}

/// First count at which the bitmap actually wins, and the first at which the model
/// says it does. `None` means it never won over the swept range.
fn crossovers(cells: &[Cell]) -> (Option<usize>, Option<usize>) {
    (
        cells.iter().find(|c| c.bitmap_ns < c.sort_ns).map(|c| c.count),
        cells.iter().find(|c| c.model_picks_bitmap).map(|c| c.count),
    )
}

fn row(domain: usize, count: usize, runs_n: usize, label: &str) {
    let runs = make_runs(domain, count, runs_n);
    let total: usize = runs.iter().map(Vec::len).sum();

    let a = concat_sort(&runs, total);
    let b = kway_merge(&runs, total);
    let c = bitmap_extract(&runs, total, domain);
    assert_eq!(a, b, "concat+sort vs merge disagree ({label})");
    assert_eq!(a, c, "concat+sort vs bitmap disagree ({label})");

    let t_s = time_ns(|| concat_sort(&runs, total).len());
    let t_m = time_ns(|| kway_merge(&runs, total).len());
    let t_b = time_ns(|| bitmap_extract(&runs, total, domain).len());
    let best = t_s.min(t_m).min(t_b);
    let win = if best == t_s {
        "sort"
    } else if best == t_m {
        "merge"
    } else {
        "bitmap"
    };
    println!(
        "{label:<26}{total:>8}{:>10.2}{:>10.2}{:>10.2}{win:>9}{:>8.2}x",
        t_s / 1000.0,
        t_m / 1000.0,
        t_b / 1000.0,
        [t_s, t_m, t_b].iter().copied().fold(f64::MIN, f64::max) / best,
    );
}

fn header(axis: &str) {
    println!("\n{axis:<26}{:>8}{:>10}{:>10}{:>10}{:>9}{:>8}", "cands", "sort µs", "merge µs", "bitmap µs", "best", "spread");
}

#[test]
#[ignore = "micro-benchmark; synthetic inputs, no store needed"]
fn bench_candidate_materialize_contenders() {
    println!(
        "\nthree ways to a sorted candidate list. runs are disjoint and scattered;\n\
         engine reference: domain {REAL_DOMAIN}, {REAL_RUNS} combinations, BITS_PROMOTE {BITS_PROMOTE_REF}"
    );

    // Axis 1: candidate count, at the real domain and run count.
    header("A. count @ real domain");
    for c in [16, 64, 256, 1_024, 4_096, 8_192, 16_384, 31_508] {
        row(REAL_DOMAIN, c, REAL_RUNS, &format!("  {c} of {REAL_DOMAIN}"));
    }

    // Axis 2: domain, holding the answer size fixed. This is the axis that decides
    // whether the bitmap's fixed cost is affordable.
    header("B. domain @ 4,096 cands");
    for d in [31_508, 100_000, 300_000, 1_000_000, 3_000_000] {
        row(d, 4_096, REAL_RUNS, &format!("  domain {d}"));
    }

    // Axis 3: run count, which only the merge should care about.
    header("C. runs @ 4,096 cands");
    for k in [8, 64, 564, 2_048] {
        row(REAL_DOMAIN, 4_096, k, &format!("  {k} runs"));
    }
}

/// Can the plan be *modelled* from the count and domain the caller already has, rather
/// than switched on a calibrated count threshold? The crossover is a function of both
/// (axis B), so a single count constant is only right at one domain. This checks the
/// model against measurement near the crossover, and prices being wrong.
#[test]
#[ignore = "micro-benchmark; synthetic inputs, no store needed"]
fn bench_candidate_materialize_cost_model() {
    println!(
        "\nmodelled plan choice. sort = {SORT_FIXED_NS} + {SORT_PER_CAND_NS}·c ns;\n\
         bitmap = {BITMAP_FIXED_NS} + {BITMAP_PER_WORD_NS}·⌈domain/64⌉ + {BITMAP_PER_CAND_NS}·c ns"
    );

    for (domain, name) in [(REAL_DOMAIN, "card space"), (PRINTING_DOMAIN, "printing space")] {
        println!("\nD. residuals @ domain {domain} ({name}), {REAL_RUNS} runs");
        println!("{:>8}{:>10}{:>10}{:>10}{:>10}{:>9}{:>11}", "cands", "sort µs", "model", "bmap µs", "model", "picks", "regret ns");
        for c in cells(domain, REAL_RUNS) {
            println!(
                "{:>8}{:>10.2}{:>10.2}{:>10.2}{:>10.2}{:>9}{:>11}",
                c.count,
                c.sort_ns / 1000.0,
                model_sort_ns(c.count) / 1000.0,
                c.bitmap_ns / 1000.0,
                model_bitmap_ns(c.count, domain) / 1000.0,
                if c.model_picks_bitmap { "bitmap" } else { "sort" },
                format!("{:.0}", c.regret_ns),
            );
        }
    }

    println!("\nE. crossover: where the bitmap starts winning, measured against modelled");
    println!("{:>10}{:>8}{:>12}{:>12}{:>12}", "domain", "words", "measured", "modelled", "max regret");
    for domain in [REAL_DOMAIN, PRINTING_DOMAIN, 300_000, 1_000_000, 3_000_000] {
        let cs = cells(domain, REAL_RUNS);
        let (measured, modelled) = crossovers(&cs);
        let worst = cs.iter().map(|c| c.regret_ns).fold(0.0f64, f64::max);
        let show = |v: Option<usize>| v.map_or("none ≤1024".to_string(), |c| c.to_string());
        println!("{domain:>10}{:>8}{:>12}{:>12}{worst:>11.0}ns", domain.div_ceil(64), show(measured), show(modelled));
    }

    // Does the two-dimensional surface collapse to one number? Domain enters the bitmap
    // linearly and the count enters it linearly too, so if the sort's log factor is weak
    // enough over the range that matters, `domain/count` alone would decide — a far
    // simpler rule than the model. Located with a fine sweep, since the answer being
    // tested for is a drift smaller than the coarse grid's 1.5x steps.
    println!("\nG. does it collapse to a domain:count ratio? ({FINE_STEP_PCT}% steps, {FINE_CONFIRM_STEPS} confirmations)");
    println!("{:>10}{:>8}{:>12}{:>10}{:>12}{:>10}", "domain", "words", "crossover", "ratio", "modelled", "ratio");
    for domain in [REAL_DOMAIN, PRINTING_DOMAIN, 300_000, 1_000_000, 3_000_000] {
        let measured = fine_crossover(domain, REAL_RUNS);
        let modelled = model_crossover(domain);
        println!(
            "{domain:>10}{:>8}{:>12}{:>10}{modelled:>12.0}{:>9.0}:1",
            domain.div_ceil(64),
            measured.map_or("none".to_string(), |c| c.to_string()),
            measured.map_or("-".to_string(), |c| format!("{:.0}:1", domain as f64 / c as f64)),
            domain as f64 / modelled,
        );
    }
}

/// Is the sort linear in the candidate count, or `n log n`? `sort_unstable` is a full
/// pdqsort, so asymptotically it is the latter — but the model only has to be right over
/// the corpus sizes this engine sees. Per-element cost flat across the sweep says the log
/// factor is not observable here; per-element cost rising like `log2 c` says it is.
///
/// Answer (minimum of 10 runs): faintly rising, far under `n log n`. 4.35 ns/elem at 1,024 to 5.08 at
/// 31,508 — a 1.17x rise across 31x the size, where `n log n` demands 1.49x. Linear is the right
/// model over this range, which is what `cost::MATERIALIZE_SORT_PER_CAND_NS` assumes.
///
/// **Run this more than once and take the minimum per column.** A single run on a machine doing
/// anything else reads as flat ~5.0 at every size, which inverts the conclusion: the contention here
/// is one-sided, so the max is contaminated while min and median agree to within 0.06 ns/elem.
#[test]
#[ignore = "micro-benchmark; synthetic inputs, no store needed"]
fn bench_candidate_materialize_sort_shape() {
    println!("\nH. is concat+sort linear? @ domain {REAL_DOMAIN}, {REAL_RUNS} runs");
    println!("{:>8}{:>10}{:>12}{:>14}{:>14}", "cands", "sort µs", "ns/elem", "if linear", "if n·log2 n");
    // Reference point the two shapes are both anchored to, so they can only disagree
    // about growth, not about scale.
    const ANCHOR: usize = 1_024;
    let anchor_runs = make_runs(REAL_DOMAIN, ANCHOR, REAL_RUNS);
    let anchor_total: usize = anchor_runs.iter().map(Vec::len).sum();
    let anchor_ns = time_ns(|| concat_sort(&anchor_runs, anchor_total).len());
    let per_elem = anchor_ns / anchor_total as f64;
    let per_elem_log = per_elem / (anchor_total as f64).log2();

    for c in [ANCHOR, 4_096, 8_192, 16_384, 31_508] {
        let runs = make_runs(REAL_DOMAIN, c, REAL_RUNS);
        let total: usize = runs.iter().map(Vec::len).sum();
        let ns = time_ns(|| concat_sort(&runs, total).len());
        println!(
            "{total:>8}{:>10.2}{:>12.2}{:>14.2}{:>14.2}",
            ns / 1000.0,
            ns / total as f64,
            per_elem,
            per_elem_log * (total as f64).log2(),
        );
    }
}

/// How much of the bitmap plan is the extract pass? That difference is what a lower
/// `BITS_PROMOTE` would save, since the scatter is paid either way — the production-side
/// half of the threshold question the issue doc leaves open.
#[test]
#[ignore = "micro-benchmark; synthetic inputs, no store needed"]
fn bench_candidate_materialize_extract_split() {
    println!("\nF. scatter against scatter+extract @ domain {REAL_DOMAIN}, {REAL_RUNS} runs");
    println!("{:>8}{:>12}{:>12}{:>12}{:>10}", "cands", "scatter µs", "+extract", "extract µs", "sort µs");
    for c in [64, 256, 1_024, 4_096, 16_384, 31_508] {
        let runs = make_runs(REAL_DOMAIN, c, REAL_RUNS);
        let total: usize = runs.iter().map(Vec::len).sum();
        let t_scatter = time_ns(|| bitmap_scatter(&runs, REAL_DOMAIN).len());
        let t_both = time_ns(|| bitmap_extract(&runs, total, REAL_DOMAIN).len());
        let t_sort = time_ns(|| concat_sort(&runs, total).len());
        println!(
            "{total:>8}{:>12.2}{:>12.2}{:>12.2}{:>10.2}",
            t_scatter / 1000.0,
            t_both / 1000.0,
            (t_both - t_scatter) / 1000.0,
            t_sort / 1000.0,
        );
    }
}
