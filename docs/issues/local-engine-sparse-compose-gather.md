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

## Prerequisite: what the 0.40× actually is

Price the compose `Gather` path. Diagnosed 2026-08-04, features before rates, over 33–38 cells with the
decline toggled off (15 trials each).

**Features first, and they are a minority of it.** Each priced feature against its own realized counter:

| feature | realized counter | median used/realized |
| --- | --- | --: |
| `eval_domain` | `cards_visited` | 0.70× |
| **`compose_scan_printings`** | `printings_examined` | **0.15×** |
| `matches` | `matches_pushed` | 0.88× |

`compose_scan_printings` is off by 6.7×, and `gather_composed_page`'s own comment already predicted
exactly this: the feature is the composed bitmap's **popcount**, on the grounds that compose "walks the
set bits", but the loop iterates `start..end` of every candidate card and bit-tests each printing, so
the realized quantity is the candidate cards' **span**. It is the same printings→distinct-rows
projection error behind `eval_domain`'s 0.70×.

An ORACLE re-pricing — realized counters substituted, every shipped rate untouched — settles the
sequencing:

| features | median pred/meas |
| --- | --: |
| shipped | **0.40×** |
| `compose_scan_printings` corrected alone | 0.47× |
| all three realized | **0.57×** |

So perfect features close about 40% of the gap and leave 1.75×. That is the **opposite** of the P3/P4
result in [the loop-phase doc](./local-engine-loop-phase-measurement.md), where the oracle reached 83%
and features were the whole story — worth noting before reusing that conclusion on a different arm.

**What is left is a missing term, not a level error.** The oracle column spans 0.32–0.79, so no single
multiplier fits it. Taking `meas − oracle_pred` gives a residual of **7,639 ns median** (stdev 1,720)
against a `COMPOSE_FIXED_COST_NS` of 163.56 ns. Re-running the whole measurement on a one-third corpus
(32,402 printings) gives 4,210 ns — a factor of 1.81 where the corpus factor is 3.0, so it is neither
fixed nor proportional. A two-point fit:

    residual ≈ 2,496 ns  +  0.0529 ns/printing   (3.39 ns per 64-bit word)

Both halves are plausible as *unmodelled work* rather than mis-levelled rates. `compose_printing_bits`
allocates a full-width printing bitmap and ANDs each child into it — O(`n_printings`/64) per leaf,
charged nowhere, since `popcount_words` counts the **result-space** bitmap, not the printing-space
build. And `bitmap_card_ids` walks the whole card bitmap to extract set ids whatever the popcount.

Two caveats before anyone moves a constant on this. It is a **two-point fit**, and the loop-phase doc's
own saturation finding is the standing warning against trusting those — a third size is the next
measurement, not the constant. And the older reading here ("under by ~4-7× at p10 and over by 178× at
p99") spanned a wider population including broad queries; the two need reconciling, because a term
added to fix the sparse end will move the broad end too.

### Attempted and held: deriving the span feature from the candidate count

`compose_scan_printings = scan_units` (the candidate span `GatheredScan` already estimates) in place
of `printing_matches * COMPOSE_GATHER_SPAN_PER_MATCH`. It does what it claims — the feature grades
0.15x -> **0.46x** of realized, and the shape becomes per-candidate instead of per-match, which is what
the quantity actually is.

**It is held, because routing got worse: regret 1.33 -> 1.41 us** (same-session baseline, so the 0.08
is outside the ~0.03 run-to-run spread). And the cost barely moved, 0.40 -> **0.41x**, because bit
tests are only 11% of the modelled page cost — the earlier "worth 0.40 -> 0.47" came from substituting
a *perfect* counter, which `scan_units` at 0.46x does not deliver.

The lesson is the sharper version of this branch's recurring one. Compose is under-priced by 2.5x
overall; making one of its features honest while the level stays wrong does not move the level, it
moves compose's *relative* position — and it was winning those argmins correctly while under-priced,
so raising one term only loses them. **Partial accuracy on an argmin can be worse than consistent
inaccuracy.** The span fix should land together with the missing ~7.7 us term, not before it. Patch
kept at `scratchpad/span_fix.patch`.

### The two constants are approximating something exactly computable

`COMPOSE_CARD_ESTIMATE_BIAS` (1.78) and `COMPOSE_CANDIDATE_SPAN_BIAS` (2.1) both correct
`balls_into_bins`, which is *uniform* occupancy — `domain * (1 - e^(-k/domain))`, every card an equally
likely bin. Cards are not equally likely: a card is a candidate iff one of its `S` printings matches,
so selection is size-biased. On this corpus the mean card holds 3.09 printings but the size-biased mean
`E[S^2]/E[S]` is **42.30**, and the realized printings-per-candidate runs ~13 — between the two,
because `k` is not infinitesimal. No single constant spans that.

The size-aware form needs no fitted constant, only the printing-count histogram (one small table, built
once at load):

    E[candidate cards] = sum_c [1 - (1 - k/N)^S_c]
    E[candidate span]  = sum_c S_c * [1 - (1 - k/N)^S_c]

Both reduce to `balls_into_bins` when every `S_c` is equal, and both saturate correctly at `k = N`.
Graded against exact truth (k from the printing-mode total, candidates from the card-mode result, span
from the corpus) over 19 queries:

| | size-aware | shipped |
| --- | --: | --: |
| candidate cards, median est/true | **1.10** | 0.72 |
| candidate span, median est/true | **~1.1** | ~0.36 |

Better centred on both, and with no constant to refit when the corpus changes. **But not uniformly
better**: the span reads 9.85x on `usd<=0.02` and 4.73x on a narrow date range, because those
predicates are *anti*-correlated with card size — the cheapest printings sit on small cards, so
size-biased selection over-predicts. The uniform model is wrong in one direction, the size-biased one
in the other, and neither knows which predicate it has.

### Which makes the exact route the interesting one

`RangeCardCounts` already answers distinct cards **exactly** for a range — and its `distinct_cards`
returns `None` for an *interior* range, because "distinct counts do not subtract". That hole is exactly
the two-sided population the range fusion just created, so those queries fall back to the estimator
above precisely where an exact answer exists for their one-sided siblings.

No 1-D prefix array closes it: whether a card has a printing in `[lo, hi)` is a 2-D question. But there
is an exact structure. A card with values `v1 < ... < vk` has NO printing in `[lo, hi)` iff the interval
fits inside one of its `k+1` gaps, so

    cards missing the range = # gaps containing [lo, hi)     (at most one per card)

over ~`n_printings + n_cards` gap intervals — a containment/dominance count, answerable exactly in
O(log^2) with a merge tree, not a prefix sum. Weighting each gap by its card's printing count makes the
**same** structure return the exact span as well. One structure, both features exact, both constants
deleted, and the interior-range hole closed. That is a bigger change than a calibration and belongs in
its own doc if it is taken up.

Progress on the compose arm generally is tracked in
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
