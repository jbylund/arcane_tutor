# Exact totals for PAIRS of low-cardinality values

`compose_printing_estimate`'s `And` folds with `m.min(cm)` — an intersection upper bound — so the most
selective leaf decides alone and every other conjunct contributes nothing. Every `f:X border:white`
estimates identically at 5,131 (`border:white`'s own count) against true totals of 658–5,072.

For two leaves from a low-cardinality dimension, a stored pair count is not a tighter bound: it is the
exact answer. The dimensions are small enough that the table is tens of kilobytes.

## Sizing it, and what pruning does

Four rounds of correction, each of which shrank it:

| basis | pairs | size |
| --- | --: | --: |
| all five dimensions, 32×4 format slots | 7,821 | 92 KB |
| real format cardinality (23 formats; only `legal`/`not_legal` worth pairing) | 3,705 | 58 KB |
| drop the frame tail | — | 23 KB |
| **selectivity floor: both values ≥ `STREAM_MIN_MATCHES`** | **879** | **13.7 KB raw** |

The floor is the useful rule. A pair is only worth storing if *both* values are broad enough that an
estimate about them can change a routing decision — below `STREAM_MIN_MATCHES` the sparse floor decides
the plan and min-over-singles is already within a small factor. Measured, that prunes 4.2× and removes
`layout` entirely, because only `normal` clears the floor:

| dimension | values | survive `≥ 1,024` |
| --- | --: | --: |
| border | 5 | 4 |
| rarity | 5 | 4 |
| layout | 14 | **1** |
| frame | 29 | 9 |
| legality (`legal` + `not_legal`) | 46 | 41 |

Counted in printings, which is the conservative side: cards ≤ printings, so a value with 1,500 printings
but 400 cards is kept — and that is exactly the one card-mode routing needs.

Realized archive cost is **42.6 KB**, above the 13.7 KB raw figure because rkyv's `HashMap` carries ~26
bytes per entry. Build cost +0.2 s.

## Two structural points

**Completeness over stored ids, not over all values.** Absence from `ValueTotals` is read as an exact
zero, so that table must cover everything. This one may prune — a miss just falls back to the `min`
bound. But every pair of *stored* ids is archived **including the zero ones**, so "both ids present"
means the answer is exact, possibly exactly zero. Storing only non-zero cells made a provably-empty pair
indistinguishable from a pruned one: `frame:2003 frame:1997` read 10,769 against a true 0.

**Partitions get disjointness for free.** Border, rarity and legality hold exactly one value per
printing, so two distinct values never co-occur — a rule (`leaves_are_disjoint`), not stored data.
`frame_data` is the exception: it is multi-valued (1–5 per printing) and `frame:2015 frame:legendary`
genuinely matches 10,321, so frame needs real same-dimension entries.

## The table is right

21 of 21 measured cells exact, in all three spaces, where card mode had been 2.8–10.1× over:

| query | printings | cards | artworks |
| --- | --: | --: | --: |
| `f:modern border:white` | 3,117 | 978 | 1,501 |
| `f:pauper border:white` | 2,683 | 858 | 1,274 |
| `f:vintage border:white` | 5,072 | 2,025 | 2,698 |
| `f:modern r:rare` | 25,708 | 6,518 | 10,950 |
| `r:rare border:white` | 1,330 | 625 | 772 |
| `frame:2015 border:black` | 58,156 | 22,169 | 28,930 |
| `f:modern frame:2015` | 51,925 | 17,750 | 27,322 |

Three or more leaves get a tighter bound rather than exactness — `min` over stored pairs:

| query | min over singles | min over pairs | truth |
| --- | --: | --: | --: |
| `f:modern r:rare border:white` | 5,131 (7.80×) | **1,330 (2.02×)** | 658 |
| `f:modern frame:2015 border:black` | 64,139 (1.36×) | **51,925 (1.10×)** | 47,332 |
| `f:pauper r:common border:black` | — | **1.01×** | 24,521 |

## And wiring it into the estimate makes routing worse

Interleaved A/B, 8 rounds, 1,368 queries: whole mix **1.010**, and the regression list is one shape:

| query | off | on | |
| --- | --: | --: | --: |
| `border:white border:black` | 9.0 µs | 220.5 µs | **24.5×** |
| `border:white border:black` (other configs) | 8.8–16.1 µs | 210–242 µs | 15–24× |
| `r:common r:rare` | 10.2 µs | 88.1 µs | 8.6× |

With `matches = 0`:

    GatheredScan     pred=  0.2u   meas= 199.3u   <- picked
    StreamedSelect   pred=  0.8u   meas= 192.0u
    PrintingCompose  pred=  1.9u   meas=   1.0u   <- actually best

**A result total is not a scan domain.** `eval_domain` and `scan_units` for the *materializing
alternatives* are derived from the match estimate, so an exact zero makes them free — but they still walk
their candidate set to discover the set is empty. Compose really is ~1 µs (AND two bitmaps, popcount) and
loses on predicted cost.

This is the same distinction that `exact_cards` vs `exact_total` draws in the compose acquire, and that
the printing-mode `result_total` change was careful about ("substituting the true match count there would
under-charge the scan"). It was violated one level up: the pair answer flows into `exact_cards` →
`est_cards` → `eval_domain`/`scan_all`.

## What it needs

`compose_printing_estimate` should return **two** numbers: the best available *result* estimate (exact
where the pair table answers) and a *candidate* bound (min over singles, which is what the alternatives
actually walk). `result_total` takes the first; `eval_domain` and `scan_units` take the second. Then the
disjoint cases price compose at 1.9 µs against alternatives charged for a real scan, and get the plan
right instead of 24× wrong.

Worth doing alongside: an exact total of **0 should short-circuit to an empty result** before routing at
all. `border:white border:black t:creature` scans 6,402 printings over 79 µs to return nothing, because
its acquire is `candidates` and never consults these tables — a filter containing two disjoint conjuncts
is empty regardless of what else it says.

## Status

Built, archived and measured; wiring switched off at `CARD_ENGINE_PAIR_TOTALS` (default 0) pending the
result-vs-candidate split above. The table's own numbers are all measured on the production corpus.
