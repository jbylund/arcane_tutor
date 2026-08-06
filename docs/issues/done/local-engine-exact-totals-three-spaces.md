# Exact result totals in all three spaces, for the predicates that have a plane

**DONE — merged in [#841](https://github.com/jbylund/sylvan_librarian/pull/841)** (layer 9 of the
cost-model stack), archive `2026080509`. Nothing carried forward.

Kept deliberately despite measuring flat: **zero plans changed** across 1,347 query configs and the whole
mix read 1.001. The estimates were wrong by up to 10× and still on the correct side of every argmin,
because the margin between best and next-best is wider than the error. What justified shipping it is that
a later layer depends on the exactness: [#844](https://github.com/jbylund/sylvan_librarian/pull/844)'s pair
table is what made the legality scan-scope fix in that same layer safe — see
[local-engine-legality-scan-scope.md](local-engine-legality-scan-scope.md). (#841's own body credits that
to "layer 11"; the scan-scope fix is in layer 12, alongside the pair table.)

The router's biggest remaining estimation error is not a bad formula, it is asking a formula a question
the store can already answer. `unique=` has three result spaces — printings, cards, artworks — and for a
predicate backed by a plane or a value index, the exact count in each is a build-time aggregate that
fits in kilobytes.

Two halves, both now done — and the honest headline is that the second one made 27 of 27 estimate cells
exact and changed **no plan and no wall time**. Section 2's A/B is the number to read before extending
this line of work.

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
free win — see [the `is:`/`frame:` doc](local-engine-is-frame-predicates.md) for that one.

`range_card_counts_are_exact` checks both spaces exhaustively over every distinct value on three
indexes, against a brute-force distinct count, plus the shapes the table must DECLINE (a genuinely
interior multi-value range). Exhaustive rather than sampled because the errors in this area clustered at
range ends.

## 2. Rarity, and the per-value core table — SHIPPED, and it bought no latency

**Rarity** got a sixth `RangeCardCounts` over the `rarity_printing_ordered` index that already existed
for the rarity orderby walk. Per-value counts would have been *wrong* here, not merely incomplete: a
card printed at both common and rare is in the distinct-card count for each, so `r<=rare` is not a sum.
Prefix/suffix/at is the shape the question needs. 184 bytes; artwork went from 1.07-1.35 (error changing
sign, so no bias constant could have fixed it) to 1.000 on all six values and every op.

**Border, layout, frame, and (format, status)** got `ValueTotals` — a per-value `SpaceTotals`
(printings, cards, artworks), built in one pass with per-value stamps for the two dedup questions.
Measured before/after, `est / true` on the production corpus:

| predicate | mode | before | after |
| --- | --- | --: | --: |
| `border:black` / `border:borderless` | artwork | 0.905 / 0.835 | **1.000** |
| `frame:2015` / `2003` / `1997` | card | 0.952 / 0.901 / 0.874 | **1.000** |
| `frame:2015` / `2003` / `1997` | artwork | 0.948 / 1.080 / 0.973 | **1.000** |
| `f:modern` / `f:vintage` / `f:pauper` | printing | 0.931 / 1.005 / 1.039 | **1.000** |
| `f:modern` / `f:vintage` / `f:pauper` | artwork | 0.835 / 0.882 / 0.873 | **1.000** |
| `banned:modern` | printing / artwork | 0.401 / 0.411 | **1.000** |

All 27 cells exact. Three implementation points:

- **All 32 format slots are stored, not just the assigned ones.** Absence from this table is read as an
  exact zero, so an under-populated table is a *wrong* total rather than a missing one — and restricting
  to the registry snapshot left it empty wherever the snapshot was (the fuzz store). The entries for
  unassigned slots are correct rather than merely harmless: an unassigned format reads `not_legal` for
  every card, which is what those entries say.
- **Legality is counted per printing, not per card**, so `legality_divergent` cards contribute their
  printings' own words. Reading the card word for those would mis-count exactly the cards the flag exists
  to flag.
- **Printing mode takes the exact value for `result_total` only.** `printing_matches` also feeds
  `scan_units` and `project`, and it proxies the size of the bitmap compose BUILDS — for a legality leaf,
  every printing of every existentially-legal card, a superset the residual then filters. The cost
  features are calibrated against that superset, so substituting the true match count there would
  under-charge the scan on precisely the divergent cards.

### It changed no plan, and no wall time

Interleaved A/B, 8 rounds, 1,398 queries (realistic sample plus enrichment across every dimension
covered), `CARD_ENGINE_EXACT_VALUE_TOTALS` selecting the arm so both read a byte-identical archive:

| subset | n | off | on | on/off |
| --- | --: | --: | --: | --: |
| exact-total touched TARGET | 482 | 70.5 ms | 70.4 ms | **0.998** |
| everything else CONTROL | 916 | 81.6 ms | 81.8 ms | 1.003 |
| whole mix | 1,398 | 152.1 ms | 152.2 ms | **1.001** |

p50 1.002, p90 1.008, p99 1.001; median cell 1.000 in both subsets. The ±6-8% outliers are on queries
the change cannot reach (`name:the`, `a:easley`, `o:more`) and appear in both directions — the noise floor.

The reason is not that the wins cancelled. **The router picked the identical plan for all 1,347 query
configs**, off and on. Estimates wrong by 0.63-1.35x were still on the correct side of every argmin on
this mix. So the honest accounting is: exactness achieved, latency unchanged, ~3.2 KB spent.

That is consistent with the 2.5%-of-dispatch-time ceiling on all cost-model work
([measured](../local-engine-layout-postings.md#why-this-was-invisible-before)) — it just lands at the
bottom of that range rather than the top. It does NOT vindicate the earlier reading that the artwork
slice's 21% share of routing regret came from its missing exact totals: those totals are now exact and
the regret did not move.

### What is still not exact, and why the table cannot fix it

`is:flip` reads **1,080x** over and `is:split` 135x, unchanged. Their acquire is `count_source:
candidates`, not compose, so `exact_result_total` is never consulted — `card_layout` has no narrowing arm
at all ([layout postings](../local-engine-layout-postings.md)). The `layout` map in `ValueTotals` is
therefore correct but **unreachable today**; it is kept (14 entries, ~200 bytes) because it is what that
work will need, not because anything reads it now.

`-r:common` also stays off (0.45 printing, 0.67 artwork): a negated rarity leaf acquires as `candidates`
rather than compose, so it too never reaches these arms.

## What exactness here was supposed to be worth

Estimation error does not cost time directly; it costs time by mis-routing. The bound on all
cost-model work is small — 2.5% of dispatch time is the total recoverable regret over uniform traffic
([measured](../local-engine-layout-postings.md#why-this-was-invisible-before)). But routing regret is
concentrated: the artwork slice alone carried 21% of it at p99 205 µs against 36–40 µs for the other two
modes, and the reason is that artwork was the only space with no exact path at all.

So the case for this work was not "the estimate is ugly" — it was that a wrong total on one side of an
argmin picks the wrong plan, and the two spaces without exact counts were the two where the router was
most often wrong.

**That reasoning did not survive measurement.** The premise is sound and the conclusion was false here,
because it assumed the argmin was *close*. It is not: on these dimensions the gap between the best plan
and the next one is wide enough to absorb a 35% error in the total. The lesson for the next accuracy
item is to check the argmin's MARGIN before fixing an estimate — a large error on a wide margin is free
to leave alone, and cheap to prove harmless with the plan-diff probe rather than a full A/B.

## Status

Both halves are implemented and measured on the production corpus. Every accuracy ratio is a minimum of
3 runs after warmup against the executor's realized total; the wall-clock table is 8 interleaved rounds
with a control subset, on a machine with Docker shut down.

Total archive cost: **156.6 KB** for the artwork column plus **3.2 KB** for the per-value table and
rarity — 0.22% of the store, for exactness that bought no measured latency. Whether that trade is worth
keeping is a judgement call, and the flat A/B is the number to make it on.
