# `f:modern border:white` picks a 100 µs plan over a 44 µs one, and the whole family estimates identically

**DONE — merged in [#844](https://github.com/jbylund/sylvan_librarian/pull/844)** (layer 12 of the
cost-model stack), alongside the pair table that unblocked it. `f:modern border:white` goes
149.0/99.5/134.1 µs (printing/card/artwork) to 76.4/102.1/83.2.

Defect 1 (the `stream_scan_units` correction scoped on the wrong question) shipped. Defect 2 resolved
itself once the pair table made the two-leaf card total exact. **Item (1) of "What to do" — the
`DeclineSparseExact` cliff that discards a compose build already paid for — is refiled as
[#848](https://github.com/jbylund/sylvan_librarian/issues/848)**: the pair table removed the population
that was hitting it, not the cliff.

`f:modern border:white` measures **100.5 µs** on the `StreamedSelect` the router picks and **43.8 µs** on
the `PrintingCompose` it passes over. The same holds across the family, and the reason is that the cost
model cannot tell its members apart.

## Every `f:X border:white` gets identical features

| query | eval_domain | matches | scan_units | stream_scan_units | broadcast |
| --- | --: | --: | --: | --: | --: |
| `f:modern border:white` | 2,755 | 5,131 | 17,848 | 2,755 | 28,518 |
| `f:pauper border:white` | 2,755 | 5,131 | 17,848 | 2,755 | 33,020 |
| `f:vintage border:white` | 2,755 | 5,131 | 17,848 | 2,755 | 212 |
| `f:future border:white` | 2,755 | 5,131 | 17,848 | 2,755 | 14,413 |
| `f:modern r:rare border:white` | 2,755 | 5,131 | 17,848 | 2,755 | 28,518 |

Only `broadcast_printings` differs, and it feeds `PrintingCompose`'s arm alone — so `StreamedSelect` is
predicted at 50.3 µs and `GatheredScan` at 120.2 µs for all five, against measured `StreamedSelect` of
82–181 µs. Note the last row: adding `r:rare` changes nothing either.

`5,131` is `border:white`'s own printing count. `compose_printing_estimate`'s `And` arm folds with
`m.min(cm)` — an intersection **upper bound** — so the most selective leaf wins and every other conjunct
contributes nothing. Against the true printing totals that reads 1.01x on `f:vintage`, 1.65x `f:modern`,
1.91x `f:pauper`, and **6.50x** `f:future`: the error scales with how selective the ignored conjuncts are.

## Defect 1: the `stream_scan_units` correction is scoped on the wrong question

`stream_scan_units` carries a correction for legality: `card_match_count` answers from span arithmetic
for every card `card_pass` resolves at card level, so P3 examines only the divergent remainder. Measured
at 0.10–0.26x of P4's count on bare legality filters, and correct there.

It is gated on `filter_touches_legality(composed)`. But the argument is about what `card_pass` can
**settle**, and legality being card-level is only decisive when it is the *only* thing left to verify.
One printing-varying partner makes `card_pass` return `PrintingDep` for every card, and P3 then walks the
whole span like P4:

| query | charged | P3 realized | |
| --- | --: | --: | --: |
| `f:vintage border:white` | 2,755 | 19,737 | 7.2x under |
| `f:modern border:white` | 2,755 | 13,786 | 5.0x under |
| `f:pauper border:white` | 2,755 | 10,965 | 4.0x under |
| `f:future border:white` | 2,755 | 5,353 | 1.9x under |

`touches_printing_field` already treats `Legality` as card-level (it ranks by the common case), so
`filter_touches_legality(composed) && !touches_printing_field(composed)` is the right condition and a
bare legality filter still takes the divergent-share arm. Implemented behind
`CARD_ENGINE_LEGALITY_SCAN_SCOPE`, **now default on** — it shipped off until the pair table fixed defect 2
below.

Per query, with the scope fixed, all four flip to the correct plan:

| query | before | after |
| --- | --: | --: |
| `f:modern border:white` | 100.5 µs (StreamedSelect) | **43.8 µs** (PrintingCompose) |
| `f:vintage border:white` | 185.0 µs | **3.0 µs** |
| `f:pauper border:white` | 83.0 µs | **65.8 µs** |
| `f:future border:white` | 97.0 µs | **37.6 µs** (GatheredScan) |

## Defect 2: card mode is picked into a plan that refuses to run

Fixing defect 1 alone nets a **wash** — interleaved A/B, 8 rounds, 1,374 queries: target 1.008, control
0.988, whole mix 0.990. The same queries appear in both the improvement and the regression lists, split
by mode:

| mode | scope off | scope on | |
| --- | --: | --: | --- |
| printing | 153.9 µs | **77.0 µs** | 2.0x faster |
| card | **103.0 µs** | 163.4 µs | 1.6x SLOWER |
| artwork | 134.4 µs | **84.5 µs** | 1.6x faster |

Card mode is not "compose is slower there". Compose is **picked and produces no trial**:

    card:  PrintingCompose  pred=67.5u  trials=0  declined=9  picked=True  paging_taken='DeclineSparseExact'

Compose builds the bits, computes the exact total, finds it under `STREAM_MIN_MATCHES` (1,024), and
bails — after paying the build. The fallback then runs a second plan on top of that.

And the trigger is defect 1's own root cause, the `min` fold:

| query (card mode) | est cards | TRUE total | est/true | |
| --- | --: | --: | --: | --- |
| `f:modern border:white` | 2,755 | 978 | 2.82 | declines |
| `f:pauper border:white` | 2,755 | 858 | 3.21 | declines |
| `f:vintage border:white` | 2,755 | 2,025 | 1.36 | runs |
| `f:modern r:rare border:white` | 2,755 | 273 | **10.09** | declines |

The estimate says 2,755 for every one, comfortably above the floor, so the router predicts compose will
run. Three of the four are actually below it.

## What to do, in order

1. **Make `DeclineSparseExact` not throw away paid work.** The sparse floor exists because compose is not
   worth building for a tiny result — but at the point this fires the bits are *already built*, and
   finishing the page from them is a page-sized walk. Declining after the exact total is known is
   strictly worse than completing. The decline belongs before the build, on the estimated total, where
   `ComposePaging::Decline` already lives.
2. **Then turn on `CARD_ENGINE_LEGALITY_SCAN_SCOPE`** and re-run the A/B. Printing and artwork already
   measure 2x and 1.6x; card should stop regressing once the decline is not a cliff.
3. **Improve the `And` fold** — independence capped by the existing `min` bound. On this family that
   reads 1.00 / 1.25 / 0.63 / 1.16 against `min`'s 1.01 / 1.65 / 1.91 / 6.50. It does NOT fix the card
   estimate on its own (independence over exact per-leaf CARD counts still gives 1,455 against a true 978
   for `f:modern border:white`), which is the second reason (1) has to come first: an estimate near a hard
   threshold will always sometimes fall the wrong side, so the threshold must not be a cliff.

## Resolved

Defect 2's trigger was the `min` fold, and [the pair table](local-engine-pair-totals.md) makes the
two-leaf card total exact (978, not 2,755), so `compose_paging` predicts the decline rather than walking
into it. Card mode now routes to `GatheredScan` at 102.1 µs instead of picking a compose that bails at
163.1 µs, while printing keeps 2.0x and artwork 1.6x. Both toggles are on; aggregate is neutral (0.995
target / 0.997 whole mix over 12 interleaved rounds).

Item (1) of the order below — `DeclineSparseExact` discarding paid work — is still open and still worth
doing: the pair table removed the case that was biting, not the cliff.

## Status

Defect 1 is on by default. Every number above is measured on the production corpus: per-query figures are
a minimum of 9–15 trials after warmup, and the A/B is 8 interleaved rounds with a control subset.

Related: [#731](../00731-engine-compose-universal-evaluator.md) — this is the arm that would gain
`color`/`cmc` as broadcast sources, which is why it is worth fixing the router on it first.
