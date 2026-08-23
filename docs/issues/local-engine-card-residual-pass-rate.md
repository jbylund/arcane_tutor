# Card mode's `matches` estimate is bimodal, and a flat pass rate makes it worse, not better

`candidate_feats`'s `Mode::Card` arm sets `matches = count` (the raw candidate-card count) with no
discount at all, unlike `Mode::Printing`/`Mode::Artwork`, which apply a fitted `RESIDUAL_PASS_RATE_*`.
That is provably wrong whenever a residual conjunct survives narrowing and is genuinely selective: real
`matches_pushed` can run up to 132x lower than the `matches` estimate. It has real absolute cost at
scale (tens of microseconds at large `eval_domain`), and no cheap fix was found. Documented so the next
pass at this doesn't re-derive the same dead end.

## Why this looked fixable

`matches` only feeds one term in each plan's formula — `GATHER_PUSH_PER_MATCH_NS` (2.24 ns/match) and
`STREAM_EMIT_PER_MATCH_NS` (0.12 ns/match) — so the natural fix looked like the same shape as the
`scan_units` card-mode fix landed alongside this investigation: find the systematic bias, add a
targeted correction.

Concrete examples, all compound (multi-predicate) card-mode queries with a residual:

| query | eval_domain | matches (est) | matches_pushed (real) | ratio |
| --- | --: | --: | --: | --: |
| `o:surveil frame:2003` | 215 | 215 | 1 | 215.0x |
| `ft:shadow year<1995` | 226 | 226 | 3 | 75.3x |
| `name:ja tix<0.02` | 272 | 272 | 5 | 54.4x |
| `cn:141 o:nonlegendary` | 40 | 40 | 1 | 40.0x |

The mechanism: `narrow_candidates_exact` narrows on whichever conjunct it can index (`frame:2003`,
`tix<0.02`, `cn:141`, ...), leaving the *other* conjunct (`o:surveil`, `year<1995`, `o:nonlegendary`) as
a per-candidate residual whose own selectivity `matches = count` does not account for at all.

## Why the ratio looked bigger than the impact, at first

The four examples above are all small (`eval_domain` 40-272) and their absolute measured times are
0.75-5.6 µs. Decomposed: for `is:vanilla id:bw frame:etched` (eval_domain 132), the push-term overcharge
is 293 ns against a 4,437 ns total prediction gap (measured 750 ns vs predicted 5,187 ns) — about 7% of
the error. The other 93% is the already-known, already-deferred `GATHER_RESIDUAL_FLOOR_NS` overcharge
(`eval_domain * max(tier, 18.89)` ≈ 132 × 21.89 ≈ 2,889 ns alone). At this scale the ratio is dramatic
and the absolute damage is not.

That stops being true at scale. Bucketing the real push-term overcharge (`(matches_est -
matches_pushed) * 2.24 ns`) by `eval_domain`:

| eval_domain bucket | n | median overcharge | p90 | p99 | max |
| --- | --: | --: | --: | --: | --: |
| [0, 100) | 13,278 | 0 ns | 27 ns | 134 ns | 215 ns |
| [100, 1,000) | 3,820 | 0 ns | 488 ns | 1,214 ns | 2,038 ns |
| [1,000, 10,000) | 2,860 | 651 ns | 5,773 ns | 12,768 ns | 21,517 ns |
| [10,000, ∞) | 1,008 | 10,062 ns | 26,990 ns | 38,958 ns | 52,477 ns |

Small queries hide it (as expected — everything hides in a query whose whole cost is under a
microsecond); large ones don't.

## Why no fix shipped

The real pass rate (`matches_pushed / count`) is **bimodal**, not a population that a single constant
discount can describe:

| population | n | median | geomean | mean | p10 | p90 |
| --- | --: | --: | --: | --: | --: | --: |
| compound (multi-predicate) filters | 16,532 | **1.000** | 0.711 | 0.817 | 0.350 | **1.000** |
| bare (single-predicate) residuals | 4,614 | 1.000 | 0.954 | 0.980 | 1.000 | 1.000 |

Even restricted to compound filters, both the median *and* p90 real pass rate are 1.000 — the
low-pass-rate tail dragging the geomean down to 0.711 is confined to well under 10% of compound
queries. A flat `CARD_RESIDUAL_PASS_RATE` fitted to that geomean would under-predict the ~90% of
compound queries that need no discount at all, while still not reaching the severe end (pass rates
measured down to ~0.03 on the worst rows) — a bad trade in both directions.

This is not a new failure mode for this codebase. `candidate_feats`'s own doc comment on the
`Printing`/`Artwork` discount records the same experiment already run and rejected one mode over:
`estimator::estimate_cardinality` (index-backed, independence-assumption over `And`) was tried
specifically to do better than a flat rate there, and measured *worse* — "card mean |log| 1.31 against
0.79, p90 33.38 against 9.91". Nothing here suggests a naive estimator would fare differently for card
mode's version of the same problem, and the data above says a *flat rate* specifically loses on the
majority case, which the printing/artwork rate does not (that population isn't bimodal the same way).

## If someone wants to fix this properly

The honest fix is a real per-query cardinality estimate for the *leftover, unnarrowed* residual
conjunct — not a global rate. Candidates worth trying, none attempted here:

- A conditional correction gated on a cheap, specific signal rather than "is the filter compound":
  e.g., only apply a discount when the residual conjunct is itself index-backed (a second
  `CollectionCmp`/range leaf that narrowing didn't pick), where an exact or near-exact count might be
  cheaply available — the same shape as the `RangeCardCounts::distinct_cards` 9%-coverage path already
  used elsewhere in this file, adapted to cover more of the compound population.
- Keying off `proven_conjuncts`: an `And` where narrowing proved *all but one* conjunct is a
  structurally different case from one where narrowing only partially covers a single unproven
  conjunct's own selectivity — the current investigation treated "compound vs bare" as the whole
  signal, but `proven_conjuncts`'s bit count is already computed and unused for this purpose.

## Status

Documented, not implemented. Land alongside `scan_units`'s card-mode fix (same investigation,
different outcome) as the record of what was tried and why it didn't ship.
