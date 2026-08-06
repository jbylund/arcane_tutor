# Materializing a sorted candidate list: bitmap beats sort, merge loses badly

**DONE for the range arms — merged in [#845](https://github.com/jbylund/sylvan_librarian/pull/845)** (layer
13 of the cost-model stack). Whole mix 0.949, target 0.867.

**The remaining call sites are [#849](https://github.com/jbylund/sylvan_librarian/issues/849)** —
`arith_tuple_narrow` (the case that prompted this benchmark) and the other posting-union arms. They are
card-space, so the ~490:1 domain:count crossover measured here has to be re-derived on that axis rather than
reused.

**Shipped for `range_narrowed` (the range arms).** See "What shipped" at the bottom; the crossover turned
out to be a domain:count RATIO, not a count, and the remaining call sites (`arith_tuple_narrow` and the
other posting-union arms) have not adopted it yet.

Any narrowing arm that unions several postings rows has sorted rows and no globally
sorted output, because posting-row order is not card order. `arith_tuple_narrow` is
the case that prompted this — it concatenates the selected rows and calls
`sort_unstable`, with a comment naming exactly why the sort is needed. The same
shape shows up anywhere disjoint sorted runs get combined.

Two alternatives need no information the caller does not already have: a k-way
merge (every run is already sorted), and a bitmap scatter followed by reading the
set bits back (sorted by construction).

Measured, all three asserted to produce the identical `Vec<u32>` first:

    cargo test --release bench_candidate_materialize -- --ignored --nocapture

`card_engine/src/bench_candidate_materialize.rs`. Synthetic inputs so the domain can
be varied, runs disjoint and scattered through the domain rather than contiguous,
which is the real situation.

## The bitmap wins from about 64 candidates up

Domain 31,508, 564 runs — the arith-tuple case:

| candidates | concat+sort µs | merge µs | bitmap+extract µs | best |
| ---------- | -------------: | -------: | ----------------: | ---- |
| 16         | **0.25**       | 0.33     | 0.42              | sort |
| 64         | **0.46**       | 1.00     | 0.46              | tie |
| 256        | 1.50           | 3.58     | **0.79**          | bitmap 1.9x |
| 1,024      | 5.25           | 16.08    | **1.42**          | bitmap 3.7x |
| 4,096      | 21.12          | 79.58    | **3.50**          | bitmap 6.0x |
| 16,384     | 80.83          | 381.00   | **13.54**         | bitmap 6.0x |
| 31,508     | 159.88         | 798.25   | **26.04**         | bitmap 6.1x |

## The k-way merge is the worst option at every size

This is the surprise. The merge is 3x to 5x slower than the sort it was supposed to
replace, and 30x slower than the bitmap at the top end. A `BinaryHeap` pays a
`log k` sift with a data-dependent branch and a cache-unfriendly access pattern per
element, where `sort_unstable` (pdqsort) on `u32` is close to optimal — branchless
partitioning over a contiguous buffer. Being handed pre-sorted runs does not help
enough to overcome that.

Axis C confirms the merge is the only contender that cares about run count, and it
is already losing: at 4,096 candidates it goes 43.5 µs at 8 runs to 82.0 at 2,048,
while sort stays 17–21 and bitmap 3.0–4.4.

**Do not build the merge.** That is the useful half of this finding.

## The bitmap's edge is a function of domain/count, not of count

The bitmap pays `domain/64` words regardless of the answer size, so its advantage
erodes as the space grows relative to the result. Holding candidates at 4,096:

| domain    | ratio  | concat+sort µs | bitmap+extract µs | best |
| --------- | -----: | -------------: | ----------------: | ---- |
| 31,508    | 7.7:1  | 18.12          | **3.17**          | bitmap 5.7x |
| 100,000   | 24:1   | 18.29          | **3.33**          | bitmap 5.5x |
| 300,000   | 73:1   | 18.29          | **5.83**          | bitmap 3.1x |
| 1,000,000 | 244:1  | 18.25          | **11.38**         | bitmap 1.6x |
| 3,000,000 | 732:1  | **18.17**      | 22.92             | sort 1.3x |

Break-even sits near 600:1. The card corpus is 7.7:1 for a 4,096-card result and
~31,508:1 only for a single-card result, so the engine is far inside the bitmap's
range for anything but a handful of matches. Printing space (97,206) does not change
that conclusion.

## The crossover is a ratio, not a count

The original recommendation below said "keep concat+sort below ~64 candidates", from axis A — which is
measured at the CARD domain (31,508, 493 words). `range_narrowed` materializes into PRINTING space
(97,206, 1,519 words), where the bitmap's fixed cost triples and the crossover moves to ~194. A count
constant is only ever right at one domain.

Axis G's fine sweep (12% steps, 2 confirmations) shows it collapses to a ratio:

| domain | words | measured crossover | ratio |
| --: | --: | --: | --: |
| 31,508 (cards) | 493 | 90 | 350:1 |
| 97,206 (printings) | 1,519 | **194** | **501:1** |
| 300,000 | 4,688 | 667 | 450:1 |
| 1,000,000 | 15,625 | 2,064 | 484:1 |
| 3,000,000 | 46,875 | 6,401 | 469:1 |

So `k * 490 > domain`, which is `MATERIALIZE_BITMAP_RATIO`. Same shape as `bitmap_beats_postings`'s
`k * 32 > n` — but that is a STORAGE crossover in bytes and this is a materialization-time one, so the
two constants are unrelated and should not be conflated.

## What shipped

`sorted_ids(ids, k, domain)` picks the route by the ratio above, and `range_narrowed` calls it instead of
`collect` + `sort_unstable`. Byte-identical output, so it is a pure cost choice with no consumer effect.

Per query, on the production corpus — `prep` is `ns_prepare`, which is where the sort lived:

| query | prep before | prep after | total before | total after |
| --- | --: | --: | --: | --: |
| `usd<0.18 t:land` | 134.0 µs | **54.5** | 140.3 µs | **60.8** |
| `usd<0.20 t:land` | 160.3 | **67.4** | 167.8 | **74.5** |
| `usd<0.20 t:creature` | 171.8 | **80.2** | 287.5 | **195.3** |
| `usd<0.20 c:g` | 162.6 | **70.7** | 203.3 | **111.8** |
| `eur<0.13 t:land` | 153.5 | **63.6** | 161.8 | **71.5** |

Interleaved A/B, 10 rounds, 1,362 queries, drift 0.978/1.010: range TARGET **0.867**, control 0.989, whole
mix **0.949**, p90 0.902. No regression above 1.07 and all of those are sub-20 µs queries.

This also retired the one regression from
[the breadth-denominator change](local-engine-range-breadth-denominator.md): `eur<0.13 t:land` went
208 µs before that change, 267 after it, and **71.5 µs** now.

Note `usd<0.18 t:land` was never affected by the denominator at all — it was already narrowing, and
already paying 134 µs to sort 13,328 ids. The sort cost was pre-existing across the whole mid band; the
denominator change only made more queries visit it.

## The remaining call sites: adopted, and measurably neutral

`numeric_candidates` and `arith_tuple_narrow` now call `sorted_ids` too. Both are **within noise on every
query sampled**, and the reason is worth recording so nobody re-measures it hoping for the range result:

- **`numeric_candidates` is mostly shadowed.** `cmc`/`power`/`toughness` are `BitPlanes`, so `split_planes`
  consumes them and this function is never reached for the queries that would have large `k`.
- **`arith_tuple_narrow` is already capped.** Its vec path only runs below `BITS_PROMOTE` (4,096) —
  past that the arm above already hands back `CardBits`. So the affected band is 65–4,096 ids in card
  space, worth 0.3 µs at the bottom and 16 µs at the top.

`range_narrowed` was the outlier because its vec path has **no such cap**: a mid-band price range
materializes up to 24,301 ids through the sort, which is where the 2.4× came from.

Keeping the adoption anyway: it is equivalence-checked (below), it costs nothing, and it removes the trap
if `BITS_PROMOTE` ever moves — which is this doc's own open question.

## Demonstrating the change respects the API

The two routes differ in exactly one way, and it is silent: **a bitmap dedups, a sort does not.** A caller
emitting an id twice gets a shorter vec from one route than the other, with no error.

Every current caller is duplicate-free by construction — a `PrintingValueIndex` holds one entry per
printing with a value, `build_numeric_index` one per card, and a card lives in exactly one `arith_tuple`
posting row — but that is a list that can go stale. So the debug build **runs both routes on every call
and compares them**, making the precondition a live check at every call site the test suite reaches
rather than a claim in a doc comment.

Verified live rather than assumed: sabotaging the bitmap route (dropping one id) fails **12 tests**
across the suite, including the fuzz row-identity differentials. Release builds pick one route and pay
nothing.

## What to change (original, for the remaining call sites)

The sorted-vec path only runs below `BITS_PROMOTE` (4,096) — above it the engine
already hands back `CardBits`. So the band this affects is roughly 64 to 4,096
candidates, where bitmap+extract is 1.9x to 6x faster for byte-identical output and
the same sorted-`Cards` invariant. It is a drop-in.

Keep concat+sort below ~64 candidates, where the bitmap's fixed domain scan costs
more than sorting a handful of ids.

## The larger question this raises, which is not answered here

If a bitmap is cheaper to *build* than a sorted vec from 64 candidates up, and
downstream can consume bitmaps (it does, above `BITS_PROMOTE`), then `BITS_PROMOTE`
at 4,096 may be far too high — most of that band could stay a bitmap and never be
extracted at all.

This bench cannot settle that, because it measures only the *production* side. A
31,508-bit map is 3,939 bytes that a consumer must scan; a 100-element `Vec<u32>` is
400 bytes it can walk directly. That consumption asymmetry is what `BITS_PROMOTE`
encodes, and moving the threshold needs the And/Or consumers measured on both
representations at the same candidate counts. Worth doing separately — the
production-side change above is worth making regardless of where the threshold lands.
