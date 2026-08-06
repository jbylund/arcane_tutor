# A conjunct the candidate set proves was still re-verified per printing

**DONE — merged in [#843](https://github.com/jbylund/sylvan_librarian/pull/843)** (layer 11 of the
cost-model stack). Target 0.497, control 0.957, whole mix 0.718, p99 0.428. Nothing carried forward.

`o:this` runs in **52 µs** and examines **zero** printings. `o:this border:black` runs in **1,993 µs**.
Adding a predicate that cuts the result set from 55,004 to 49,278 made the query 38× slower.

## The cost is per candidate CARD, and has nothing to do with the added leaf

| query | cards | printings examined | µs | ns/card |
| --- | --: | --: | --: | --: |
| `o:this` alone | 19,968 | **0** | 67 | 3.4 |
| `o:this border:black` | 19,968 | 55,004 | 1,993 | 99.8 |
| `o:this cn>200` | 19,968 | 55,004 | 1,816 | 90.9 |
| `o:this frame:2015` | 19,968 | 55,004 | 1,972 | 98.8 |
| `o:this border:black cn>200 usd>1` | 19,968 | 55,004 | 1,923 | 96.3 |

Every pairing costs ~1.9 ms whatever the partner is, and adding two more printing-level leaves changes
nothing (36.2 → 37.0 → 35.0 ns/printing). The printing-level predicates are ~1–7 ns each. **The whole
cost is one card-level oracle-text evaluation per candidate, repeated once per printing of that card.**

## Tightness was one bit for the whole expression

`o:this` narrows tightly in card space — that is exactly why it examines no printings: `all_match_known`
fires and the residual never runs. Inside an `And`, `narrow_rec`'s `seal` does
`n.tight &= every_child_included`, so dropping `border:black` (broad, printing-space) makes the whole
result loose. `card_pass` then re-evaluates **every** conjunct per card, including the oracle contains
that membership in the candidate set already settles.

`card_pass` was already doing the right thing structurally: it evaluates each child with `printing: None`,
keeps only `PrintingDep` children in the per-printing residual, and drops settled ones. Card-invariant
hoisting was never missing. What was missing is that the *proof* — child *k* was fully represented in the
candidate set — was computed and then thrown away.

## The fix

`Narrowed` gains `proven: u64`, a bitmask over a top-level `And`'s children. A child is marked when its
own narrowing came back **tight AND card-space** and it actually got pushed: `and_all` intersects, and an
intersection is a subset of each input, so every card in the result satisfies each marked child.
`card_pass` skips those children — not into the residual either, since printings of one card cannot
disagree about a card-space fact. It is the `all_match` promotion argument at conjunct granularity.

Four things that have to be right, each of which would be a wrong answer rather than a slow one:

- **Card-space only.** A tight printing-space set says "this printing matches", not "every printing of
  this card does". The lone-printing branch returns 0.
- **The mask dies with the set.** `narrow_candidates_exact` discarding a too-broad set, or
  `prepare_candidates` dropping `candidate_cards`, clears it — the walk then visits cards the proof never
  covered.
- **Indices survive reordering.** `order_children_by_verify_cost` permutes children on every query with a
  residual, so it now permutes the mask with them (sorting an index permutation, stable, same resulting
  order as before). Clearing the mask there instead would forfeit the win on every query that reaches it.
- **Children are located by pointer identity**, not by tracking position through
  `fuse_and_range_children`'s regrouping and the ranking sort. A positional guess that slipped would drop
  a real predicate. Children past 64 are never marked: a re-verification, never a wrong answer.

The mask is *not* applied by blanking proven children out of the filter, which was the first idea and is
unsound: `PrintingCompose`, `PlanePopcountOrder`, `PrintingRangeScan` and `CardRangePopcount` all read
the same `filter` and never see the candidate set, so a filter with conjuncts removed would hand them a
weaker predicate.

`verify_cost_tier_unproven` charges the tier of what `card_pass` will actually evaluate. Without it the
model keeps pricing a conjunct nobody verifies, and a cost model that cannot see a change cannot route on
it.

## Measured

| query | before | after |
| --- | --: | --: |
| `o:this border:black` | 1,993 µs | **542 µs** |
| `o:this cn>200` | 1,816 µs | **498 µs** |
| `o:this border:black cn>200 usd>1` | 1,923 µs | **748 µs** |
| `o:this frame:2003` | 490 µs | **209 µs** |

Per printing: 36.2 ns → 9.9 ns.

Interleaved A/B behind `CARD_ENGINE_PROVEN_CONJUNCTS`, 8 rounds, 1,368 queries:

| subset | n | off | on | on/off |
| --- | --: | --: | --: | --: |
| mixed card+printing `And` TARGET | 218 | 128.1 ms | 63.7 ms | **0.497** |
| everything else CONTROL | 1,150 | 118.6 ms | 113.4 ms | 0.957 |
| whole mix | 1,368 | 246.6 ms | 177.1 ms | **0.718** |

p90 0.929, **p99 0.428** (2,227 µs → 952 µs). Biggest cells: `o:creature r>=common` 2,241 → 417 µs,
`o:target cn>200` 1,650 → 369 µs, `year>=2006 o:turn` 1,367 → 309 µs.

The one apparent regression, `name:s t:creature` at 1.68×, is within-query variance: its spread *inside
arm A* is 2.25×, and the other two sampled configs of the same query read 0.99.

### The estimates were wrong in the same place

| | predicted | measured | p/m |
| --- | --: | --: | --: |
| GatheredScan, before | 760 µs | 2,011 µs | 0.38 |
| StreamedSelect, before | 892 µs | 1,689 µs | 0.53 |
| StreamedSelect, after | 564 µs | 518 µs | **1.09** |
| GatheredScan, after | 678 µs | 719 µs | **0.94** |

The router had been picking the *slower* plan (GatheredScan at 2,011 against StreamedSelect's 1,689,
~320 µs of regret); it now picks StreamedSelect. Across the sampled shapes the pairs moved from 0.38–0.53
to 0.74–1.34.

## What this did NOT fix

- **`name:s border:black` is unchanged at ~1,160 µs**, and *not* for the reason first written here. This
  doc previously claimed its residual is "genuinely evaluated 60,705 times" and that the loops need the
  card verdict hung on a candidate set. Both are wrong: `card_pass` returns the card-level children as
  settled and only the `PrintingDep` ones in `residual`, and `push_card_matches` verifies **only that
  residual** per printing. The card/printing partition has always been there.

  What the 1,160 µs actually is, measured against `name:s` alone (472 µs, 31,508 cards, zero printings
  examined — 15.0 ns/card for the name residual):

  | query | total | minus card part | per printing |
  | --- | --: | --: | --: |
  | `name:s usd>1` | 1,068 µs | 596 µs | 9.8 ns |
  | `name:s cn>200` | 1,074 µs | 602 µs | 9.9 ns |
  | `name:s border:black` | 1,158 µs | 686 µs | 11.3 ns |
  | `name:s frame:2015` | 1,160 µs | 688 µs | 11.3 ns |

  The leaf's own test is 0–1.5 ns of that; the other ~10 ns is the cost of *examining a printing at all*.
  So the remaining cost is the per-printing WALK, not any redundant evaluation — and it is unavoidable for
  a plan that verifies row by row, because a printing-varying conjunct means the exact total requires
  testing every printing under every matching card.
- **`name:s border:black` also still routes wrong** — GatheredScan at 1,170 µs measured against
  StreamedSelect's 975 (predicted 1,470, p/m 1.51).
- The earlier diagnosis in
  [the `is:`/`frame:` doc](local-engine-is-frame-predicates.md) — that broad printing-space partners
  under a card-space driver are wrongly declined, and that a stored bitmap's AND would be nearly free —
  **was measuring the wrong thing.** Narrowing with the border bitmap would not have helped: the pass over
  printings was never the cost. That item is withdrawn.

## Status

Implemented and measured on the production corpus. Soundness is checked two ways:
`a_proven_conjunct_holds_for_every_candidate` asserts every marked child evaluates `Tri::True` for
*every* candidate (not just a returned page, where a rare false positive would hide), and the mask was
confirmed to fire inside `fuzz_row_identity_matches_reference` and six other differential tests by
temporarily asserting it never did — so the end-to-end row-identity comparison covers it.
