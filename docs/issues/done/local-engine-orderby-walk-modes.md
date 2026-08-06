# The orderby walk is printing-mode only, and card mode pays 18x for it

**DONE — merged in [#839](https://github.com/jbylund/sylvan_librarian/pull/839)** (layer 7 of the
cost-model stack). Target subset 0.931, p99 −8.1%. Nothing carried forward; the related open item is
[cost-based OrderbyWalk vs Gather](../local-engine-compose-paging-cost-based.md), which is about choosing
between two branches that are both available rather than about a branch being unavailable.

(The `## Status` section below still reads "Measured, not implemented" — it describes the state at the time
the investigation was written, and `## Outcome` immediately after it is what shipped.)

**Shipped** (`b0618f0`, `38423f5`) — one walk for all three distinct-ons, via the group representative.
The walk was the easy half; routing it took four attempts, and the thing that finally worked was
realising that **breadth does not decide it — whether the compose build broadcasts does.** See
[Outcome](#outcome). Target subset 6.9% faster, whole mix 2.9%, p99 down 8.1%.

`border:black` ordered by rarity takes **25 µs** under `unique=printing`, **451 µs** under
`unique=card`, and **836 µs** under `unique=artwork`. Same filter, same orderby, same corpus. The walk
that makes the first one fast requires `Mode::Printing`:

```rust
let walk_col = perm.is_none() && matches!(mode, Mode::Printing) && orderby_walk_available(sort_col);
```

Everything else falls to a gather over every candidate CARD — so a broad predicate costs a whole-corpus
pass instead of a 60-row walk.

**The target is card mode, not artwork**, and an earlier revision of this doc had that backwards
because it ranked by the 200 slowest queries rather than by traffic. Artwork is the slowest per query
but 5% of `REALISTIC_UNIQUE_WEIGHTS`; card is 75%. Weighted by realistic traffic:

| shape | share of realistic traffic |
| --- | --: |
| artwork x (`usd`\|`rarity`) | 0.90% |
| **card x (`usd`\|`rarity`)** | **13.50%** |

`usd` (10%) and `rarity` (8%) are 18% of `REALISTIC_ORDERBY_WEIGHTS`. So ~13.5% of real traffic runs at
451-882 µs where the identical shape in printing mode runs at 25 µs. Artwork dominated the 200-slowest
list only because `uniform` sampling weights the three modes equally.

## Why card mode is 2x cheaper than artwork, and why that is fragile

Not a structural advantage — one store-order trick, and it is worth measuring because it is also the
cheapest available evidence that the fix below is cheap.

| unique | prefer | `printings_examined` | per card | µs |
| --- | --- | --: | --: | --: |
| card | default | 31,453 | **1.01** | **451** |
| card | usd_high | 96,790 | 3.11 | 882 |
| artwork | default | 96,790 | 3.11 | 836 |
| artwork | usd_high | 96,790 | 3.11 | 993 |
| printing | either | **60** | — | **25** |

Printings are stored prefer-desc within a card, so under `Prefer::Default` the first MATCHING printing
is the representative and the gather early-breaks after 1.01 printings per card instead of 3.11. Any
other prefer must score the whole card to find the max, and the break is gone — 451 → 882 µs, landing
where artwork already is. 15% of realistic `prefer` draws are non-default. Artwork can never have the
break: it must see every matching printing to discover the card's other artwork groups.

But `cards_visited` is 31,169 — the whole card corpus — in every card-mode row. The early break makes
each card cheaper, not the pass shorter, which is why card mode is still 18x off printing mode.

## This is the dominant slow shape in sampled traffic

The 200 slowest of 52,330 uniform queries are **9.9% of all dispatch time**, 937 µs mean, and only
**0.7% of it is recoverable by routing**. What they have in common is not a missing index:

| axis | | |
| --- | --- | --- |
| predicate count | **135 of 200 are a SINGLE predicate** | mean 888 µs |
| unique mode | **136 of 200 are `artwork`** | 0.8% of artwork queries vs 0.1% of card |
| orderby | **124 of 200 are `usd` or `rarity`** | the two permutation-less columns |

And the predicates are broad, not obscure — `cn>28`, `r<=mythic`, `border:black`, `eur>=0.03`,
`usd>=0.08`, `year>2008`, each ~880 µs as a sole predicate. Nothing here needs a new index; they match
most of the corpus and the cost is producing a deduped page over that match set in an order with no
precomputed structure.

Measured across the grid, so the interaction is visible rather than inferred:

| query | unique | orderby | paging branch | µs |
| --- | --- | --- | --- | --: |
| `border:black` | printing | rarity | **OrderbyWalk** | **25.5** |
| `border:black` | artwork | rarity | Gather | **853.8** |
| `border:black` | card | rarity | Decline → GatheredScan | 353.5 |
| `border:black` | artwork | edhrec | Perm | 172.2 |
| `border:black` | printing | edhrec | Perm | 26.7 |
| `cn>28` | printing | rarity | RangeNoPermutation | 53.0 |
| `cn>28` | artwork | rarity | Gather | 898.1 |
| `r<=mythic` | printing | rarity | **OrderbyWalk** | **28.3** |
| `r<=mythic` | artwork | rarity | Gather | 821.6 |

`orderby=edhrec` has a card permutation and is 26-221 µs in every mode. The gap opens only where the
orderby has no permutation AND the mode is not printing.

## Why the restriction is real, not arbitrary

The walk emits printings in value order. A card- or artwork-mode row is a GROUP, and the group's sort
key is its **representative** printing's key — where the representative is chosen by `prefer`, not by
the sort column. `gather_composed_page` can do this because it has the whole match set: it groups,
picks each group's representative by `prefer` (`gather_group_printings`), and only then sorts.

A value-order walk cannot. It meets a card's printings scattered across the value order, so at the
moment it first encounters one it does not yet know whether that printing is the group's
representative — and if it is not, the group's true sort position is somewhere else entirely. Taking
the first-encountered printing would silently redefine the ordering as "group by min usd", which is not
what any other plan produces. This is the same representative-selection trap that makes
`ArchivedSortPermutations::get` return `None` for usd/rarity in the first place, and the same class of
bug as the printing-varying `all_match` problem: it changes which ROW is returned, not just the count,
so `force_plan_differential_agreement` is the gate for anything attempted here.

### The semantics, measured rather than assumed

`unique=card orderby=usd desc` over `usd>=0.01` returns **Timetwister's $8.15 printing** while that
card's most expensive printing is **$51.42** — and the page is ordered by the returned price,
monotonically. Six of the top 25 rows are likewise not their card's maximum. So the key is the
representative's value, not min/max over the group, and the representative is `prefer`-chosen. That is
the whole obstacle, stated as a fact about output rather than a reading of the code.

### A static permutation is the wrong tool, and the numbers say why

A permutation is sound for a card only if its key is identical whichever printing represents it:

| column | cards with a uniform value | can move with the filter |
| --- | --: | --: |
| rarity | 28,713 (91.1%) | 2,795 (8.9%) |
| **usd** | 13,268 (42.1%) | **18,240 (57.9%)** |

41.4% of cards have a single printing, so nearly all of usd's "uniform" cards are simply cards that
could not vary. And displacement is unbounded: among cards with a price spread, max/min is p50 2.1x,
p90 9.0x, **p99 241x, max 24,000x**. A displaced card does not land near its static position, it
crosses the whole order, so repairing a static permutation needs an unbounded buffer.

Rarity's 8.9% is the same order as `legal_divergent`'s 1.8% carve-out, so rarity alone might be
servable that way. usd cannot. Storage was never the issue — 123 KB per vector, 492 KB for both
columns both directions, 0.96 MB with inverses, against a 69 MB store.

### What does work: resolve the representative on the fly, no new structure

The walk already has the value index. Walk its key runs in page order and, for each matching printing
`p` of card `c`, resolve `rep(c)` — the `prefer`-best MATCHING printing of `c` — then **emit `c` only
when `p == rep(c)`**.

That is correct, and each card is emitted exactly once at its true key position:

- If `rep(c)`'s value is *below* the current run, `rep(c)` was itself encountered in an earlier run
  (it matches, so it is in the index) and `c` was emitted there.
- If it is *above*, this encounter is skipped and the walk reaches `rep(c)` later.
- If it is *equal*, `rep(c)` is in this same run, so emitting at `rep(c)`'s position gives the correct
  tiebreak — which is exactly why the test is `p == rep(c)` and not "first printing of an unseen card".
  Emitting on first encounter would order the group by whichever printing arrived first, which is the
  min-over-group semantics the measurement above rules out.

Cost is one representative resolution per encountered matching printing, and the card-mode early break
above is the measurement: it resolves a representative in **1.01 `pbits` tests per card** in
production, on the same store order, for the same reason. That is not an estimate — it is the operation
this walk needs, already running at scale. Non-default prefers cost the card's full span instead (3.11
printings). No buffer, no permutation, no archive change.

The prize, bounded by what the equivalent permutation walk already achieves in the same modes
(`border:black`/edhrec, which HAS a permutation): 172 µs artwork, and card mode's own `Perm` figures.
So expect card mode ~451 → tens of µs and artwork ~836 → ~172 µs — a large win on 13.5% of realistic
traffic and a smaller one on 0.9%.

One gate interacts: card mode currently DECLINES compose on broad filters
(`COMPOSE_GATHER_MAX_CARD_FRACTION`, correctly for the gather) and runs `GatheredScan`. A walk inverts
that premise exactly as it already does for printing mode — "broad ⇒ not worth composing" is backwards
for a branch whose best case is a dense predicate — so the gate must not apply to it, the same carve-out
`walk_col` already has.

Row identity is the gate, not regret: this changes which ROW represents a group if the `p == rep(c)`
test is wrong, and `force_plan_differential_agreement` asserts full row order against `GatheredScan`.

### Rejected

- **Redefine group ordering as min/max over the group.** Cheap and wrong: it changes results, and the
  measurement above shows it is not what any current plan produces.
- **Accept it.** The router already picks the cheapest of the bad options — regret on these rows is
  ~0.7% — so there is nothing to recover by routing, only by making a better option exist.

## What it is worth

Whole-corpus-broad artwork queries on `usd`/`rarity` are 136 of the 200 slowest and ~7% of all
dispatch time in uniform sampling. `unique=artwork` is 5% of realistic traffic
(`REALISTIC_UNIQUE_WEIGHTS`: card 75, printing 20, artwork 5) and `usd`/`rarity` together are a
meaningful slice of `REALISTIC_ORDERBY_WEIGHTS`, so unlike
[the layout question](../local-engine-layout-postings.md) this shape is genuinely sampled by realistic
traffic — it just needs the realistic-mode number measured before the work is priced.

Card mode was described here as "a separate case and mostly NOT this problem". That was wrong: it is
the SAME problem and the largest instance of it. Compose does decline there
(`COMPOSE_GATHER_MAX_CARD_FRACTION` — `border:black` is 98.9% of cards, past the calibrated ~93% where
the gather stops winning) and that decline is correct **for the gather**; it is not correct for a walk,
which is the point.

## Status

Measured, not implemented. Timings are the routed path through `engine.query` on the production corpus,
minimum of 20 after warmup; the 200-slowest breakdown is one 150 s uniform sample of 52,330 priced
queries.

Related: [cost-based OrderbyWalk vs Gather](../local-engine-compose-paging-cost-based.md) is about
choosing between the two branches where BOTH are available; this is about the branch not being
available at all.

## Outcome

The walk itself was the easy half. `group_representative` reproduces `walk_grouped_page`'s selection
byte for byte and takes the same early break (printings are stored prefer-desc, so under
`Prefer::Default` the first match wins) — without that break the walk paid **42 probes per resolution**,
because heavily reprinted cards sort early under `edhrec_rank`; with it, 2.1.

| query | unique | prefer | before | after |
| --- | --- | --- | --: | --: |
| `border:black`/rarity | card | default | 451 µs | **165 µs** |
| `border:black`/rarity | card | usd_high | 882 µs | **287 µs** |
| `r<=mythic`/rarity | card | default | 626 µs | **233 µs** |
| `r<=mythic`/rarity | artwork | default | 822 µs | **219 µs** |

### Two routing attempts that failed, and why

1. **Gate the grouped walk on `compose_gather_declines`' broad verdict.** The router then predicted
   `OrderbyWalk` where the executor gathered, so compose won the argmin at a walk's price and paid a
   gather's: **1.8–2.6× regressions on every mid-range legality filter** (`f:penny` 176 → 465 µs). It
   also left `ComposePaging::Gather` unreachable — every other sort column has a permutation — which
   `compose_paging_prediction_matches_the_branch_taken` caught immediately.
2. **Compare result-space totals at `COMPOSE_GATHER_MAX_CARD_FRACTION` (0.85).** Killed the regressions
   and the wins together: the router's card estimate tops out near 0.82 of the domain, so it never
   cleared a bar the executor's exact total did. `cn<645` was the lone survivor, and that is the clue —
   it is a one-sided range, so `RangeCardCounts::distinct_cards` answers it EXACTLY and the two sides
   agreed.

### What made it work: legality already had an exact count

`legality_candidate_bits` reads the status's card-space `_EXISTS` plane, and existence-for-some-printing
**is** the fact `unique=card` counts — so its popcount is the exact distinct-card total.
`compose_printing_estimate` was computing it, scaling it into printing space, and the caller then ran
balls-into-bins over that to recover an *estimate* of the number it had started with:

| format | exact cards | estimated | ratio |
| --- | --: | --: | --: |
| `f:future` | 4,672 | 7,141 | 1.53× |
| `f:pauper` | 10,703 | 14,021 | 1.31× |
| `f:penny` | 15,060 | 17,747 | 1.18× |
| `f:duel` | 31,386 | 25,903 | 0.83× |

Now exact — 1.00 on all eight formats checked — via `exact_card_total`, alongside the one-sided range
case. **No stored table needed**: a per-format count table would work (32 formats × 4 statuses ≈ 1 KB)
but a popcount of a plane the query already reads gives the same answer for nothing.

Note the direction reverses: over by up to 1.53× when sparse, **under** at 0.83× when near-total,
because the formula saturates against the domain while the truth approaches it. That is why no single
bias constant fixed it, and why the 0.85 gate — sitting exactly where the error changes sign — behaved
so badly.

### Attempt 3 was a fitted threshold, and the sweep that killed it

Comparing result-space totals at a fitted 0.75 got the regressions to 1.00x and kept four wins, but the
aggregate stayed inside the noise floor — and it excluded `cn>=74 cn<=413` by **0.4 percentage points**
on a query where the walk is 5x faster. That near-miss was the clue to sweep the axis properly. Forcing
the branch each way over 14 card-mode filters x {usd, rarity}:

| breadth | query | walk/gather | |
| --: | --- | --: | --- |
| 100% | `r<=mythic` | **0.28×** | walk |
| 100% | `f:duel` | 1.25× | gather |
| 99% | `border:black` | **0.41×** | walk |
| 75% | `cn>=74 cn<=413` | **0.20×** | walk |
| 71% | `f:modern` | 1.08× | gather |
| 48% | `f:penny` | 1.85× | gather |
| 45% | `cn>200` | **0.53×** | walk |
| 35% | `r:rare` | **0.57×** | walk |
| 34% | `f:pauper` | 1.75× | gather |
| 15% | `f:future` | 1.37× | gather |
| 9% | `r>=mythic` | **0.54×** | walk |

**Breadth does not predict the winner. The filter family does** — every legality filter loses, every
range/plane filter wins, at every breadth from 9% to 100%, 28 of 28 cells separated by one bit.

A legality leaf's compose build BROADCASTS a card-space plane across every printing of every matching
card, which dominates the query — `f:duel` measures ~180 µs on *both* branches, so paging is noise — and
legality is card-invariant, so the gather's early break lands at its best possible case, 1.01 printings
per card. A range or plane leaf composes with a cheap slice or scatter, leaving paging to dominate, and
that is where an O(page) walk beats an O(matches) gather.

### What shipped: a structural gate

`compose_needs_broadcast(filter)` — does this tree contain a legality leaf — plus the existing sparse
floor. Structural matters twice: it removes the fitted constant, and it removes the estimate-vs-exact
disagreement between router and executor that caused attempts 1 and 2, because both sides read the same
tree. `compose_paging_with_total` takes the filter now, and the acquire passes `compose_source`'s output
rather than the residual, since a plane-consumed residual would hide the leaf that decides the branch.

### What it is worth

Paired wall time, 2,000 realistic queries, per-query minimum over two runs on each side:

| subset | n | base | attempt 3 (0.75) | **final** | final/base |
| --- | --: | --: | --: | --: | --: |
| card/artwork × (usd\|rarity) TARGET | 279 | 35.3 ms | 35.0 ms | **32.9 ms** | **0.931** |
| printing × (usd\|rarity) | 76 | 10.0 ms | 10.1 ms | 9.8 ms | 0.981 |
| everything else CONTROL | 1,645 | 140.6 ms | 141.8 ms | 138.0 ms | 0.981 |
| whole mix | 2,000 | 186.0 ms | 187.0 ms | **180.7 ms** | **0.971** |

p50 59.0 → 58.1, p90 172.3 → 168.4, **p99 635.4 → 583.8 µs**. Control and printing both read 0.981 —
inside the noise floor — so the aggregate move is the target subset's. And unlike attempt 3, which paid
off on four queries, the wins are broad:

| query | before | after |
| --- | --: | --: |
| `cn>=74 cn<=413` | 849 µs | **227 µs** (attempt 3 excluded it entirely) |
| `r>=mythic` | 151 µs | **58 µs** (9% breadth; attempt 3 declined it) |
| `year>=1994 year<=2013` | 352 µs | 156 µs |
| `r<=uncommon` | 422 µs | 195 µs |
| `border:black` | 449 µs | 228 µs |
| `cn<645` | 551 µs | 315 µs |
| `year>2017` | 516 µs | 297 µs |
| `date<2019-11-07` | 527 µs | 317 µs |

Regressions are 1.11–1.28× on queries of 12–45 µs, plus one 100 → 114 µs row on `orderby=name` that
this change cannot reach.

### Two corrections to earlier readings of this data

- **`r>=mythic` and `t:creature year<=2016` were listed as attempt-1 "wins". They were noise.** Verified
  by BRANCH rather than by timing: `r>=mythic` card/usd is 9% of the domain, compose declined in every
  build, and `GatheredScan` ran throughout; `t:creature year<=2016` is `orderby=name`, which has a
  permutation. A table sorted by timing ratio mixes real branch changes with a ±1.58× per-query noise
  floor, so branch identity is the check, not the ratio. (`r>=mythic` *is* a genuine win now — but only
  because the structural gate lets it walk, which is a different mechanism.)
- **The comparison itself was unfair for one round**: best-of-two on the baseline against a single run of
  the new build hands the baseline a floor the new build never gets, which manufactured
  "`f:penny` 2.6× slower" on an orderby that has a permutation. Both sides need the same estimator.
