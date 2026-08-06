# Merge against the sorted candidate list instead of re-deriving membership — 0.17 ns where 5.9 is paid

Status: **designed and measured, not implemented.** Filed as
[#857](https://github.com/jbylund/sylvan_librarian/issues/857). Split out of
[#856](00856-engine-compose-membership-bittest.md), which owns the defect, the correctness gate and the shared
population measurement. Read that first; this doc is one alternative route and does not restate it.

**The split is along reach, not preference.** #856 restores the discarded membership as a **printing bitmap**,
probed per printing — general, works for any visit order. This is the cheaper special case that covers the
whole *measured* population but cannot serve every visit order. Both are worth having.

## The narrowing already hands over a sorted list

`indexes.set_codes` is a printing-space `TagIndex` — set code → sorted printing ids — and
`narrow_candidates_exact` turns `set:X` straight into `Narrowed::tight(Candidates::Printings(...))`
(`card_engine/src/lib.rs`). On the dominant family — `set:` is **63%** of the population by printings
examined — the exact answer is **already a sorted `Vec<u32>`**, and it is still live as `raw_candidates` at
exactly the point #856 would scatter it into a bitmap:

```rust
let (raw_candidates, residual_exact, proven_conjuncts): (Option<Candidates>, bool, u64) =
    narrow_candidates_exact(filter, indexes, offsets, cards);
// Captured before the flattening below consumes it — see PreparedCandidates::narrowed_repr.
```

So carry the list itself and walk it with a **two-pointer merge** against the card spans. No bitmap, no
allocation, no scatter pass.

## Cheaper at every density, and structurally so

`card_engine/src/bench_membership_check.rs`, modelled on the measured production shape — 234 candidate cards,
13.6 printings each, 1.56 matching. ns per printing in a visited span:

| matches/card | density | bitmap probe (#856) | **merge (this)** | bitmap build |
| --- | --: | --: | --: | --: |
| 1 | 7% | 0.33–0.80 | **0.06–0.14** | 0.3 µs/query |
| **2 (measured 1.56)** | **14%** | **0.38–0.88** | **0.06–0.17** | **0.3–0.5 µs/query** |
| 4 | 29% | 0.52–0.97 | **0.13–0.22** | 0.9–1.7 µs |
| 7 | 50% | 0.64–1.09 | **0.28–0.31** | 3.5–4.4 µs |
| 14 | 100% | 0.42–0.98 | **0.39–0.64** | 5.5–8.5 µs |

**Read the ratio, not the absolutes.** These kernels run at 0.1–1 ns per operation, where variation is a large
fraction of the value: the production row read 0.17 in one version of the bench and 0.06–0.08 across three runs
of the next, with the probe moving 0.88 → 0.37–0.43 alongside it. The **merge:probe ratio held at 5.0× and
5.4×**, and both routes stay an order of magnitude under the 5.9 ns residual. The figures quoted here are the
**pessimistic end**, so the 6.47× is conservative.

Not a constant-factor win: the merge is **O(matches)** where a probe is **O(span)**, so it never touches the
86% of printings that do not match. It also avoids allocating and zeroing the bitmap — 0.5 µs per query at
production scale, ~2% of a 22 µs query, which the probe route pays before it can probe anything.

End to end on the shared population, against the 5.9 ns/printing residual both would replace:

| route | saves | of routed time | speedup on the population |
| --- | --: | --: | --: |
| **merge, 0.17 ns (this)** | **6.9 ms** | **84.5%** | **6.47×** |
| bitmap probe, 0.88 ns (#856) | 6.1 ms | 73.9% | 3.84× |

## Why this cannot replace #856

The merge needs the walk to visit pids in **globally ascending order**, because it carries a single forward
pointer.

- **`GatheredScan` — holds.** `cards_of_printings` yields ascending cids (both its paths: the small one dedups
  consecutive cards from a sorted printing list, the large one goes through `bitmap_card_ids`, which is
  ascending by construction), and each card's printing span is contiguous. So the walk visits pids ascending.
- **`StreamedSelect` — does not hold.** It walks a permutation in *sort* order, so a forward pointer would
  skip past matches and silently drop rows. Only a bitmap works there.

Every query in the measured population picked `GatheredScan` — **377 of 377** — so this covers the measured
case, and #856 stays as the general route for the plan that was never chosen there. That also means #856's own
value is currently **unmeasured**: its population is the one where `StreamedSelect` is picked on a compose
acquire, which this sample never produced.

## Scope — do not read the 6.5× as a latency win

Owned by [#856](00856-engine-compose-membership-bittest.md#it-touches-no-slow-queries-and-that-is-structural)
and summarised here only so this doc cannot be read in isolation as a bigger win than it is: the population is
1.3% of realistic queries and **1.22% of all routed time**, and it contains **no slow queries** — its maximum
is 78.5 µs against the overall p99 of 216 µs and max of 1.1 ms. That is structural, because a tight narrowing
is what makes a query fast in the first place.

## The correctness gate is #856's, unchanged

The bitmap-or-list covers the **narrowed subexpression only**, so an ANDed existential plane (legality) still
needs its per-printing check. That reasoning is identical for both routes and lives in
[#856](00856-engine-compose-membership-bittest.md#the-correctness-constraint-that-decides-the-gate).

One addition specific to this route: **the merge is order-dependent, so getting the plan gate wrong is a
wrong-answer bug, not a slow one.** Assert the ascending-order precondition in debug rather than relying on
the `GatheredScan`-only gate holding through future refactors — the same discipline `sorted_ids` got in #844.

## One measurement trap, recorded

An early model strided over *all* cards, which made the merge advance its pointer through pids belonging to
skipped cards and read **3.75 ns** instead of 0.17 — a 22× error, in the direction that would have killed the
idea. That cannot happen: `card_ids` is derived **from** the candidate list, so every visited card holds at
least one match and the list contains no pid outside a visited span. The same model used the corpus-average
3.09 printings per card where visited cards really hold 13.6, understating span work 4×.

## Verifying it

Rows first, timings second — this changes what comes back if the order precondition is ever violated. The
row-diff harness from the sparse-gather experiment captured totals and page rows over **127,640
compose-acquire queries**; compare before/after across all three distinct-ons and both prefer settings, since
the gate interacts with per-printing-varying fields
([the repair pattern](reference-engine-printing-varying-plane-repair-pattern.md)).

```bash
cargo test --release bench_membership_check -- --ignored --nocapture
.venv/bin/python scripts/bench_membership_waste.py \
    --corpus benchmarks/bitplanes/corpus.jsonl --shm /tmp/membership.store --sample 30000 --mode realistic
```

## Related

- [#856](00856-engine-compose-membership-bittest.md) — the defect, the gate, and the general bitmap route.
- [#852](00852-engine-compose-acquire-p3-p4-ranking.md) — the routing error on this same acquire, and where
  the engine's actual slow tail is.
- [#730](00730-engine-popcount-skip-walk.md) — the machinery the permutation-order route would use. Its own
  subject is deep pagination; membership under a residual is a **second consumer**, recorded there along with
  the measured rejection of sorting (2.4–2.9 µs/query against 0.2–0.3 for a scatter) and the note that its
  "why deferred" reasoning covered only the pagination case.
