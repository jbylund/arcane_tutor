# Fusing a two-sided range into one index interval

Split out of [local-engine-loop-phase-measurement.md](./local-engine-loop-phase-measurement.md),
which found this while auditing plan-cost features and where the routing context lives. Shipped in two
commits — `4991759` for the narrowing, `7374e19` for the compose builders.

`narrow_rec`'s `And` arm narrows children independently and intersects the *results*, never the
bounds. `usd>=0.42 usd<=0.43` therefore has both halves match most of the corpus, each trip
`range_too_broad_to_narrow` (`MAX_NARROW_FRACTION` 0.25) on its own and decline — and that their
intersection is 837 printings is never discovered.

## The evidence that made it worth doing

All artwork, `orderby=toughness`, limit 10, before the change:

| query | results | `eval_domain` | P3 `printings_examined` | best measured |
| --- | --: | --: | --: | --: |
| `usd>=200` | 160 | **148** | 6,089 | **26.7 µs** |
| `usd<=0.02` | 103 | **100** | 339 | **3.2 µs** |
| `cn>=20000` | 285 | **281** | 2,980 | **17.0 µs** |
| **`usd>=0.42 usd<=0.43`** | 837 | **31,508** | **97,206** | **1,146.8 µs** |
| **`cn>=100 cn<=101`** | 460 | **31,508** | **97,206** | **1,136.1 µs** |
| `usd>=200 t:creature` | 36 | **36** (`narrowed_repr=printings`) | 532 | **2.5 µs** |
| `usd>=0.42 usd<=0.43 t:creature` | 361 | 17,317 | 45,976 | 570.2 µs |

A one-sided range with **160** results narrows to **148** candidates and finishes in 26.7 µs. A
two-sided range with **837** results narrows to nothing and takes **43× longer for 5× more rows**. So
the shape a reader intuitively wants — walk the index slice, test the residual, accumulate the page —
**is** what the engine does for `usd>=200`; the two-sided form simply never gets there.

## What shipped

`fuse_and_range_children` groups an `And`'s children by which printing-range index they select on and
fuses each group of two or more into one half-open interval (`lo = max(lo_i)`, `hi = min(hi_i)`) before
the arm ranks anything. The interval is the exact conjunction of its constituents, which is what lets
one source stand in for all of them — including in `every_child_included`'s tightness accounting.

| query | before | after |
| --- | --: | --: |
| `usd>=0.42 usd<=0.43` | 1,146.8 µs, exam 97,206 | **76.2 µs**, exam 9,273 |
| `cn>=100 cn<=101` | 1,136.1 µs, exam 97,206 | **49.9 µs**, exam 5,604 |
| `usd>=0.42 usd<=0.43 t:creature` | 570.2 µs, evd 17,317 | **17.0 µs**, evd 356 |

**Regret cannot see this, which is why it was measured differently.** Fusion makes the plans that ran
faster without changing which one wins, and regret is a difference between plans — the same blind spot
that hid `r>=rare`'s missing plan and the sparse-gather decline. The measurement is paired routed
dispatch over 11,915 traffic queries, same seed and stream on both builds, sliced by whether the query
has two or more comparisons on one fusible field:

| slice | n | base | fused | ratio |
| --- | --: | --: | --: | --: |
| fusible | 577 | 111.6 ms | **95.9 ms** | **0.86** |
| the rest (fusion cannot touch it) | 11,338 | 915.6 ms | 897.6 ms | 0.98 |

Regret mean went 1.42 → 1.35 µs, with 5% more queries fitting the same 180 s budget.

**The untouchable slice is the noise gauge, and it is worth reading before believing any single row.**
Fusion cannot alter those 11,338 queries at all, yet they move 2% in aggregate and up to **±170 µs**
per query. Every apparent per-query regression in this data sits inside that envelope — including the
+166 µs on broad two-sided `cn`/`year` ranges that first looked like the gate below earning its keep.

### The gate is a scope decision, not a measured win

Fusion applies only where the fused interval is itself sparse. Fused-vs-gated is 0.88 against 0.86 of
baseline on the fusible slice, which the noise floor above makes indistinguishable — so the honest
claim is not that the gate is faster. What it buys is a bound: outside the sparse population where the
win *is* demonstrated, the change is a provable no-op, because the constituents pass through to their
own arms untouched.

It also removes a live question. Fusing a still-broad interval is not neutral: one broad source reaches
`range_narrowed` under a single `broad_ok` where two broad children each got their own, so the `And`'s
per-child skip logic stops deciding per child. The gate means that never happens, and as a side effect
`range_narrowed`'s `broad_ok` is unreachable from a fused source at all (a sparse `k` returns on the
vec path first) — which is why the fused source carries no negation flag despite `bare_range_bounds`
happily reducing `-usd<c` for it.

### Row identity

4,560 cells identical, hashing the returned `scryfall_id` sequence over three modes × five orderbys ×
four pages × two prefers: two-sided `usd`/`cn`/`date`/`year`, negated halves, three constituents on one
index, two indexes fused independently, unsatisfiable pairs, fused ranges under `Or` and `Not`, plus
one-sided and broad controls. Harness: `scratchpad/fuserows.py`.

The unsatisfiable case is load-bearing, not decoration. `usd>=1 usd<=0.5` fuses to `hi < lo`, and every
consumer computes `k` as `partition_point(hi) - partition_point(lo)` — that subtraction **underflows
and panics**. Clamping to `[lo, lo)` yields `k = 0`, which is what an empty range means and what every
consumer already handles.

## The compose half, and why it was not an estimator problem

The narrowing fusion left `compose_printing_bits` and `compose_printing_estimate` reading the unfused
shape, because both decompose `And` before any range guard. Their fold takes the **min** of the
children's match counts — an intersection upper bound, and a bad one here. (An earlier draft of this
doc said it *multiplied* the two sides. It does not: 33,862 is exactly `min(33,862, 48,559)`. The
summing is in `scatter_printings`, 82,421 = 33,862 + 48,559.)

| query | mode | estimated `matches` | true total | est/true |
| --- | --- | --: | --: | --: |
| `usd>=0.42 usd<=0.43` | printing | 33,862 | **879** | 38.5× |
| | artwork | 20,411 | **837** | 24.4× |
| | card | 14,281 | **763** | 18.7× |
| `cn>=100 cn<=101` | printing | 35,589 | **568** | 62.7× |
| **`usd>=200`** (control) | printing | 258 | 258 | **1.0×** |

**The control is the whole finding.** A *one-sided* range already estimates at 1.0×, because the leaf
arm reads `k` off two `partition_point` calls. So nothing here needed a better estimator — the exact
count was two binary searches away the entire time, and the fold simply never saw the interval. The
card-side projection is equally cheap: `range_card_counts_for` pairs each range index with an exact
`RangeCardCounts` table over the same interval, which is why `usd>=0.42`/card reads 12,408 against a
true 12,408.

Shipped by reusing `fuse_and_range_children` with `sparse_only: false`. The compose builders want the
fusion at every width, unlike narrowing: `range_leaf_bits` is an O(k) scatter, so one scatter of a
subset cannot lose to two scatters of its supersets plus an AND, and the estimate is exact rather than
an upper bound at any width. After: printing mode reads **1.0×**, card and artwork 0.6–0.9× — the same
ratios their one-sided forms already carry, since those project printings to distinct rows.

**The paging prediction follows for free, which is the part sparse-gather needed.** 879 is under
`STREAM_MIN_MATCHES`, so `compose_paging_with_total` now predicts `Decline` where it predicted `Perm` —
matching what the fastpath actually does — and the pick moves off `PrintingCompose` onto the plan that
was already answering. That removes the blocker named in
[local-engine-sparse-compose-gather.md](./local-engine-sparse-compose-gather.md): its `Decline` →
`Gather` flip regressed routing purely because `compose_paging_for` branches on `result_total`, the
estimate, and so could not tell a sparse query from a broad one. It can now.

Cumulative over both fusion commits, paired routed dispatch on the same seed and stream:

| | base | + narrowing | + compose |
| --- | --: | --: | --: |
| fusible slice (577 queries) | 111.6 ms | 95.9 ms | **90.5 ms (0.81×)** |
| the untouchable slice | 915.6 ms | 897.6 ms | 925.8 ms (1.01, noise) |
| regret mean | 1.42 µs | 1.35 µs | **1.30 µs** |

Row identity for this half: 3,120 further cells, aimed at what only the compose builders fuse — broad
intervals, each composable leaf kind (`border`/`r`/`f`/`set`/`watermark`/`type` and negations), two
indexes fused inside one compose, unsatisfiable pairs, an `Or` of two fused `And`s. Harness:
`scratchpad/composerows.py`.

## What is left

**The sparse gather was re-attempted and is still declined** — see
[local-engine-sparse-compose-gather.md](./local-engine-sparse-compose-gather.md). The accurate estimate
did remove its stated blocker (the prediction can now tell sparse from broad), and a second stale
blocker fell with it (the tie-ordering reason the code gives for the decline died with #815). What
remains is narrower and better quantified: the arm under-prices the gather to 0.27–0.53 of real, the
resulting mispicks cancel a real 1.3–2.7x win, and the change measures neutral in wall time and 16%
worse in regret.

**Watch `printing_compose`'s own slice.** Its share of routed queries went 27% → 31% and its mean
regret 1.65 → 1.80 µs across this commit, even as the total fell. More queries route there now and the
model prices them worse; that is the next thing to look at in the acquire, and it is the same cell the
rarity widening flagged.

**`eur` and `tix` cannot fuse at all**, because `resolve_numeric_range_leaf` covers only `price_usd` and
`collector_number` (plus `DateCmp`/`YearCmp` reaching `released_at` directly). That is the same root
cause as [local-engine-eur-tix-range-index.md](./local-engine-eur-tix-range-index.md), and fusion adds
one more reason to fix it: several top-100 regret rows are two-sided `eur:`/`tix:` ranges, which are
exactly the shape that gains most and currently gains nothing.

**Card-level numeric contradictions are separate work.** `usd>=1 usd<=0.5` short-circuits for free
here, but `power=3 power=5` does not reach this code: `resolve_numeric_range_leaf` returns `None` for
`Power`, and `printing_dependent` classifies `Power`/`Cmc`/`Toughness`/`Loyalty` as card-level,
evaluated through `numeric_candidates`. Detecting that contradiction is most naturally a bind-time
pass, and needs its own decision on `Ne` and null semantics.
