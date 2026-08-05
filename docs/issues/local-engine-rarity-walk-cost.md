# The rarity orderby walk collects whole buckets, and ordering the buckets fixes it

`walk_rarity_orderby_page` walks rarity buckets in sort order. The four interior rarities
(`common`=0, `uncommon`=1, `rare`=2, `mythic`=3) are one-hot **planes**; the sparse tail
(`special`=4, `bonus`=5) is **postings** ([planes.rs:184](../../card_engine/src/planes.rs#L184)).

A plane bucket yields its pids in **pid** order, so `collect_orderby_page` must take the bucket whole and
sort it. Measured, printing mode, `limit=60 offset=0`:

| query | dir | total | **pushed** | examined | widths | measured |
| --- | --- | --: | --: | --: | --: | --: |
| `border:black` | **asc** | 85,046 | **24,653** | 97,216 | 1.0 | **115.5 µs** |
| `border:black` | desc | 85,046 | 337 | 391 | 0.0 | **2.5 µs** |
| `f:modern` | **asc** | 73,783 | **22,853** | 97,216 | 1.0 | **142.3 µs** |
| `f:modern` | desc | 73,783 | 315 | 391 | 0.0 | 40.6 µs |
| `usd>0.01` | **asc** | 81,534 | **25,418** | 97,216 | 1.0 | **153.6 µs** |
| `usd>0.01` | desc | 81,534 | 249 | 391 | 0.0 | 32.8 µs |
| `r:mythic` | asc | 8,924 | 8,924 | 388,864 | **4.0** | 44.8 µs |
| `r:mythic` | desc | 8,924 | 8,924 | 97,607 | 1.0 | 43.7 µs |

**Ascending collects the entire `common` bucket — 24,653 matches to serve 60 rows, a 411x overshoot — and
runs 46x slower than the same query descending**, which fills from the sparse bonus/special postings and
examines 391 entries. `r:mythic` ascending is the other shape: it ANDs common, uncommon and rare finding
nothing, then takes all of mythic, for four corpus widths.

## The fix: tiebreak-ordered storage for the bucket a walk STARTS in

Give the relevant rarity a postings list sorted by the full tiebreak, alongside its plane
([the same layout proposed for range indexes](./local-engine-range-index-value-major.md)). The walk then
bit-tests entries in page order and **stops when the page fills** — ~60-70 entries for a broad filter,
against 24,653 collected today.

Which buckets need it follows from the table, and it is not the one I first guessed:

- **`common` is the priority.** Ascending always starts there, and that is the 115-154 µs case.
- **`mythic` second**, for descending — though descending already examines only 391 entries when
  bonus/special fill the page, so this matters mainly for filters those two cannot satisfy (`r:mythic`
  itself, at 43.7 µs).
- `uncommon`/`rare` are only reached when an earlier bucket fails to fill, so they can wait.

Dual storage, kept deliberately: the plane is still what `rarity_cmp_leaf_bits` reads on the FILTER path,
where a whole-bucket bitmap is exactly what compose wants. The postings list serves the WALK path, where
early stop is what matters. ~110 KB for common, ~36 KB for mythic. And as you note, special/bonus need no
plane at all — their bits can be twiddled in from postings when a filter wants them.

**Retracted: "postings for mythic would be ~6x slower."** An earlier revision of this doc closed the idea
off on that arithmetic — 1,519 plane words against 8,924 postings entries. That compares
enumerate-the-whole-bucket both ways, which is the wrong comparison: the entire point of tiebreak ordering
is that the walk does not enumerate the whole bucket. With early stop the crossover moves to roughly "does
the filter match more than ~4% of this rarity", which nearly every query clears. The plane only wins when
the filter is so sparse within the rarity that the walk drains the whole postings list anyway.

**This is separate from the `r:mythic`-ordered-by-**usd** case**, which walks the price index and never
consults a rarity structure —
[that one is a paging-branch choice](./local-engine-compose-paging-cost-based.md).

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
