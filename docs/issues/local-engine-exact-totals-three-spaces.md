# Exact result totals in all three spaces, for the predicates that have a plane

The router's biggest remaining estimation error is not a bad formula, it is asking a formula a question
the store can already answer. `unique=` has three result spaces — printings, cards, artworks — and for a
predicate backed by a plane or a value index, the exact count in each is a build-time aggregate that
fits in kilobytes.

Two halves. The first is done.

## 1. The artwork column on the range tables — SHIPPED

`RangeCardCounts` carried prefix / suffix / at triples for **cards** only. Printings were already exact
(`k = e - s` from the value index's own partition points), so artworks were the one space every
one-sided range query estimated, through `printing_bits_to_artwork_bits` plus a two-stage
balls-into-bins.

Measured before and after, `est / true` on the production corpus (`unique=artwork`, sole predicate):

| query | before | after |
| --- | --: | --: |
| `usd<50` | 0.87 | **1.000** |
| `year>=2015` | 0.84 | **1.000** |
| `cn>200` | 0.80 | **1.000** |
| `usd>=200`, `year<2000`, `cn<=10`, `eur<5`, `tix>=1` | — | **1.000** |

Card mode stayed 1.000 and printing mode stayed exact by construction; all five range dimensions
(`released_at`, `price_usd`, `price_eur`, `price_tix`, `collector_number`) are covered.

Three implementation points worth keeping:

- **One pass, two spaces.** The artwork triple is filled in the same sweep as the card triple over a
  parallel seen-bitmap. Two passes would have been simpler and would have been able to drift.
- **The artwork id must be global** — `artwork_base[card] + artwork_group_id`. Group ids repeat across
  cards, so omitting the base merges unrelated artworks. The exactness test brute-forces it the same way.
- **`exact_cards` and `exact_total` are different quantities.** `eval_domain`, `scan_all` and the
  artwork capacity all consume a CARD count regardless of the query's mode; only `result_total` wants the
  mode's own space. `exact_card_total` became `exact_result_total(filter, indexes, n_cards, mode)` and the
  acquire calls it twice — once pinned to `Mode::Card`, once in the query's mode. Collapsing them into one
  mode-aware call puts an artwork count where cards are expected.

Cost: **156.6 KB** of archive (12 bytes × ~13,400 distinct values), 0.22% of the store. That is more
than the 133 KB `frame_data`'s hybrid handed back, so this is a real size-for-accuracy trade and not a
free win — see [the `is:`/`frame:` doc](./local-engine-is-frame-predicates.md) for that one.

`range_card_counts_are_exact` checks both spaces exhaustively over every distinct value on three
indexes, against a brute-force distinct count, plus the shapes the table must DECLINE (a genuinely
interior multi-value range). Exhaustive rather than sampled because the errors in this area clustered at
range ends.

## 2. The core 3-space count table — NOT YET DONE

Same idea for the predicates that are a plane rather than a range. Five low-cardinality dimensions, 77
values between them:

| dimension | values |
| --- | --: |
| border | 5 |
| rarity | 6 |
| layout | 14 |
| frame | 29 |
| format | 23 |

77 values × 3 spaces × 4 bytes = **0.9 KB**. At that size there is no threshold to tune and no sparse
tail to special-case: store all of them.

What it fixes, from the accuracy audit:

| predicate | mode | est/true today |
| --- | --- | --: |
| `frame:*` | card | 0.63–0.95 |
| `format:*` | printing | 0.87–0.93 |
| border / rarity / frame / format | artwork | estimated |

Wire it into `exact_result_total`, which already has the mode parameter and already returns `None` for
non-card modes on the shapes it cannot answer — the table is exactly what turns those `None`s into
answers. Consumers are `compose_total_for_mode` and the acquire's `exact_cards`.

Rare statuses (`banned`, `restricted`) need not be covered: they are a small share of traffic and the
estimator's error on them is not what is costing time.

**Not the same as a popcount of a plane.** Where the query already reads a card-space `_EXISTS` plane,
its popcount IS the exact card total and no table is needed — that is how legality got exact card counts
for free, recorded in [the walk-modes doc](./local-engine-orderby-walk-modes.md). The table is for the
two spaces that popcount cannot give: a card-space plane does not say WHICH printing matched, so it
cannot count printings or artworks.

## Why exactness here is worth anything at all

Estimation error does not cost time directly; it costs time by mis-routing. The bound on all
cost-model work is small — 2.5% of dispatch time is the total recoverable regret over uniform traffic
([measured](./local-engine-layout-postings.md#why-this-was-invisible-before)). But routing regret is
concentrated: the artwork slice alone carried 21% of it at p99 205 µs against 36–40 µs for the other two
modes, and the reason is that artwork was the only space with no exact path at all.

So the case for this work is not "the estimate is ugly". It is that a wrong total on one side of an
argmin picks the wrong plan, and the two spaces without exact counts were the two where the router was
most often wrong.

## Status

(1) is implemented and measured on the production corpus; (2) is scoped and not started. Every ratio
above is measured, minimum of 3 runs after warmup, against the executor's realized `result_total`.
