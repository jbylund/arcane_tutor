//! Micro-benchmark: does `unique=card` PrintingCompose paging pay for its
//! printing→card projection once or twice? Item 13 of
//! docs/issues/00799-engine-simplicity-pass.md.
//!
//! `printing_compose_fastpath` projects the composed `pbits` into card space to popcount
//! `Mode::Card`'s `total`. When the orderby has no card-space permutation (`usd`/`rarity`)
//! and no printing-space walk applies (both true under `unique=card`), paging falls to
//! `gather_composed_page`, which derived its candidate cards from the *same* projection
//! over the *same* bits — a second full pass, discarded the first time.
//!
//! Two contenders over the identical `pbits`, both producing `(total, candidate_cards)`:
//! - `derive_twice`: what shipped — project for the total, project again for the candidates.
//! - `derive_once`: project once, popcount it, then extract ids from it.
//!
//! `printing_bits_to_card_bits` is O(n_printings/64) words scanned plus a set-bit
//! extraction per surviving printing, so the saving tracks selectivity rather than being
//! flat. The sweep below runs sparse → near-total to show that curve.
//!
//! `gather_composed_page` is timed alongside, not because it changes, but because a
//! saving is only worth reporting next to the path it sits in: the last column is the
//! share of the whole (derive + gather) card-mode paging cost that this removes.
//!
//!     cargo test --release bench_compose_card_projection -- --ignored --nocapture
//!
//! Needs benchmarks/verify-order/real.store (same file/rebuild contract as bench_verify_cost.rs).

use std::hint::black_box;
use std::time::Instant;

use rkyv::Archived;

use super::{
    archive_header, archive_payload, bitmap_card_ids, compose_printing_bits, gather_composed_page, printing_bits_to_card_bits, AOffsets,
    CardData, CmpOp, CollField, FilterExpr, Mmap, Mode, Prefer, QueryCtx, QueryParams, SortCol, ARCHIVE_HEADER_LEN,
};

const ITERS: usize = 200;
const LIMIT: usize = 175; // a Scryfall page
const STORE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../benchmarks/verify-order/real.store");

/// This bench's fixed query shape: `unique=card`, `orderby=usd` ascending. `usd` has no
/// card-space sort permutation, and the printing-space orderby walk requires
/// `Mode::Printing` — so card mode lands in `gather_composed_page`, the affected branch.
fn card_params(page_offset: usize) -> QueryParams {
    QueryParams { mode: Mode::Card, prefer: Prefer::Default, sort_col: SortCol::PriceUsd, descending: false, limit: LIMIT, page_offset }
}

fn best_ns(mut kernel: impl FnMut() -> usize) -> u128 {
    let mut best = u128::MAX;
    for _ in 0..ITERS {
        let t0 = Instant::now();
        black_box(kernel());
        best = best.min(t0.elapsed().as_nanos());
    }
    best
}

fn popcount(bits: &[u64]) -> usize {
    bits.iter().map(|w| w.count_ones() as usize).sum()
}

/// What shipped: the total's projection is thrown away, then rebuilt for the candidates.
fn derive_twice(pbits: &[u64], offsets: &AOffsets, n_cards: usize) -> (usize, Vec<u32>) {
    let total = popcount(&printing_bits_to_card_bits(pbits, offsets, n_cards));
    let candidates = bitmap_card_ids(&printing_bits_to_card_bits(pbits, offsets, n_cards));
    (total, candidates)
}

/// One projection, both consumers.
fn derive_once(pbits: &[u64], offsets: &AOffsets, n_cards: usize) -> (usize, Vec<u32>) {
    let card_bits = printing_bits_to_card_bits(pbits, offsets, n_cards);
    (popcount(&card_bits), bitmap_card_ids(&card_bits))
}

#[test]
#[ignore = "micro-benchmark; needs benchmarks/verify-order/real.store (see module docs)"]
fn bench_compose_card_projection() {
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
    let n_printings = data.printings.len();
    let n_cards = data.cards.len();
    let offsets = &data.offsets;
    let ctx = QueryCtx::from(data);
    println!("\n{n_printings} printings, {n_cards} cards from {STORE_PATH}");

    let coll = |field, value: &str, negate: bool| -> FilterExpr {
        let leaf = FilterExpr::CollectionCmp { field, op: CmpOp::Ge, value: value.to_string(), value_id: None };
        if negate { FilterExpr::Not(Box::new(leaf)) } else { leaf }
    };

    // Same selectivity sweep as bench_compose_paging, so the two benches' rows line up:
    // real composable collection leaves, sparse → near-total. Subtypes are title-case.
    let filters: Vec<(&str, FilterExpr)> = vec![
        ("type:Octopus (very sparse)", coll(CollField::Subtypes, "Octopus", false)),
        ("type:Goblin (sparse)", coll(CollField::Subtypes, "Goblin", false)),
        ("type:Human (mid)", coll(CollField::Subtypes, "Human", false)),
        ("-type:Human (~85%)", coll(CollField::Subtypes, "Human", true)),
        ("-type:Goblin (~98%)", coll(CollField::Subtypes, "Goblin", true)),
        ("-type:Octopus (near-total)", coll(CollField::Subtypes, "Octopus", true)),
    ];

    println!(
        "\n  {:<30} {:>6} {:>7} {:>10} {:>10} {:>9} {:>10} {:>8}",
        "predicate", "sel%", "cards", "twice ns", "once ns", "saved ns", "gather ns", "saved %"
    );
    for (label, filter) in &filters {
        let pbits = compose_printing_bits(filter, &data.indexes, offsets, &data.printings, n_printings);
        let printing_total = popcount(&pbits);
        if printing_total == 0 {
            println!("  {label:<30}  (0 matches — skipped)");
            continue;
        }
        let sel = 100.0 * printing_total as f64 / n_printings as f64;

        // Agreement before timing: the projection is idempotent over the same bits, so the
        // two derivations must produce the identical total and candidate list. A divergence
        // here means the reuse is not sound, which is the whole thing being claimed.
        let (twice_total, twice_cands) = derive_twice(&pbits, offsets, n_cards);
        let (once_total, once_cands) = derive_once(&pbits, offsets, n_cards);
        assert_eq!(twice_total, once_total, "{label}: totals differ");
        assert_eq!(twice_cands, once_cands, "{label}: candidate cards differ");

        let twice_ns = best_ns(|| derive_twice(&pbits, offsets, n_cards).1.len());
        let once_ns = best_ns(|| derive_once(&pbits, offsets, n_cards).1.len());
        let card_bits = printing_bits_to_card_bits(&pbits, offsets, n_cards);
        let gather_ns = best_ns(|| gather_composed_page(&ctx, &card_params(0), &pbits, Some(&card_bits)).len());

        let saved = twice_ns.saturating_sub(once_ns);
        let share = 100.0 * saved as f64 / (twice_ns + gather_ns) as f64;
        println!(
            "  {label:<30} {sel:>5.2} {:>7} {twice_ns:>10} {once_ns:>10} {saved:>9} {gather_ns:>10} {share:>7.1}%",
            once_total,
        );
    }
    println!();
}
