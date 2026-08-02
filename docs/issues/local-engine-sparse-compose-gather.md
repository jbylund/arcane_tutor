# Sparse compose should gather, not decline

`printing_compose_fastpath` throws away a completed compose on every sparse permutation query and
makes the router redo the same work with a full scan. It does not have to. The replacement is
**verified byte-identical over 127,640 queries** and is one line.

It is nonetheless **blocked**, twice measured, and the blocker is not the change itself — it is that
`plan_cost` cannot price the path the change routes to. Written up separately from
[the cost-model work](local-engine-cost-model-agreement.md) because it is one shippable idea with a
clean acceptance test, waiting on a prerequisite that lives elsewhere.

## The change

```rust
Some(perm) => {
    if total <= *STREAM_MIN_MATCHES {
        return None; // sparse: the general path gathers + globally sorts, ordering ties differently
    }
    walk_grouped_page(ctx, params, &pbits, perm)
}
```

becomes `gather_composed_page(ctx, params, &pbits)` in place of the `return None`.

**Why the stated reason does not apply.** The decline exists because `walk_grouped_page` orders ties
differently from the general path. `gather_composed_page` does not: it pages through the same bounded
`GatherSelect` with the same comparator. That is exactly why the permutation-free branch immediately
below needs no small-total decline of its own, and its comment says so —

> the gather fallback pages via the bounded GatherSelect, whose tie-break matches the general path
> exactly (same GatherSelect, same comparator) — so no separate small-total decline is needed.

**Why it is cheap precisely here.** `gather_composed_page` walks `bitmap_card_ids` of the composed
set — only cards holding a matching printing — so it is O(total), and `total <= STREAM_MIN_MATCHES`
is the condition. Add one O(n_printings/64) projection. Declining instead discards the finished
compose and pays `prepare_candidates` plus a full candidate scan to recompute it.

## Correctness: verified, not asserted

The suite passing is not evidence here, because the claim is about returned ROWS on a path the suite
may not cover. Rows were captured before and after and compared directly:

| | |
| --- | --- |
| compose-acquire queries compared | **127,640** |
| identical result totals | **100.0000%** |
| identical rows, in order | **100.0000%** |
| differences | **0** |

## Why it is blocked

Those queries previously declined. **A declining plan accumulates no trials**, so they were absent
from every cost measurement ever taken here. Enabling the path introduces a population nothing had
measured — and the compose arm handles it badly:

| | p10 | p50 |
| --- | --: | --: |
| `PrintingCompose` estimate/real, before | 0.64 | 0.93 |
| after enabling the gather | **0.14** | 0.74 |
| after adding an explicit `n_printings/64` projection term | 0.23 | 0.77 |

All three `Gather` terms scale with the RESULT, while the projection runs whatever the result size, so
a sparse query predicts almost nothing. Under-costed compose is then over-picked, and routing gets
worse — twice:

| attempt | routing regret |
| --- | --- |
| first | **+0.445 µs**, 95% CI [+0.129, +0.829], 187 worse / 145 better |
| retried after compose's artwork arm was corrected | **+0.377 µs**, CI [+0.123, +0.709] |

The retry matters because it **falsified the obvious theory**. The change makes compose cheaper in
every mode, which is right for printing (under-picked 10:1) and wrong for artwork (over-picked 38:1).
Artwork is now priced correctly and the retry still loses by nearly as much, so mode-specific
mispricing was not the blocker, or not the only one.

## Two traps, both paid for

- **An A/B on this change can be inert.** The first latency comparison measured "no difference" —
  because the cost model still returned `INFINITY` for those queries, so the router never picked
  compose and the engine change did nothing at all. Change both halves before measuring anything.
- **Removing the infinities exposes a pre-existing p99 of 178x OVER-cost** in the compose arm, which
  was always there and hidden behind `inf`. The arm is wrong in both directions on this population.

## Prerequisite

Price the compose `Gather` path. This is not a one-term tweak: the arm is under by ~4-7x at p10 and
over by 178x at p99 on the population the change exposes. Progress on the compose arm generally is
tracked in [the cost-model doc](local-engine-cost-model-agreement.md); this change should be retried
whenever that cell reads sanely.

## Acceptance

1. `PrintingCompose` estimate/real p10 ≥ 0.6 on the sparse-gather population, from
   `scripts/bench_cost_error_percentiles.py --mode uniform`.
2. Paired routing not worse: `scripts/bench_plan_misselection.py --compare`, CI including or below
   zero.
3. Rows still identical — re-run the before/after row capture; it should stay at 0 differences.

The rows check is cheap and should be repeated rather than trusted from this document, since the
comparator or the paging strategy may have moved since.
