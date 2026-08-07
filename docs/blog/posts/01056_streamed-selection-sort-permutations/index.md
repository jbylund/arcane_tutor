---
title: "We Were Quickselecting 31,000 Matches to Return 100"
date: 2027-02-16
publishDate: 2027-02-16
tags: ["rust", "performance", "query-engines", "pagination", "benchmarking"]
summary: "Half the cost of our broad queries was ranking results nobody would see: sort keys, prefer scoring, and a quickselect over every match, to keep 100. We split match from order — count matches sequentially, then walk a precomputed permutation and touch only page cards. The broad-query tail got 1.66x faster, deep pagination became free, and exact totals never wavered."
---

After we indexed the last text field in our Rust card-search engine, the slow-query survey stopped pointing at *finding* matches and started pointing somewhere stranger: the engine was spending up to two-thirds of its time ranking results nobody would ever see.
A query matching 31,000 cards with `limit=100` computed 31,000 sort keys, prefer-scored 31,000 cards' printings, and ran a quickselect over all of it — to discard 30,900 rows.
This post is about splitting selection into a match phase and an order phase, so that ordering work only ever touches the hundred cards on the page: 1.66× across the broad-query tail, 4× on the worst configurations, and exact totals and exact pagination preserved throughout.

## Measuring the invisible work

Ranking cost hides inside query time, so we separated it with a deliberately broken query: append an unindexable conjunct that matches nothing (`power+toughness>99` — arithmetic never narrows, so the scan shape is preserved while the match count drops to ~zero).
The full query pays evaluation plus ranking; the broken variant pays evaluation alone.
On the live 97,206-printing corpus:

| config | full | eval-only | emission share |
|---|---|---|---|
| `t:creature`, unique=card, default prefer | 0.253 ms | 0.182 ms | 28% |
| `t:creature`, card, prefer=usd-high | 0.355 ms | 0.187 ms | 47% |
| `rarity>=common`, card, usd-high | 0.999 ms | 0.529 ms | 47% |
| `rarity>=common`, artwork mode, default | 1.412 ms | 0.531 ms | 62% |
| `rarity>=common`, artwork, usd-high | 1.521 ms | 0.533 ms | 65% |

The pattern: the more work the engine does *per match* — non-default prefer orders score every matching card's printings; artwork mode groups every matching card by illustration — the more of the query is spent on rows outside the page.
In the slow-query survey, non-default prefers and artwork mode accounted for 24 overlapping tags of the slowest 60 configurations.
This was the largest remaining cluster, and no index could touch it: these queries were already finding their matches efficiently.

## The idea, and the constraint that almost killed it

The obvious fix is early termination: store cards in result order and stop after filling the page.
Our results promise an exact total (`"1,204 results"`), which seems to forbid that — you can't know the total without evaluating everything.
The reframe that unlocked it: exact totals require evaluating the *predicate* everywhere, but they don't require *ranking* anything.
Skip the first `offset` matches, take `limit`, and get the total from counting — the expensive per-match work (sort keys, prefer walks, grouping, quickselect) is only needed for the rows actually returned.

That works for one physical order.
The second reframe covers every sort column: don't reorder the store, precompute a **permutation** per (column, direction) — card ids sorted by (sort key, tiebreaks) — and walk it at query time.
Five of our seven sort columns read card-level values (edhrec rank, cubecobra, cmc, power, toughness), so ten permutations cost [~1.26 MB](https://github.com/jbylund/sylvan_librarian/blob/d3c5e58/card_engine/src/lib.rs#L1164-L1188).
The other two (rarity, price) key off the prefer-*chosen printing*, which is query-dependent — no permutation can be exact, and they keep the old path.

The third reframe is the one with a measurement behind it: **never evaluate in permutation order.**
When we added the price index we measured candidate evaluation by random access at roughly 2× the per-element cost of a sequential scan — and walking a permutation is random access.
So the match phase stays sequential (store order, cache-friendly), writing per-card match counts into a reused buffer; the permutation walk afterward touches only the counts array (126 kB, cache-resident) until it reaches page cards.
Skipping to `offset=5000` is arithmetic over counts — deep pagination measured at the same cost as page one.

## What the match phase must count

`unique=card` needs existence per card — a short-circuiting walk, cheaper than what the old fused loop did.
`unique=printing` needs passing-printing counts.
`unique=artwork` needs *distinct illustrations with a passing printing* per card — and this is where exact totals genuinely cost something, because grouping is part of the count, not part of the ranking.
More on that below.

The [order phase](https://github.com/jbylund/sylvan_librarian/blob/d3c5e58/card_engine/src/lib.rs#L2106-L2214) then walks the permutation: skip whole cards while `offset` remains, emit page cards through the same per-card logic the old path used (prefer walk, grouping), stop at `limit`.
A planner constant ([`STREAM_MIN_MATCHES = 1024`](https://github.com/jbylund/sylvan_librarian/blob/d3c5e58/card_engine/src/lib.rs#L2022)) keeps small match sets on the old gather-and-quickselect path, where they're already microseconds and byte-identical to the previous behavior — the same measured-constant-instead-of-cost-model approach as our narrowing guards.

## The benchmark pushed back twice

The first benchmark round found two regressions we hadn't predicted.
Selective queries (`t:goblin`, 501 matches) got 25% *slower*: they paid the match phase's 126 kB counts allocation and a double walk before falling back to the gather path.
The fix was embarrassingly simple — if the index-narrowed candidate list is already at or below the streaming threshold, the match count can't exceed it either, so dispatch straight to the fused path; plus a thread-local buffer.
And the non-streamable `usd` orderby lost ~30% because extracting the per-card emission into a shared helper broke inlining in the hot loop; `#[inline(always)]` recovered most of it (a ~19 µs residual on one configuration remains, documented in the PR).

The second round showed artwork mode undershooting the prediction — 1.5× where the emission shares promised 2–3×.
The probe had lumped illustration-grouping into "emission," but exact totals force the match phase to keep the grouping walk; only the scoring and quickselect were eliminable.
The recovery: when the card-level pass proves every printing matches — true for every card-level filter — the distinct-illustration count is a **build-time constant** (one u16 per card, 63 kB), and the walk disappears entirely.
`t:creature` artwork went from 1.5× to 2.84× with that one table.
For printing-dependent filters (`usd<50` artwork), the walk is irreducible and those rows sit at 1.2–1.3× — the follow-up (dense per-card group ids, so dedup becomes a bitmask instead of UUID comparisons) is filed, not shipped.

## Results

Same protocol as always: live corpus, 15 warmup iterations then a 2.5 s timed window per config, M-series MacBook, [PR #632](https://github.com/jbylund/sylvan_librarian/pull/632).
35 configurations: six broad filters × unique modes × prefers, plus direction, deep offsets, non-streamable orderbys, and selective controls.

| | geomean |
|---|---|
| **streamed cluster (29 configs)** | **1.66×** |
| selective controls (4) | 1.09× — parity; the planner keeps them on the old path |
| non-streamable `usd` orderby (2) | 0.97× |
| all 35 | **1.53×** |

| highlights | before | after | |
|---|---|---|---|
| `rarity>=common`, card, usd-high | 1.647 ms | 0.420 ms | 3.92× |
| `usd<50`, card, usd-high | 1.468 ms | 0.450 ms | 3.26× |
| `t:creature`, artwork | 0.389 ms | 0.137 ms | 2.84× |
| `t:creature`, card, offset +5000 | 0.253 ms | 0.158 ms | 1.61× — deep pages cost the same as page one |
| `t:goblin` (selective control) | 0.063 ms | 0.063 ms | 1.00× |

One semantic footnote, stated plainly: result ordering can differ from the old path only inside blocks of cards tied on *both* the sort column and edhrec rank, where the old tiebreak used the query-chosen printing's prefer score and the permutation canonicalizes on the store-preferred printing.
A 72-configuration equivalence test — twin stores differing only in the presence of permutations, crossed over uniques, prefers, orderbys, directions, and offsets — returns identical pages, and the engine/SQL parity suite is green; but on adversarial data the tie order inside those blocks is defined differently, and we'd rather say so than have someone find it.

## What this exposes next

Streaming removed the work after matching; the floor that remains *is* matching — 31.5k filter-tree evaluations touching a 250-byte struct's cache line to, in the worst case, test one bit.
A color query now spends its whole budget on delivery: the comparison is one AND, the driver loop around it is 8 ns per card.
The fix for that is a different transposition — bitplanes for the low-cardinality dimensions, one 4 kB bitset per color/type/format, so the match phase itself becomes a handful of vectorized word operations feeding the same order phase.
That's filed as the successor, and it's the same lesson one level down, which is really the only lesson this project keeps learning: the data you touch per row matters more than the work you do per row — and the fastest per-row work is the row you never touch.
