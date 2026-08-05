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
against a `COMPOSE_FIXED_COST_NS` of 163.56 ns.

### Measured over a controlled 10x corpus axis: it is per-printing, and there is no fixed part

`upscale_corpus.py --copies N` replicates the corpus with rewritten identities, so the value
distribution is preserved exactly and both `n_printings` and `n_cards` scale by N. Fractional steps need
a partial copy sampled by **oracle card**, not by printing — thinning each card's span would change the
printings-per-card distribution, which is the quantity under test. `CARD_ENGINE_STREAM_MIN_MATCHES`
scaled with the corpus keeps the sparse population from evaporating as results scale (without that, the
cell count fell 33 -> 26 -> 13 and the 4x stdev was 5x its median).

Nine sizes, 0.5x to 5x, 33 cells each:

| n_printings | bitmap | residual | fitted | meas/fit | ns/printing |
| --: | --: | --: | --: | --: | --: |
| 48,161 | 6 KB | 4,045 | 3,914 | 1.033 | 0.0840 |
| 97,206 | 12 KB | 7,597 | 8,009 | 0.949 | 0.0782 |
| 194,412 | 24 KB | 15,276 | 16,126 | 0.947 | 0.0786 |
| 339,779 | 41 KB | 28,724 | 28,264 | 1.016 | 0.0845 |
| 486,030 | 59 KB | 40,459 | 40,475 | 1.000 | 0.0832 |

    residual = -107 ns  +  0.0835 ns/printing   =  5.34 ns per 64-bit word     R^2 = 0.99832

**The intercept is zero within noise.** There is no missing *fixed* cost — the whole 7.6 us is a
per-corpus-width build term. And `ns/printing` varies only **1.13x across a 10x range with no trend**,
which rules out the cache story worth worrying about here: the printing bitmap goes 6 KB (L1) to 59 KB
(L2) over this sweep without the rate moving, so linear is the right shape and 5.34 is not
corpus-specific.

**Retracted: the earlier two-point fit** (2,496 ns fixed + 0.0529 ns/printing). Its small point was
`head -32402` of the corpus — the first third of the file, not a uniform sample — and the spurious fixed
term came entirely from that. Sampling by card is what fixed it.

The mechanism matches: `compose_printing_bits` allocates a full-width printing bitmap and ANDs each
child into it, `printing_bits_to_card_bits` projects it, and `bitmap_card_ids` walks the card bitmap to
extract set ids — all O(corpus width), all charged nowhere, since `popcount_words` counts only the
**result-space** bitmap.

### But it cannot be added to the shared build section alone

`compose_printing_bits` runs for every compose execution, so the physical argument says the term belongs
in the build and applies to `Perm`/`OrderbyWalk` too. Measured over ten sizes, that is wrong — and
wrong in a way only the many-point sweep shows. `PrintingCompose` `predicted/measured` on the Perm
population, with and without the term applied:

| corpus | pred/meas | + build term |
| --- | --: | --: |
| 0.5x | 1.244 | 1.482 |
| **1.0x** | **1.185** | **1.449** |
| 2.0x | 0.965 | 1.198 |
| 3.0x | 0.916 | 1.152 |
| 3.5x | 0.838 | 1.035 |
| 4.5x | 0.834 | 1.046 |
| 5.0x | 0.855 | 1.061 |

**Both curves SATURATE past ~3.5x** — the same phenomenon [the loop-phase
doc](./local-engine-loop-phase-measurement.md) already records, and the reason a linear term shifts this
curve instead of flattening it. With the term the asymptote is ~1.045 (right) against ~0.845 (18% under)
without, so the term is real here as well. But at the **production** size it makes Perm worse, 1.185 ->
1.449, which is what happens when an arm's rates were fitted with the cost already absorbed into them.

### It is not only Perm, and the three branches disagree about what is wrong

The sweep above conflated `Perm` with `OrderbyWalk`, and priced `Gather` by a different route (a residual
on oracle features rather than `predicted/measured` on shipped ones), so the three were never comparable.
Re-measured with one methodology — shipped features, `predicted_ns / plan_self_ns`, split by branch:

| corpus | Perm (bare / +term) | OrderbyWalk | Gather |
| --- | --: | --: | --: |
| 0.5x | 1.259 / 1.630 | **0.571** / 1.243 | 0.544 / 0.992 |
| 1.0x | 1.193 / 1.544 | 0.848 / 1.317 | 0.553 / 0.952 |
| 2.0x | 0.926 / 1.223 | 0.953 / 1.358 | 0.540 / 0.944 |
| 3.0x | 0.851 / 1.064 | 0.959 / 1.444 | 0.543 / 0.887 |
| 5.0x | **0.721** / 0.924 | **0.997** / 1.314 | 0.518 / 0.876 |

- **`Gather` is a clean LEVEL error** — flat at 0.52-0.55 across the whole 10x range, no scale dependence
  whatsoever — and the build term fixes it (0.88-0.99). It is the branch the term was measured on and the
  only one where it behaves as designed.
- **`Perm` drifts DOWN 1.75x** (1.259 -> 0.721), and the term shifts the curve while leaving the same
  1.76x drift. Perm's defect is not the build cost.
- **`OrderbyWalk` drifts UP 1.75x** (0.571 -> 0.997) — the OPPOSITE sign. Nearly 2x under at half-corpus,
  correct at 5x.

**That rules out a shared refit.** Perm and OrderbyWalk both multiply `COMPOSE_WALK_STEP_NS` (0.58) and
`COMPOSE_WALK_EMIT_PER_ROW_NS` (2.19), and no single refit of shared constants flattens two curves running
in opposite directions. The shipped values are a compromise sitting where the two cross — near 1-2x, which
is exactly why they look acceptable at the production corpus and diverge either side of it.

And the cause is a FEATURE, not a rate, which is the rule this branch keeps re-confirming. The two
branches multiply the same rate by different quantities: Perm uses `printings_walked` (`page_span /
match_rate`, which does not scale with the corpus), while OrderbyWalk uses
`max(printings_walked, orderby_walk_scan)` and `orderby_walk_scan` **is** `n_printings` for a rarity
orderby. Same rate, differently-scaling features.

(`orderby_walk_scan` was deleted with the value-major layout — the rarity walk no longer ANDs a plane
per bucket, so both branches now multiply the rate by `printings_walked` alone. The diagnosis stands as
the reason the two diverged; the OrderbyWalk half of it is resolved. See
[done/local-engine-value-major-sort-indexes.md](./done/local-engine-value-major-sort-indexes.md).)

### So the sequencing

1. **Gather-scoped term.** Ready. Changes no production routing at all, because the Gather branch is
   declined in production — purely a prerequisite for the sparse-gather work.
2. **Perm and OrderbyWalk are separate investigations, features before rates.** The prerequisite for Perm
   is a realized counter for `printings_walked`: it is an estimate with nothing to grade it against,
   which is why an oracle pricing of that branch returns a stdev 15x its median. Same shape of blocker
   `compose_scan_printings` was for Gather.

A build-wide term plus a shared refit — the shape this was going to take — would have made the production
corpus worse on Perm (1.193 -> 1.544) while fixing its asymptote, and over-corrected OrderbyWalk at every
size. Ten points showed that; three would not have.

One older reading still needs reconciling: "under by ~4-7x at p10 and over by 178x at p99" spanned a
wider population including broad queries, so a term fixed on the sparse end will move the broad end too.

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
**same** structure return the exact span as well.

**Measured against a cheaper approximation, and the merge tree loses.** A binned start x end triangle
`T[i][j]` = distinct cards in bins i..j stores the union answer directly, so it subtracts where a
prefix array cannot. 100 equal-printing bins is 5,050 u32 = **20 KB per index**, against ~274 KB for a
wavelet tree over the gaps and ~8.8 MB for an explicit merge tree, and the query is a bisect plus two
reads rather than O(log^2).

Broad intervals bracket tightly -- `T[i+1][j-1]` is contained, `T[i][j]` contains, and the width is the
two boundary bins: **0.3% / 1.0% / 2.3% / 2.6%** on the four broad cases measured.

Narrow intervals collapse the bracket (fewer than three bins spanned means no interior, so the lower
bound is 0) and more bins cannot fix that. But the upper bound is the estimator, and it pairs with a
second free bound: `k`, the printings in the range, is the two partition points the range path already
computes, and a card needs at least one printing in range. Over 14 narrow intervals spanning both ends
of both value axes:

| estimator | median | max | direction |
| --- | --: | --: | --- |
| `T[i][j]` | 2.16x | 8.52x | never under |
| **`min(k, T[i][j])`** | **1.21x** | **1.41x** | never under |
| shipped | 0.72x | — | unbounded either way |

The two bounds are complementary for a structural reason worth keeping: equal-printing bins are narrow
in VALUE space exactly where printings are dense, so at the cheap end a bin is two distinct prices wide
and `T[i][i]` is exact (3 of 14 cells). Where values are sparse the bin spans 1,381 values against a
query's 173 and `T` is useless -- but that is the regime where printings-per-value is low, so cards ~ k
and the clamp takes over.

So the whole feature is 20 KB, O(1), bounded at 1.41x over on narrow and 2.6% on broad. **The arm is
under by 2.5x overall**, so buying exactness here is misallocated: the merge tree, and the O(k)
count-it-directly variant, both cost more to fix an error smaller than the one that remains.

Same shipping caveat as the span patch: `min(k, T)` is systematically OVER, which makes compose look
more expensive -- the direction that lost argmins compose deserved. It lands with the level fix, not
before it. And the 14 intervals are hand-picked; grade it over the regret matrix's traffic first.

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
