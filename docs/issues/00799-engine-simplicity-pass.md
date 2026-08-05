# Engine: Simplicity Pass — Duplicated Grammars, Repeated Comparators, and Comment Density

Status: proposed, nothing implemented. Tracked by
[#799](https://github.com/jbylund/sylvan_librarian/issues/799). Findings from a read-through of the
whole engine at `c950dd8` (main, #796). No behavior changes are proposed anywhere in this doc —
every item is either a same-output refactor or a strictly-cheaper rephrasing, so the existing
differential tests (`force_plan_differential_agreement`, `fuzz_row_identity_matches_reference`) are
the safety net throughout.

This is a hitlist rather than one shippable idea, which stretches the [one-idea-per-doc
rule](README.md#length-and-scope). It stays one doc because the items are individually too small to
justify their own, and because the batching below is the actual plan: three mechanical commits plus
one real refactor. **Item 1 should be extracted into its own doc when it gets scheduled** — it is
the one entry with enough design surface to need it.

## Why now

The engine is structurally sound: the plan router, the `Narrowed`/`Candidates` algebra, and the
tri-valued filter are cleanly factored, and most of what *looks* like duplication is documented as
deliberate (see [What not to touch](#what-not-to-touch)). What has accumulated instead is a specific
pattern — **the same grammar or comparator spelled out in several places, with a prose comment
asserting the copies agree.** `compose_printing_bits`'s doc says "gated by `is_printing_composable`";
five call sites re-declare a comparator whose named form is 40 lines up and claim "byte-identical
order to the gathered path". Each assertion is true today. None of them is checked by the compiler.

That is the through-line: **replace prose claims of agreement with structural agreement.**

## Batch A — one grammar, three implementations

### 1. Collapse the printing-compose grammar into one classifier

The composable-leaf grammar is written three times, ~235 lines total:

| Function | Lines | Job |
|---|---|---|
| [`is_printing_composable`](../../card_engine/src/lib.rs#L4745) | 4745-4806 | is this shape composable? |
| [`compose_printing_bits`](../../card_engine/src/lib.rs#L5123) | 5123-5200 | build its exact bitmap |
| [`compose_printing_estimate`](../../card_engine/src/lib.rs#L5211) | 5211-5308 | estimate its cost |

All three match the same leaves (`border ==`, `set:`/`watermark:`, `-set:`, collection `Ge` and its
negation, rarity `Eq`, legality, range and negated range) with the same guards, and the two
materializing ones end in `unreachable!("gated by is_printing_composable")`. The docs already name
the risk this creates and mitigate it by hand, routing field dispatch through shared helpers
(`collection_compose_index`, `bare_range_bounds`).

**Proposed shape:** one classifier that resolves the leaf *and* its backing index in a single pass:

```rust
enum ComposeLeaf<'i> {
    BorderPlane(&'i str),
    RarityPlane(f64),
    TagPostings(&'i Archived<TagIndex>, &'i str, /* negated */ bool),
    Collection(&'i Archived<TagIndex>, &'i str, /* card_space */ bool, /* negated */ bool),
    Legality(u8, u64),
    Range(&'i Archived<PrintingRangeIndex>, u32, u32),
}

fn classify_compose_leaf<'i>(f: &FilterExpr, indexes: &'i Archived<CardIndexes>) -> Option<ComposeLeaf<'i>>;
```

Then `is_printing_composable` becomes a recursion over `classify(...).is_some()`, and both
`compose_printing_bits` and `compose_printing_estimate` match on `ComposeLeaf` — a closed set the
compiler checks — instead of re-deriving the shape from `FilterExpr`. The two `unreachable!()` arms
disappear because the grammar exists once.

This also removes six instances of the **double-destructure pattern**: guard with
`matches!(inner.as_ref(), Pat)`, then immediately re-destructure with
`let Pat = inner.as_ref() else { unreachable!("guarded by the matches! above") }` —
[5159](../../card_engine/src/lib.rs#L5159), [5176](../../card_engine/src/lib.rs#L5176),
[5248](../../card_engine/src/lib.rs#L5248), [5270](../../card_engine/src/lib.rs#L5270), plus
[3350](../../card_engine/src/lib.rs#L3350) and [3367](../../card_engine/src/lib.rs#L3367) in
`narrow_rec`. The pattern exists because a match guard cannot bind; a classifier returning the bound
data sidesteps it entirely.

**Risk:** the only real one. `compose_printing_estimate` deliberately costs `broadcast` and
`scatter` at *different rates* per leaf kind, and the legality arm scales from `min(legal, illegal)`
(#744). Those distinctions must survive the move onto `ComposeLeaf`. Check `plan_cost` outputs are
unchanged on the calibration corpus before/after.

### 2. `bare_range_bounds` writes its leaf grammar twice

[4460-4500](../../card_engine/src/lib.rs#L4460): the direct arm and the `Not` arm are the same
three-way NumericCmp/DateCmp/YearCmp dispatch, differing only in `op` vs `negate_op(op)`. Extract an
inner helper taking `map_op: impl Fn(CmpOp) -> CmpOp`; each arm becomes one line. Related to item 1
(the `Range` leaf resolves through this) but landable independently.

## Batch B — mechanical, no-behavior-change (one commit)

### 3. `page_cmp` exists but is re-declared inline five times

[`page_cmp`](../../card_engine/src/lib.rs#L3840) is the named comparator. The identical closure is
re-written at [3879](../../card_engine/src/lib.rs#L3879) (`select_page`),
[4557](../../card_engine/src/lib.rs#L4557) (`walk_printing_page`),
[4674](../../card_engine/src/lib.rs#L4674) (`aligned_page`),
[6104](../../card_engine/src/lib.rs#L6104) (`walk_grouped_page`), and
[7028](../../card_engine/src/lib.rs#L7028) (`run_query_streamed`). Every one of those sites has a
comment claiming its ordering matches the gathered path's; using the function makes the claim
structural. Pure substitution.

### 4. Two stale `#[allow(dead_code)]`

[`gathered_scan_applicable`](../../card_engine/src/lib.rs#L5778) ("referenced through the force entry
point") and [`scan_units`](../../card_engine/src/lib.rs#L6375) ("consumed by the cost benches
(tests.rs) and future all-mode routing") are both called from ordinary non-test code —
`PhysicalPlan::applicable` and `candidate_feats` respectively. **Verified:** removing both attributes
leaves `cargo check --lib` clean. The attributes and their comments are wrong today, and both
comments actively mislead about where the functions are used.

### 5. Dead binding

[3649](../../card_engine/src/lib.rs#L3649): `let _ = b;` inside a
`Candidates::PrintingBits(b) if …` arm. Bind `_` in the pattern.

### 6. The rarity `keep` closure, written twice, with a silent semantic difference

[2320](../../card_engine/src/lib.rs#L2320) (`rarity_candidates`) and
[2362](../../card_engine/src/lib.rs#L2362) (`rarity_plane_candidates`) declare the same
op-dispatch closure — except the first maps `Ne => false` and the second `Ne => r != val`. The
first is dead (the function returns `None` for `Ne` two lines earlier), so this is currently
harmless and currently invisible.

`filter.rs` already has exactly this function: [`fn cmp(op, a, b)`](../../card_engine/src/filter.rs#L272).
Make it `pub(crate)` and delete both closures.

### 7. `Candidates::len()`'s doc contradicts itself

[2641](../../card_engine/src/lib.rs#L2641): "Approximate member count (exact for both
representations)."

## Batch C — repeated shapes worth extracting

### 8. The Card/Artwork group-best emission block is byte-identical in two functions

[`walk_grouped_page`:6135-6160](../../card_engine/src/lib.rs#L6135) and
[`gather_composed_page`:6239-6264](../../card_engine/src/lib.rs#L6239) are the same
`group_best` / `touched` / `take()` loop, character for character.
[`push_card_matches`:4329-4385](../../card_engine/src/lib.rs#L4329) holds a third near-copy.

The two compose walks differ only in candidate source (permutation vs `bitmap_card_ids`) and sink
(`page` vs `GatherSelect`). Extract the grouping into one `#[inline]` helper those two share; leave
`push_card_matches` out of it (see [What not to touch](#what-not-to-touch)).

### 9. The directional value-bucket walk is duplicated — DONE, by deleting both copies

[`aligned_page`:4632-4649](../../card_engine/src/lib.rs#L4632) and
[`walk_range_orderby_page`:5414-5435](../../card_engine/src/lib.rs#L5414) contain the same "next
maximal run of equal value, forward or backward" loop — the fiddliest code in the paging layer, in
two copies. Extract `next_value_bucket(idx, &mut lo, &mut hi, descending) -> (usize, usize)`.

Resolved without the helper. The value-major layout made runs contiguous and pre-delimited by
`starts`, so "next maximal run of equal value" became an offset subtraction and both loops went away:
`aligned_page` emits a `pids` slice directly and `collect_orderby_page` no longer exists. See
[done/local-engine-value-major-sort-indexes.md](./done/local-engine-value-major-sort-indexes.md).

### 10. CSR build and CSR expand, three copies each

Expand-and-sort: [`expand_text_ids`](../../card_engine/src/lib.rs#L1154),
[`expand_artist_ids`](../../card_engine/src/lib.rs#L1687),
[`expand_flavor_ids`](../../card_engine/src/lib.rs#L1851) — identical modulo field names.

Build (count → prefix-sum → place with a cursor):
[oracle text](../../card_engine/src/lib.rs#L1088), [artists](../../card_engine/src/lib.rs#L1664),
[flavor](../../card_engine/src/lib.rs#L1793).

One generic `expand_csr` plus one `build_csr` removes ~60 lines of load-path code and gives the CSR
convention a single documented home.

### 11. Three interners differing only in id width

[421-509](../../card_engine/src/lib.rs#L421): `Interner` (u32), `VocabInterner` (u16),
`ManaVocabInterner` (u8) — ~90 lines for one algorithm with three id types and three overflow
messages. A single `Interner<Id: TryFrom<usize>>` covers all three; the width-specific overflow text
becomes a parameter.

## Batch D — performance

Each of these is below the noise floor of end-to-end query timing. Per the project's usual practice,
measure items 12 and 14 with kernel micro-benchmarks (`bench_posting_intersect.rs`,
`bench_compose_paging.rs`) rather than a query A/B.

### 12. `swap_remove(0)` defeats the length-ordering the code just did

[`and_all`:2709-2717](../../card_engine/src/lib.rs#L2709) says "Intersect the vecs by ascending
length", then:

```rust
vecs.sort_unstable_by_key(Vec::len);
let mut result = vecs.swap_remove(0);
for v in vecs { result = intersect_sorted(&result, &v); }
```

`swap_remove(0)` moves the **largest** vec into slot 0, so the remaining chain runs largest-first —
the opposite of the stated intent, and the expensive direction, since `intersect_sorted` is
O(|a| + |b|). [`intersect_operands`:1321-1331](../../card_engine/src/lib.rs#L1321) has the same
shape (its "smallest posting seeds the working set" claim holds; the *rest* of the order is
scrambled).

Fix is also the simpler phrasing: `let mut it = vecs.into_iter(); let mut result = it.next()?;`

### 13. Card-mode compose projects printing→card twice

[`printing_compose_fastpath`:5571](../../card_engine/src/lib.rs#L5571) computes
`printing_bits_to_card_bits` for the total. When there is no permutation (`orderby=usd`/`rarity`
under `unique=card`), paging falls to
[`gather_composed_page`:6202](../../card_engine/src/lib.rs#L6202), which computes the same
projection over the same `pbits` again. Pass the card bits (or the derived candidate list) in.

### 14. `best` is recomputed from scratch on every And child

[`narrow_rec`:3591](../../card_engine/src/lib.rs#L3591) runs
`card_sets.iter().chain(printing_sets.iter()).map(|n| n.set.len()).min()` **inside** the per-child
loop, and `Candidates::len()` popcounts an entire bitmap. That is O(children² × words). Track the
running minimum as sets are pushed.

Measured (`bench_and_best.rs`, #811): 1.5x at two bitmap children, 2.5x at four, 3.5x at six —
50 ns to 1.9 μs per And node at realistic widths, and nothing at all when every child is
vec-shaped, since `Vec::len` was never the problem.

### 15. `and_all` clones a whole bitmap for nothing

[2719-2725](../../card_engine/src/lib.rs#L2719):
`bit_sets.split_first().map(|(first, rest)| { let mut acc = first.clone(); … })` — on an owned
`Vec<Vec<u64>>`. `into_iter()` takes ownership instead.

Measured (`bench_narrow_alloc.rs` section A, #811): 1.06-1.45x, i.e. 60-130 ns per `and_all` that
sees bitmaps. Biggest at two printing-space operands (414 → 286 ns), shrinking as the AND chain
grows and the saved memcpy becomes a smaller share.

### 16. Two avoidable allocations in narrowing

- ~~The oracle-word sparse expansion chains `union_sorted` once per matched dictionary word
  ([3172-3181](../../card_engine/src/lib.rs#L3172)) — quadratic in total postings for a needle that
  hits many words. Collect, then `sort_unstable` + `dedup`: linearithmic and shorter.~~
  **Measured and rejected** (#811): the fold beats sort+dedup by 2-5x for the 98.8% of needles
  that match ≤ 7 dictionary words, and only loses past ~24. Kept as a threshold proposal in
  [local-engine-sparse-union-threshold.md](local-engine-sparse-union-threshold.md);
  `bench_narrow_alloc.rs` section B holds the numbers.
- The regex-factor arm clones the candidate vec — `acc.map_or(cand.clone(), …)`
  ([3255](../../card_engine/src/lib.rs#L3255)). Note `map_or` takes its default **by value**, so
  this clones on *every* factor, not just the first. A plain `match` avoids it: 2-3x on
  single-factor regexes, 1.05-1.08x on multi-factor ones (`bench_narrow_alloc.rs` section C).

## Batch E — comment density

Measured line counts at `c950dd8`:

| File | Total | Comment | Blank | Code | Comment share |
|---|---|---|---|---|---|
| `lib.rs` | 8,049 | 2,553 | 451 | 5,045 | 32% |
| `filter.rs` | 1,796 | 434 | 120 | 1,242 | 24% |
| `planes.rs` | 1,541 | 581 | 68 | 892 | 38% |
| `cost.rs` | 345 | 226 | 15 | 104 | 65% |
| `estimator.rs` | 545 | 153 | 41 | 351 | 23% |

Most of this is load-bearing and must stay: measured constants with their calibration, correctness
arguments about `tight`/`loose`, the trivalent-NULL traps, and the "don't 'fix' this" notes guarding
against re-introducing a measured regression. `cost.rs`'s 65% is fine — it is a table of calibrated
constants, and the calibration *is* the content.

What should go is the slice that is **changelog rather than invariant**:

- [`and_child_rank`:2962-3005](../../card_engine/src/lib.rs#L2962) — a ~40-line comment on a 15-line
  function, narrating "bug 1", "bug 4", "bug 4 follow-up" in the order they were found. The
  invariant a reader needs is one sentence: *every dedicated `Not` arm here gates on the same
  predicate `narrow_rec` dispatches on, so the two cannot drift.* The bug archaeology belongs in the
  linked issue doc.
- [`tight_narrow_space`:2800-2835](../../card_engine/src/lib.rs#L2800) — two ~15-line paragraphs
  explaining why price and dates are *absent* from a match. Absence explained at that length reads
  as presence on a skim.
- [`card_range_popcount_applicable`](../../card_engine/src/lib.rs#L5842),
  [`COMPOSE_GATHER_MAX_CARD_FRACTION`](../../card_engine/src/lib.rs#L2099) — 12-13 line docs where
  the measurement is the point and the narrative around it is not.

**Distillation rule to apply:** keep the invariant and the number in the code; move the derivation,
the iteration history, and the rejected alternatives to `docs/issues/` and link. A comment should
tell a reader what they must not break. If it is telling them what happened in July, it is a
changelog and the repo already has one.

Worth doing as a pass *after* Batches A-C: those delete code that currently carries some of the
heaviest comments (the three compose grammars alone carry ~80 comment lines that collapse to one
classifier doc).

## What not to touch

- **`card_match_count` / `push_card_matches`** ([4115](../../card_engine/src/lib.rs#L4115),
  [4239](../../card_engine/src/lib.rs#L4239)). The mode × `existential_plane` matrix is the most
  duplicated code in the engine and the most obvious refactor target. Both carry measurements
  showing the unified-closure version regressed ~15% on `banned:modern`-shaped scans. Leave them,
  and keep their comments — this is exactly the "don't 'fix' this" case the density rule above
  exempts.
- **`ArchivedSortPermutations::get` / `get_inv`** ([1912](../../card_engine/src/lib.rs#L1912)) —
  near-identical, but the duplication is forced by the archived struct's field layout. Not worth a
  macro.
- **The `debug_assert!`-guarded invariants** throughout the paging code (`group_best` pre-sizing,
  the arith-tuple domain budget). They are cheap and they are the reason CI's debug build catches
  what release would not.

## Suggested order

1. **Batch B** (items 3-7) — one commit, mechanical, no behavior change. Unblocks nothing but is
   free.
2. **Batch C** (items 8-11) — one commit per item; each is a local extraction with an obvious
   before/after.
3. **Batch D** (items 12-16) — item 12 first (it is a correctness-of-intent bug in the ordering, not
   just a slow path), then the rest with micro-benchmarks.
4. **Item 1** — its own doc, its own PR, `plan_cost` output compared before/after.
5. **Batch E** — the comment pass, last, over whatever code survives.
