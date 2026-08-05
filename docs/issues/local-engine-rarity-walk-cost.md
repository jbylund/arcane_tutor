# The rarity orderby walk: postings for mythic would not help, and what does

`walk_rarity_orderby_page` walks rarity buckets in sort order. The four interior rarities
(`common`=0, `uncommon`=1, `rare`=2, `mythic`=3) are one-hot **planes**; the sparse tail
(`special`=4, `bonus`=5) is **postings** ([planes.rs:184](../../card_engine/src/planes.rs#L184)).

The idea this doc closes off: give mythic — and the other low-density rarities — postings too, so a
selective query does not pay a whole-corpus plane AND.

## It would be slower, and the current crossover is in the right place

A plane bucket ANDs `words_per_plane` words of the corpus: **1,519** word operations at 97,206 printings,
fixed, whatever the filter matches. A postings bucket walks its own id list: one bit-test per entry.

| rarity | printings | plane cost | postings cost | cheaper |
| --- | --: | --: | --: | --- |
| common | ~27,500 | 1,519 words | ~27,500 entries | **plane** |
| rare | — | 1,519 words | — | **plane** |
| **mythic** | **8,924** | **1,519 words** | **~8,924 entries** | **plane, ~6x** |
| special / bonus | sparse | 1,519 words | short list | **postings** |

Mythic is 9% of printings, which sounds sparse but is still ~6x more entries than the plane has words.
The crossover is where a rarity's printing count passes ~1,519, and every interior rarity is far above it
while special/bonus are far below. So the representation is already right, and moving mythic to postings
would cost ~6x on every rarity-ordered page that touches it.

Nor does it help the case that motivated the question. `r:mythic` ordered by **usd** scans 31,698 entries
of the *price* index — that walk never consults a rarity structure at all, it bit-tests `pbits`. Fixing it
needs a price-ordered index per rarity value, which is one index per (predicate value × sort column) and
unbounded in combination; the cheap fix is
[making the paging branch cost-based](./local-engine-compose-paging-cost-based.md) so the walk is not
taken.

## The real defect: one rate for two operations

Both bucket kinds report their cost in the same unit — a plane bucket reports `wpp * 64`, the printings
*covered*, deliberately, "so it stays comparable to the entry-scanning buckets" — and both are then
charged `COMPOSE_WALK_STEP_NS`. But a word-AND covering 64 printings and 64 individual bit-tests are not
the same operation. Split by which kind a query consumes (printing mode, `orderby=rarity`, pages and both
directions):

| group | n | charged / examined | realized ns per reported printing |
| --- | --: | --: | --: |
| plane-only | 56 | 0.50 | **1.069** |
| postings-only | 8 | **124.43** | **3.792** |
| mixed | 32 | 0.33 | 1.413 |

`COMPOSE_WALK_STEP_NS` ships at **0.58**, so it is under both, and the two differ **3.5x** from each
other. This is the shape a constant *can* fix: the error is flat across a 10x corpus axis (the
`charged/examined` ratio measured exactly 0.67 at 0.5x, 1x, 2x, 3x, 4x and 5x), which is what a
mis-levelled rate looks like and what a missing feature does not.

**Fix:** a separate rate for plane-bucket steps and entry steps. That needs the two counted separately —
`ComposePageWork.printings_examined` currently sums both — so a second counter is the prerequisite,
exactly as a realized counter was the prerequisite for grading `printings_walked`.

## The larger error, and it points the other way

`orderby_walk_scan = n_printings` charges a full corpus pass for *every* rarity-ordered compose query. For
`r:special` / `r:bonus` the walk touches only a short postings list and never ANDs a plane at all, so the
charge is **124x** over.

That one needs no popcount and no new counter, because it is a filter-**shape** question: a rarity
equality on a postings int (`special`=4, `bonus`=5) cannot consume a plane bucket, and the acquire can see
that from the filter it already holds. The floor for those queries is the postings length, not the corpus.

The two errors run in opposite directions, which is why the aggregate read a flat 0.67 and hid both — the
plane population is 2x under, the postings population 124x over. Splitting the population was the
necessary step; a median could not have shown it.

## Order

1. **The postings shape check** (124x, no prerequisite, small).
2. **A plane-step counter**, then the rate split (3.5x, flat, needs the counter first).

Both gate on the compose-paging slice, not the total.

## Status

The table is measured (96 cells, production corpus). The crossover arithmetic is arithmetic — 1,519 words
against a printing count — not a timing, so if someone wants to move mythic to postings anyway, the thing
to measure is a forced plane-bucket AND against a forced postings walk at equal selectivity. I would not
expect it to change the conclusion at 6x.
