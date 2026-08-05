# The orderby walk is printing-mode only, and that costs 33x on artwork mode

`border:black` ordered by rarity takes **25.5 µs** under `unique=printing` and **853.8 µs** under
`unique=artwork`. Same filter, same orderby, same corpus. The walk that makes the first one fast
requires `Mode::Printing`:

```rust
let walk_col = perm.is_none() && matches!(mode, Mode::Printing) && orderby_walk_available(sort_col);
```

Everything else falls to `gather_composed_page`, which visits every candidate — so a broad predicate
costs a whole-corpus gather instead of a 60-row walk.

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

Cost is one representative resolution per encountered matching printing, and it is cheap: under
`Prefer::Default` printings are stored prefer-desc within a card, so `rep(c)` is the first `pbits` hit
in `offsets[cid]..offsets[cid+1]` — ~1-3 bit tests, 3.08 printings per card on average. A 60-row page
over a broad filter resolves on the order of 60-200 spans, against `gather_composed_page`'s whole-corpus
visit. No buffer, no permutation, no archive change.

The prize is bounded by what the equivalent permutation walk already achieves in artwork mode:
`border:black`/artwork/edhrec runs `Perm` at 172 µs, so ~5x on the 853 µs cell rather than the 33x that
printing mode gets — artwork mode has real per-card work the walk cannot remove.

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
[the layout question](./local-engine-layout-postings.md) this shape is genuinely sampled by realistic
traffic — it just needs the realistic-mode number measured before the work is priced.

Card mode is a separate case and mostly NOT this problem: compose declines there
(`COMPOSE_GATHER_MAX_CARD_FRACTION`, correctly — `border:black` is 98.9% of cards, past the calibrated
~93% where the gather stops winning) and `GatheredScan` runs at 353-626 µs. That decline is right; the
cost is real.

## Status

Measured, not implemented. Timings are the routed path through `engine.query` on the production corpus,
minimum of 20 after warmup; the 200-slowest breakdown is one 150 s uniform sample of 52,330 priced
queries.

Related: [cost-based OrderbyWalk vs Gather](./local-engine-compose-paging-cost-based.md) is about
choosing between the two branches where BOTH are available; this is about the branch not being
available at all.
