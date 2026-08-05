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

**The stated reason no longer applies to the WALK either — the comment is stale (2026-08-04).** #815
(`fe1afeb`, 2026-08-02) made row order total and filter-independent on `(key1, key2, cid, pid)`,
replacing the key-3 `prefer_score` that used to differ between the permutation (first *stored*
printing's score) and the gathered paths (first *matching* one). With `cid` and `pid` in the key there
is no tie left for the two emission shapes to break differently. Measured by toggling the decline off
and running the walk on tie-heavy orderbys (`power`/`toughness`/`cmc`, three modes, both directions,
deep offsets): **576 real invocations, 1,512 cells, 0 row differences**, plus 14 invocations inside
`force_plan_differential_agreement`, which asserts *full row order* against `GatheredScan` and passed.

That matters less for shipping than it looks — the doc's proposal was always the gather, which was
never blocked on tie order — but it retires the reason the code gives for the decline, and it means
either executor is now correct.

**Watch the vacuous A/B here.** The first attempt at this measurement showed 0 differences because the
cost model still predicted `Decline`, so compose never ran and neither arm of the A/B executed the
branch. Both halves must be toggled, and the fire count must be *counted*, not assumed.

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

## Re-measured 2026-08-04, after the two-sided range fusion

[local-engine-two-sided-range-fusion.md](./local-engine-two-sided-range-fusion.md) made
`compose_printing_estimate` exact on ranges, which removed the second stated blocker: the prediction
can now tell a sparse query from a broad one (879 against a true 879, where it read 20,411). Both
executors were then re-tried behind a runtime toggle on one binary.

**The gather really is faster on its population.** Warm, 15 trials, `PrintingCompose` against the best
plan that would otherwise win:

| query | mode | compose measured | compose predicted | best other |
| --- | --- | --: | --: | --: |
| `usd>=0.42 usd<=0.43` | artwork | **27.5 µs** | 13.5 | 75.0 |
| `cn>=146 cn<=147` | artwork | **20.5** | 8.4 | 46.1 |
| `set:lea` | artwork | **18.8** | 5.1 | 44.6 |
| `usd>=200` | artwork | **15.6** | 4.6 | 27.5 |
| `cn>=20000 r>=rare` | card | **13.9** | 4.3 | 17.6 |

1.3–2.7× faster than the alternative, and **under-priced 2–4× in every row** (0.27–0.53 of real). That
under-charge is the whole remaining problem, and it is narrow enough to be calibratable rather than
structural.

**And it is still not shippable, for a third reason that is neither of the first two.** The
under-pricing over-picks compose on queries where it does *not* win, and those mispicks cancel the wins:

| | decline | gather |
| --- | --: | --: |
| regret mean | 1.30 µs | **1.51 (+16%)** |
| `printing_compose` miss% | 7% | **18%** |
| `printing_compose` p90 | 0.00 | **8.12** |
| compose-acquire wall time, 2,341 paired queries | 113.6 ms | **113.2 ms (1.00)** |

**Read those two rows together, because separately each one lies.** Regret is a routing-error metric:
a correct pick scores zero, so enabling a plan that is faster on its own population shows up as pure
loss — the gains are invisible and only the new mispicks count. Wall time says the opposite is not true
either: the win is real but confined, and the mispicks eat exactly as much as it earns. The honest
summary is *neutral in time, worse in routing*, which is not a trade worth taking for added complexity.

(The permutation walk was also measured, since it is what the stale comment is about: regret 1.52 µs,
slightly worse than the gather, as expected — the walk pays per permutation entry and a sparse total
means stepping a long way to fill a page, where the gather is O(total).)

## Prerequisite

Price the compose `Gather` path. The 2026-08-04 measurement above narrows what that means: on the
sparse population the arm reads a consistent **0.27–0.53 of real** across every case measured, which is
a level error rather than a missing dependence on some feature — the ordering within the population is
right, the magnitude is halved. The older "under by ~4-7x at p10 and over by 178x at p99" spanned a
wider population that included broad queries; both readings should be reconciled before picking a term
to move. Progress on the compose arm generally is tracked in
[the cost-model doc](local-engine-cost-model-agreement.md).

## Acceptance

1. `PrintingCompose` estimate/real p10 ≥ 0.6 on the sparse-gather population, from
   `scripts/bench_cost_error_percentiles.py --mode uniform`.
2. Paired routing not worse: `scripts/bench_plan_misselection.py --compare`, CI including or below
   zero.
3. Rows still identical — re-run the before/after row capture; it should stay at 0 differences.

The rows check is cheap and should be repeated rather than trusted from this document, since the
comparator or the paging strategy may have moved since.

Add a fourth, because criteria 1–2 alone would have passed a change that buys nothing:

4. **Wall time better, not merely routing not-worse.** Paired routed dispatch on one binary with the
   decline toggled, sliced to the compose acquire. The 2026-08-04 run reads 1.00 — the plan has to earn
   more than it loses to its own mispicks, and regret cannot see the earning half.
