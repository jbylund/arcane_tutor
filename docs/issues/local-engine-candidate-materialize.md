# Materializing a sorted candidate list: bitmap beats sort, merge loses badly

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

## What to change

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
