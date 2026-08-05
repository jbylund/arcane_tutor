# The rarity orderby walk's cost model, after the layout change

The rarity walk's *structural* defect — a plane bucket yields pids in pid order, so
`collect_orderby_page` takes the whole bucket, and ascending collects **24,653 matches to serve 60 rows**
at 115-154 µs — is not rarity-specific. It is the same defect the range indexes have, and the fix is one
layout for both: [local-engine-value-major-sort-indexes.md](./local-engine-value-major-sort-indexes.md).
The measurements that establish it live there.

This doc keeps only what survives that change: two cost-model errors that are specific to rarity, both
measured, running in opposite directions.

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

1. **Tiebreak-ordered postings for `common`** — the biggest win by far, and it is an executor change
   rather than a cost-model one: 115-154 µs becomes ~2-3 µs on the commonest shape (ascending, broad
   filter). ~110 KB. Then `mythic` for the descending/`r:mythic` case, ~36 KB.
2. **The postings shape check** for `r:special`/`r:bonus` (124x over, no prerequisite, small).
3. **A plane-step counter**, then the rate split (3.5x, flat, needs the counter first).

Note that (1) changes what (3) is measuring — a walk that stops early no longer pays a whole plane AND
per bucket, so the plane-step rate matters less once (1) lands. Do (1) first and re-grade before
touching the rate.

Row identity is the gate for (1), not regret: it changes which rows a page contains if the tiebreak
order is wrong, and `force_plan_differential_agreement` asserts full row order against `GatheredScan`
across every plan. (2) and (3) are cost-only and gate on the compose-paging slice.

## Status

The two tables are measured on the production corpus — the per-direction overshoot (8 cells, 15 trials)
and the rate split (96 cells). Nothing is implemented.

The one number that is arithmetic rather than a timing is the ~4% crossover for where a tiebreak-ordered
postings walk stops beating a plane AND. If that matters to a decision, measure a forced plane-bucket AND
against a forced postings walk at equal selectivity; it will not affect (1), where the filters are broad
and the win is 40x.
