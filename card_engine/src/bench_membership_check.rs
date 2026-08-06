//! What per-printing membership costs the two ways it could be done, for
//! docs/issues/00856-engine-compose-membership-bittest.md (the bitmap route, general) and
//! docs/issues/00857-engine-membership-merge-sorted-list.md (the merge, GatheredScan only).
//!
//! #856 replaces a per-printing residual evaluation (measured at 5.9 ns/printing by
//! `scripts/bench_membership_waste.py`) with an O(1) membership check. That check was an ASSUMPTION of
//! ~1 ns and the whole estimate rests on it, because the saving is `residual_ns - check_ns`. This
//! measures it, and measures the alternative that needs no bitmap at all.
//!
//! ## The model comes from the measurement, not from round numbers
//!
//! `scripts/bench_membership_waste.py --mode realistic` reports, per query on the population #856
//! touches: **234 candidate cards, 3,183 printings in their spans (13.6 per card — these are
//! heavily-reprinted cards, as `set:` queries produce), 366 of them matching (11.5%, so 1.56 per card).**
//!
//! Two structural facts follow, and an earlier version of this bench got both wrong:
//!
//! 1. **Visited cards are exactly the cards holding a candidate printing**, because `card_ids` is
//!    derived FROM the candidate printing list via `cards_of_printings`. So every visited card has at
//!    least one match, and the candidate list contains no pid outside a visited span. A model that
//!    strides over all cards makes the merge below pay to skip pids that cannot exist.
//! 2. **Visited cards hold 13.6 printings, not the corpus-average 3.09.** Using the average understates
//!    the span work per card by 4x.
//!
//! ## The two candidates
//!
//! - `bit_test` — scatter the candidate list into a printing bitmap, then `bitmap_contains` per printing
//!   in each span. O(span). Works for any visit order.
//! - `merge` — two-pointer over the sorted candidate list the narrowing ALREADY produced
//!   (`Candidates::Printings`, still live as `raw_candidates` before it is flattened to card ids). No
//!   bitmap, no allocation, no scatter pass, and O(matches) rather than O(span).
//!
//! `merge` is sound only because the gather loop visits pids in globally ascending order —
//! `cards_of_printings` yields ascending cids and each card's span is contiguous. **`StreamedSelect`
//! walks a permutation in SORT order, so a forward pointer would be wrong there and the bitmap is the
//! only option.** Every query measured on this population picked `GatheredScan`.
//!
//! `walk_only` is the floor: the same traversal touching neither structure.
//!
//! Corpus scale is swept because the bitmap is 12 KB at production scale (97,206 printings, 1,519
//! words) — L1-resident — and that stops being true as the corpus grows.
//!
//!     cargo test --release bench_membership_check -- --ignored --nocapture

use std::hint::black_box;
use std::time::Instant;

use rand::RngExt;
use rand::SeedableRng as _;

use crate::planes::bitmap_contains;

/// Production corpus, from `benchmarks/bitplanes/corpus.jsonl`.
const N_PRINTINGS_1X: usize = 97_206;
/// Rounds per cell; min-of-N, matching every other bench here.
const ITERS: usize = 400;
/// Candidate cards per query, measured.
const VISITED_CARDS: usize = 234;
/// Printings per VISITED card, measured (13.6). Not the corpus average of 3.09.
const SPAN_LEN: usize = 14;
/// Matches per visited card. 1 is the floor the construction guarantees; 1.56 is measured, so 2 is the
/// nearest integer above it; the rest sweep toward "every printing matches" to show the shape.
const MATCHES_PER_CARD: [usize; 5] = [1, 2, 4, 7, 14];

/// One query's candidate cards: `VISITED_CARDS` spans of `SPAN_LEN`, spread evenly through the corpus so
/// the bitmap reads are strided across cache lines the way scattered candidate cards really are.
fn build_spans(n_printings: usize, n_cards: usize) -> Vec<(usize, usize)> {
    let gap = n_printings / n_cards;
    (0..n_cards).map(|i| (i * gap, i * gap + SPAN_LEN)).collect()
}

/// Sorted candidate pids, `matches_per_card` drawn from each span — so every visited card has at least
/// one, and no pid falls outside a visited span. Both are true of the real narrowing.
fn build_candidates(rng: &mut rand::rngs::SmallRng, spans: &[(usize, usize)], matches_per_card: usize) -> Vec<u32> {
    let mut out = Vec::with_capacity(spans.len() * matches_per_card);
    for &(start, end) in spans {
        let mut chosen: Vec<usize> = (start..end).collect();
        // Take a random subset of the span, then keep it ascending: the list the narrowing hands over
        // is globally sorted, which is what makes the merge valid.
        for i in 0..chosen.len() {
            let j = (rng.random::<u64>() % (chosen.len() as u64)) as usize;
            chosen.swap(i, j);
        }
        chosen.truncate(matches_per_card.min(end - start));
        chosen.sort_unstable();
        out.extend(chosen.iter().map(|&p| p as u32));
    }
    out
}

fn to_bitmap(pids: &[u32], n_printings: usize) -> Vec<u64> {
    let mut bits = vec![0u64; n_printings.div_ceil(64)];
    for &p in pids {
        bits[(p >> 6) as usize] |= 1u64 << (p & 63);
    }
    bits
}

/// Scatter + probe: the bitmap route. Conditionally appends, as `push_card_matches` does, so it pays a
/// real branch — a counting form (`hits += 1`) is predicated by the compiler into branch-free code and
/// reads density-flat, which is how an earlier version of this bench produced a misleadingly good number.
fn bit_test(bits: &[u64], spans: &[(usize, usize)], out: &mut Vec<u32>) -> u32 {
    out.clear();
    for &(start, end) in spans {
        for pid in start..end {
            if bitmap_contains(bits, pid as u32) {
                out.push(pid as u32);
            }
        }
    }
    out.len() as u32
}

/// Two-pointer merge against the sorted candidate list. Never touches a non-matching printing.
fn merge(sorted_pids: &[u32], spans: &[(usize, usize)], out: &mut Vec<u32>) -> u32 {
    out.clear();
    let mut j = 0usize;
    for &(start, end) in spans {
        while j < sorted_pids.len() && (sorted_pids[j] as usize) < start {
            j += 1;
        }
        while j < sorted_pids.len() && (sorted_pids[j] as usize) < end {
            out.push(sorted_pids[j]);
            j += 1;
        }
    }
    out.len() as u32
}

/// The cost the bitmap route pays before it can probe: allocate and zero `n_printings / 64` words, then
/// scatter the candidate list. Charged separately because it is per QUERY, not per printing, and it is
/// exactly what the merge avoids.
fn scatter_cost(pids: &[u32], n_printings: usize) -> u32 {
    let bits = to_bitmap(pids, n_printings);
    bits.len() as u32
}

/// Per-query setup for the permutation-order route: key each candidate pid by its card's position in the
/// sort permutation, then sort. That is what would let a forward pointer work during a `StreamedSelect`
/// walk, which visits cards in permutation order rather than ascending cid.
///
/// Charged against `scatter_cost`, since the two are alternatives: both are one-off per-query work that
/// buys an ordered structure to walk against.
fn permuted_sort_cost(pids: &[u32], printing_to_card: &[u32], inv_perm: &[u32]) -> u32 {
    let mut keyed: Vec<(u32, u32)> = pids
        .iter()
        .map(|&p| (inv_perm[printing_to_card[p as usize] as usize], p))
        .collect();
    keyed.sort_unstable();
    keyed.len() as u32
}

/// The traversal touching neither structure — loop overhead alone.
fn walk_only(spans: &[(usize, usize)]) -> u32 {
    let mut acc = 0u32;
    for &(start, end) in spans {
        for pid in start..end {
            acc = acc.wrapping_add(pid as u32);
        }
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

#[test]
#[ignore = "micro-benchmark; synthetic data, no external deps"]
fn bench_membership_check_cost() {
    let mut rng = rand::rngs::SmallRng::seed_from_u64(8_560_001);

    for scale in [1usize, 5] {
        let n_printings = N_PRINTINGS_1X * scale;
        let spans = build_spans(n_printings, VISITED_CARDS);
        let examined: usize = spans.iter().map(|(s, e)| e - s).sum();
        let kb = n_printings.div_ceil(64) * 8 / 1024;
        println!(
            "\n=== corpus {scale}x — {n_printings} printings, bitmap {kb} KB, {VISITED_CARDS} cards x {SPAN_LEN} = {examined} examined ==="
        );
        println!(
            "{:>9}  {:>8}  {:>9}  {:>7}  {:>7}  {:>10}  {:>10}  {:>9}",
            "match/card", "density", "bit_test", "merge", "walk", "scatter/q", "permsort/q", "winner"
        );

        // Permutation-order inputs for the StreamedSelect route: a card's position in the sort order is
        // uncorrelated with its cid, so a shuffled permutation is the honest model.
        let n_cards = n_printings / SPAN_LEN + 1;
        let mut printing_to_card: Vec<u32> = vec![0; n_printings];
        for (ci, &(s, e)) in spans.iter().enumerate() {
            for slot in printing_to_card.iter_mut().take(e.min(n_printings)).skip(s) {
                *slot = ci as u32;
            }
        }
        let mut perm: Vec<u32> = (0..n_cards as u32).collect();
        for i in 0..perm.len() {
            let j = (rng.random::<u64>() % (perm.len() as u64)) as usize;
            perm.swap(i, j);
        }
        let mut inv_perm: Vec<u32> = vec![0; n_cards];
        for (pos, &cid) in perm.iter().enumerate() {
            inv_perm[cid as usize] = pos as u32;
        }

        let mut out: Vec<u32> = Vec::with_capacity(examined);
        for m in MATCHES_PER_CARD {
            let pids = build_candidates(&mut rng, &spans, m);
            let bits = to_bitmap(&pids, n_printings);
            let t_bit = time_ns(|| bit_test(&bits, &spans, &mut out)) / examined as f64;
            let t_merge = time_ns(|| merge(&pids, &spans, &mut out)) / examined as f64;
            let t_walk = time_ns(|| walk_only(&spans)) / examined as f64;
            // Per query, not per printing: this is the one-off the bitmap route pays and merge does not.
            let t_scatter = time_ns(|| scatter_cost(&pids, n_printings)) / 1000.0;
            let t_psort = time_ns(|| permuted_sort_cost(&pids, &printing_to_card, &inv_perm)) / 1000.0;
            let winner = if t_merge < t_bit { "merge" } else { "bit_test" };
            println!(
                "{:>10}  {:>7.0}%  {:>9.2}  {:>7.2}  {:>7.2}  {:>8.1}us  {:>8.1}us  {:>9}",
                m,
                100.0 * pids.len() as f64 / examined as f64,
                t_bit,
                t_merge,
                t_walk,
                t_scatter,
                t_psort,
                winner
            );
        }
    }
    println!("\nns per printing in a visited span, min of {ITERS} rounds; `scatter/q` is microseconds PER QUERY.");
    println!("Measured production point is 2 matches/card (1.56) at ~11% density — read that row.");
    println!("Compare against the 5.9 ns/printing residual evaluation both would replace.");
    println!("merge is GatheredScan-only (ascending pid order). For StreamedSelect's permutation walk the");
    println!("options are the bitmap (scatter/q) or keying candidates by perm position and sorting");
    println!("(permsort/q) so a forward pointer works -- the two per-query setups compared side by side.");
}
