# The rarity orderby walk's cost model, after the layout change

**Resolved by the layout change, and by neither fix proposed below.**
[local-engine-value-major-sort-indexes.md](./local-engine-value-major-sort-indexes.md) shipped, and both
errors this doc measured were errors about *plane buckets*. The rarity walk no longer reads planes at
all: it steps a `PrintingValueIndex` one entry at a time, like the usd walk.

- The **rate split** (below, 3.5x) priced a plane-bucket step against an entry step under one constant.
  There is one operation now, so there is nothing to split.
- The **124x `special`/`bonus` over-charge** was `orderby_walk_scan = n_printings`. That field is
  deleted; the walk is priced by `printings_walked` alone, which now grades flat across a 10x corpus
  axis.

Kept as a record of how the population had to be split to see either error — the aggregate read a
dead-flat 0.67 because the two ran in opposite directions, and a median could not have shown it. The
analysis below is as measured, before the change.

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

## Order it was going to be taken in, and what happened instead

1. **Tiebreak-ordered postings for `common`** — shipped, but as one value-major layout for every key
   rather than a special case for the commonest bucket, which is what
   [the layout doc](./local-engine-value-major-sort-indexes.md) argued and measured. 115-154 µs became
   0.4 µs, not the ~2-3 µs projected here.
2. **The postings shape check** for `r:special`/`r:bonus` — not needed; the field it corrected is gone.
3. **A plane-step counter, then the rate split** — not needed; there are no plane steps in the walk.

The note here that (1) would change what (3) measured turned out to understate it: (1) removed (3)
entirely.

## Status

Resolved. The two tables were measured on the production corpus — the per-direction overshoot (8 cells,
15 trials) and the rate split (96 cells) — and both describe an executor that no longer exists.

The one number that was arithmetic rather than a timing, the ~4% crossover for where a tiebreak-ordered
postings walk stops beating a plane AND, was never needed: the layout change made the walk's structure
independent of the plane/postings crossover, which now governs only the FILTER path.
