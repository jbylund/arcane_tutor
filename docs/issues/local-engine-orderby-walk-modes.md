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

So this is not a small gate to relax. Three directions, none cheap:

1. **Walk in value order but resolve representatives lazily.** For `Prefer::Default` the representative
   is the first matching printing in *pid* order within the card, which a value-order walk can determine
   only after seeing all of that card's matching printings. Bounded if a card's printings can be probed
   cheaply from `pbits` on first encounter (`offsets[cid]..offsets[cid+1]`, ~3 printings on average) —
   that would make the walk emit correct groups at O(page x printings-per-card). **This is the promising
   one and it has not been tried.**
2. **Redefine group ordering as min/max over the group** for these two columns. Cheap and wrong: it
   changes results, and every other plan would have to change with it.
3. **Accept it** and make sure the router at least picks the cheapest of the bad options. It already
   does — regret on these rows is ~0.7%.

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
