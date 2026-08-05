# Compose's two walk branches, graded separately

Branched off `engine-loop-phase-measurement` at `c11ad25`, which shipped the Gather arm's missing build
term. That work left `Perm` and `OrderbyWalk` unaddressed and named them one problem. Measured, they
are three, and only one of them is what it looked like.

Context and the corpus-axis method:
[local-engine-sparse-compose-gather.md](./local-engine-sparse-compose-gather.md).

## The walk feature was never graded, and a counter for it already existed

`printings_walked` (`page_span / match_rate * WALK_LENGTH_BIAS`) is what both branches multiply by
`COMPOSE_WALK_STEP_NS`. `ComposePageWork` documents `cards_visited` as "permutation entries consumed for
the forward walk" and `printings_examined` as the printings bit-tested, so the counter was there the
whole time. Graded over 190 Perm cells:

| feature / counter | p10 | p50 | p90 |
| --- | --: | --: | --: |
| `printings_walked` / `cards_visited` | 2.65 | 4.53 | 39.49 |
| **`printings_walked` / `printings_examined`** | 0.17 | **1.17** | 1.75 |

It tracks `printings_examined`, which the name already said. So the feature is not badly biased — the
walk steps cards and examines each card's whole span, and ~4.5 printings per card is the corpus mean.

## Split by branch and by sort column, across a 10x corpus axis

`charged / printings_examined`, median, where `charged` is what the arm multiplies
(`max(printings_walked, orderby_walk_scan)`):

| corpus | Perm | OW/usd | OW/rarity |
| --- | --: | --: | --: |
| 0.5x | 1.25 | 0.42 | **0.67** |
| 1.0x | 1.12 | 0.88 | **0.67** |
| 2.0x | 1.22 | 0.44 | **0.67** |
| 3.0x | 1.26 | 0.30 | **0.67** |
| 4.0x | 1.36 | 0.23 | **0.67** |
| 5.0x | 1.37 | 0.18 | **0.67** |

Three different diagnoses, and the branch name hides two of them: `OrderbyWalk` charges
`orderby_walk_scan` (= `n_printings`) for a rarity orderby and `printings_walked` for usd, so it is two
features under one label and has to be graded as two.

### Perm: the feature is fine; the arm's drift is a cache effect that saturates

The feature is nearly flat (1.12-1.37 over a 10x range) while the ARM drifts 1.75x
(pred/meas 1.259 -> 0.721). So the feature is not the cause. Decomposing: predicted grows exactly 10x
across the axis while measured grows **14.6x** (21 us -> 306 us). The extra is superlinear cost in the
executor, not a missing term — the working set outgrows a cache level — and it is why adding the build
term shifted the curve without flattening it (drift 1.75x -> 1.76x).

**So Perm does not need fixing for this corpus.** It reads 1.185 at production scale, and the drift is
only visible at 3-5x. Recording it because the next person to see 0.72 at 5x will otherwise go looking
for a missing feature that is not there. If the corpus ever grows that far, the answer is a
cache-aware rate, not a new term.

### OW/rarity: the charge has the wrong SHAPE, and the right one is discrete

Exactly 0.67 at every corpus size is not a coincidence and not a constant to fit. `walk_rarity_orderby_page`
walks rarity buckets in sort order, and the interior rarities are **planes** — each plane bucket ANDs
`wpp` words of the whole corpus however few matches survive, so the walk costs
`(buckets consumed) x n_printings`. `orderby_walk_scan` charges exactly one.

Swept over pages and both directions (160 cells) the quantity is **discrete**:

    corpus widths consumed:  0 -> 31 cells,  1 -> 83,  2 -> 10,  3 -> 22,  4 -> 14

p50 is 1.00, so today's charge is right at the median and wrong in the tail — 27% of cells consume 3-4
widths and are undercharged 3-4x, and 19% consume none and are overcharged. (An earlier read of "1.5x,
fit a constant" came from sampling only `limit=60, offset=0`; sweeping the page is what exposed the
distribution. A median is not a shape.)

**A model was the obvious fix. It was tried and it does not work.** The walk consumes buckets in sort
order until it has `page_offset + limit` rows, so the fraction of buckets touched should track the
fraction of the result set skipped: `ceil(n_planes * page_span / total)`, needing no popcount and only
quantities the acquire already holds. Scored against realized widths over 168 cells:

| | mean abs err | p50 | p90 | exact |
| --- | --: | --: | --: | --: |
| **shipped (flat 1)** | **0.80 widths** | 1.00 | **2.00** | 83/168 |
| `ceil(4 * span/total)` | 0.86 | **0.00** | 3.00 | 88/168 |

Better at the median, worse in the tail, worse on the mean. The even-spread assumption is what fails:
rarity is heavily skewed, so a page at offset 0 **ascending** lands entirely inside the common bucket
and needs one width, while **descending** starts at mythic — tiny — and needs several before the page
fills. Direction, not page depth, is doing most of the work, and `page_span / total` cannot see it.

So the flat charge of 1 is a defensible estimator (exact on 83/168, mean error 0.80 widths) and this is
**not the easy win it looked like**. Predicting it properly needs the per-rarity match counts, which
cost a popcount per plane — the walk's own price — so the honest options are to leave it, or to make
the executor report a realized bucket count and revisit with a direction-aware model.

### OW/usd: a genuine feature drift, and the only one that gets worse with scale

0.88 at 1x, degrading monotonically to 0.18 by 5x — the feature progressively under-counts as the
corpus grows, by 5x over the range. Unlike Perm's, this is the feature itself drifting, so it is
fixable rather than a rate story. Not yet diagnosed; the usd walk steps value runs in the price index,
and `printings_walked`'s uniform-density assumption is the obvious suspect.

## Where this stands

Nothing shipped on this branch yet. Order to take it in:

1. **OW/usd's drift.** Now the only one of the three that is both a real feature defect and worsens
   with corpus growth (0.88 at 1x, 0.18 at 5x). Undiagnosed; `printings_walked`'s uniform-density
   assumption over the price index is the obvious suspect.
2. **OW/rarity.** Attempted and declined — see above. Revisit only with a direction-aware model, and
   only if the tail (27% of cells undercharged 3-4x) shows up in regret. It has not yet.
3. **Perm.** Leave it. 1.19 at production scale, and the drift is cache, not model.

Each needs its own regret gate. This branch has twice shown that a partial accuracy fix on the compose
arm loses regret — the `compose_scan_printings` span patch (1.33 -> 1.41 us) and the build term applied
build-wide (Perm 1.193 -> 1.544 at production scale).
