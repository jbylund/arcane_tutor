# The popcount-skip scatter CAN select the page directly — but measured, it's a net loss

[local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md)
prototyped a three-phase design for all three `unique` modes: scatter matches into rank-ordered
64-card blocks, skip by block-popcount to find the target block, then re-walk that one block's cards
to emit. This doc started as a proposal to collapse that into one phase — read on for why, once
built and measured (see "Measured" below), that turned out to be the wrong call.

## The one-phase version

The scatter already visits every match once (`O(popcount(pbits))`). If, instead of just tallying a
block-weight count, it maintains a structure holding the *k* = `offset + limit` matches with the
smallest sort keys seen so far, then after one pass that structure holds — exactly, not
approximately — the rows the page needs: pop them in order, skip the first `offset`, and the
remaining `limit` are the page. No separate skip phase, no separate re-walk. `Card`/`Artwork` still
need the existing per-card finalization (best-`prefer_score` printing, or distinct
`artwork_group_id` representatives) before a row is eligible to enter the structure — that part is
unchanged from the current prototype's `finalize_artwork_card`-style logic.

This looked like a real structural win regardless of which structure implements "hold the *k*
smallest" — removing the separate skip+re-walk phases sounds like pure upside. Measured below, it
isn't: those two phases were doing useful work (keeping the expensive part bounded by `limit`
instead of `k`), and collapsing them costs more than it saves.

## Which structure: two candidates, one already proven in this codebase

**A bounded max-heap.** Push each candidate row; once the heap holds *k* entries, only push when the
new row sorts before the current maximum, evicting it. Standard bounded top-*k*, correct by
construction. Cost is `O(popcount(pbits) · log k)` — cheap shedding matters here specifically because
most candidates, once the heap has filled with genuinely small keys, will fail a cheap
"is this even smaller than the current max" check before paying for the O(log k) heap update.

**`GatherSelect`'s existing pattern**, already implemented and tested for the unordered `GatheredScan`
path (`card_engine/src/lib.rs`): append candidates into a plain buffer; cheaply drop anything
`>= cutoff` as it's appended (`absorb`'s compaction pass); once the buffer grows `GATHER_PRUNE_CHUNK`
past *k*, batch-prune with `select_nth_unstable_by` (quickselect, `O(n)` average) and truncate,
tightening `cutoff`. No live heap at all — the "keep only the *k* best" step happens in occasional
large batches instead of a per-item structure update.

**Prior, directly relevant evidence points at the batch-prune shape.** [#730](00730-engine-popcount-skip-walk.md)
(the filed issue this whole prototype arc has been implementing) already measured a closely related
choice in this same code area: keying each candidate by its card's permutation position and
*sorting* it, versus scattering into a bitmap directly. The sort-based version — `O(k log k)`,
comparison-heavy, non-sequential access, the same general shape a per-item heap has — measured
**2.4–2.9 µs against 0.2–0.3 µs for the scatter**, widening to **23 µs against 5.5 µs** at full
density. Not the identical problem (that was membership testing, not top-*k* selection), but the same
family of shape, and it lost by 8–10x. `GatherSelect`'s design already reflects the same lesson: cheap
per-item rejection plus rare, amortized batch work over a live per-item structure.

That is suggestive, not conclusive — a top-*k* selection isn't a sort, and a heap's `O(log k)` is not
`O(k log k)` — so the honest position is the user's own: **measure before deciding**, not assume the
prior result transfers.

## Measured: is the already-shipped `gather_composed_page` already the answer?

Before building anything, worth checking whether `ComposePaging::Gather`'s existing executor
(`gather_composed_page`, `card_engine/src/lib.rs`) — which already runs `GatherSelect`'s append +
cheap-shed + batch-`select_nth_unstable_by`-prune pattern, keyed by `sort_key_bits` rather than a
permutation, so it needs no permutation at all — already *is* the one-phase selector this doc
proposes building. `compose_paging_with_total` never lets it compete against `Perm` today (it
returns `Perm` unconditionally whenever a permutation exists for the sort column), so this had never
actually run in that regime and the question was open.

Checked directly: called `gather_composed_page` on the same permutation-available Card-mode queries
already used for the three-phase prototype, both for correctness (360-case random-bitmap sweep
across density/sort-column/direction/offset, row-identity against `walk_grouped_page`, all passing)
and for performance (same real-corpus `otag:triggered-ability` / `keyword:flying` offset sweep,
same process, same corpus, same test run as the three-phase prototype's own bench for an
apples-to-apples number):

| offset | `walk_grouped_page` | 3-phase prototype | `gather_composed_page` |
|---|---|---|---|
| 0 | 1.7 µs | 71.9 µs | 179.2 µs |
| 2,000 | 34.2 µs | 55.2 µs | 212.6 µs |
| 8,000 | 109.6 µs | 53.0 µs | 211.1 µs |
| 15,000 | 257.7 µs | 61.1 µs | 180.3 µs |
| 25,000 | 254.3 µs | 58.7 µs | 150.3 µs |

Both alternatives are flat in offset as expected — the difference is the flat *level*.
`gather_composed_page` beats `walk_grouped_page` only past offset ≈ 10,000-12,000 (vs. the
three-phase prototype's ≈ 4,000), and even past its own crossover it is **2.5-5x slower in absolute
terms than the three-phase prototype**, not merely later to cross even.

**Conclusion: no, it is not already the answer, and the gap is not close.** It is correct — the
algorithm shape (append + cheap-shed + batch-prune) is fine — but `sort_key_bits`'s generic
tuple-based key extraction plus a comparator-driven `select_nth_unstable_by` costs measurably more
per candidate than a raw scatter into rank-indexed 64-bit words. This doesn't resolve the heap vs.
batch-prune question this doc asks — it rules out "just reuse `GatherSelect` unmodified," not batch
pruning in general. A *specialized* batch-prune over plain `u32` permutation ranks (no tuple key, no
dynamic comparator) is still a live candidate against a heap; only the "build nothing, flip the
decision" option is closed off. The build-both plan below stands.

## Measured: does the one-phase collapse itself win, once built properly?

Built both candidates for `Mode::Card` (`walk_card_page_via_popcount_heap`,
`walk_card_page_via_popcount_batch_prune`, `card_engine/src/lib.rs`), verified against
`walk_grouped_page` the same way as the three-phase prototype (360-case random-bitmap sweep, row
identity, all passing), then measured all four strategies together in one test run
(`compose_card_one_phase_selectors_vs_walk`) against the same real-corpus
`otag:triggered-ability` offset sweep:

A first version tracked each candidate card's best-`prefer_score` printing *during* selection — the
natural reading of "the scatter selects the page directly." That version lost to the three-phase
prototype by 3-8x at every offset, because it paid `prefer_score` for every one of
`popcount(pbits)` matching printings, not just the page's own `limit` cards. The fix: selection only
ever needs a card's RANK to decide whether it's in the top-*k* — never its printings. Deferred the
best-printing pick to a `best_matching_printing` helper called only for the final page's `limit`
cards (mirroring the three-phase prototype's own bounded emit phase), and re-measured:

| offset | `walk_grouped_page` | 3-phase prototype | heap | batch-prune |
|---|---|---|---|---|
| 0 | 1.9 µs | 65.6 µs | 34.6 µs | 37.8 µs |
| 500 | 13.7 µs | 51.6 µs | 56.4 µs | 44.0 µs |
| 2,000 | 35.5 µs | 63.0 µs | 121.3 µs | 60.3 µs |
| 8,000 | 131.3 µs | 49.8 µs | 322.5 µs | 108.9 µs |
| 15,000 | 313.6 µs | 70.4 µs | 423.0 µs | 141.3 µs |
| 25,000 | 318.2 µs | 56.1 µs | 429.0 µs | 136.5 µs |

Removing the `prefer_score` bug closed most of the gap, but a real one remains and it doesn't close
with more engineering: **both one-phase selectors' cost grows with `k = offset+limit`, and the
three-phase prototype's does not.** The three-phase design's scatter does one thing per match —
flip a dedup bit, `O(1)`, independent of `k` — and defers everything expensive (the skip-scan, the
per-card printing pick) to bounded, `k`-independent or `limit`-bounded work. A live top-*k*
structure cannot do that: maintaining "the *k* smallest seen so far" against candidates arriving in
an order uncorrelated with rank (pid order, not rank order) means the number of candidates that
actually update the structure scales with *k* itself (each of the ~N arrivals is accepted with
probability roughly `k / i` at arrival position `i`, so total accepted work is `O(k · ln(N/k))`),
not merely `O(log k)` per candidate as the heap's local complexity suggests. That is exactly the
`k`-dependence the popcount-skip idea exists to avoid in the first place — collapsing to one phase
reintroduces it.

Batch-prune is decisively the better of the two — flatter, and 2.4-3.1x cheaper than the heap at
every offset past 500, confirming the doc's own suspicion and #730's sort-vs-scatter lesson: cheap
append + rare batch work beats a live per-item structure. But it still loses to the three-phase
design by ~2.4x at offset 25,000, and the gap does not shrink as offset grows — if anything it's
roughly constant past offset 8,000, meaning there is no larger offset at which batch-prune would
overtake the three-phase design for this query's density.

**Conclusion: don't collapse to one phase.** The three-phase design
(`local-engine-compose-perm-popcount-skip-prototype.md`) already separates cheap counting from
bounded materialization, which is the actual reason it's fast — "select the page directly in one
pass" sounded like it would remove overhead, but it removes a separation of concerns that was
carrying the performance instead. The heap-vs-`GatherSelect` question this doc opened with is
answered (batch-prune wins, decisively), but the premise question — is a one-phase collapse worth
building at all — is answered no. Kept as `#[cfg(test)]` prototypes with correctness tests, same as
the repo's other measured-and-not-adopted candidates, so the negative result and its cause don't
need re-deriving.

## Related

- [local-engine-compose-perm-popcount-skip-prototype.md](local-engine-compose-perm-popcount-skip-prototype.md)
  — the three-phase design that wins; this doc's attempt to collapse it lost, measured.
- [00730-engine-popcount-skip-walk.md](00730-engine-popcount-skip-walk.md) — the filed issue this
  whole arc implements; carries the sort-vs-scatter measurement this doc leans on.
- `GatherSelect` / `prune_to_smallest` / `select_page` / `gather_composed_page`
  (`card_engine/src/lib.rs`) — the existing, tested batch-prune pattern and its shipped executor;
  measured above as correct but 2.5-5x slower per candidate than the three-phase prototype, ruling
  out "reuse as-is" but not batch-pruning in general — confirmed separately when the *specialized*
  batch-prune built for this doc still lost to the three-phase design on its own.
- `walk_card_page_via_popcount_heap` / `walk_card_page_via_popcount_batch_prune` /
  `best_matching_printing` (`card_engine/src/lib.rs`) — the two one-phase prototypes this doc's
  question led to building, and the shared bounded-emit helper that fixed their first (much slower)
  version's `prefer_score` mistake.

## Status

**Measured, not adopted.** Both one-phase candidates were built, verified correct against
`walk_grouped_page`, and benchmarked against the three-phase design on the same real-corpus offset
sweep. Batch-prune beats a live heap decisively (answering this doc's original question), but
neither beats the three-phase design in the offset range popcount-skip exists to serve — a live
top-*k* structure's cost grows with `k = offset + limit`, which is exactly the dependence the
three-phase design's cheap-count/bounded-materialize split avoids. Recommendation: keep the
three-phase design as the one to eventually wire in (per `local-engine-compose-perm-popcount-skip-
prototype.md`'s own open items), not this one-phase collapse.

That "how do we decide when to use it" question turned into its own doc:
[local-engine-compose-perm-sigma-decision-rule.md](local-engine-compose-perm-sigma-decision-rule.md)
— a `sigma` safety margin over the no-clumping model, measured as the best balance found between
`walk_grouped_page` and the three-phase walk this doc settled on.
