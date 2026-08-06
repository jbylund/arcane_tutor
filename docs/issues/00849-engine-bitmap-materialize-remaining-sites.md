# Adopt Bitmap Materialization at the Remaining Narrowing Call Sites

Status: proposed, not started. Filed as
[#849](https://github.com/jbylund/sylvan_librarian/issues/849). The measurement and the range-arm adoption
are in [the candidate-materialize record](done/local-engine-candidate-materialize.md); this is the
remainder.

## The measurement, already done

Any narrowing arm that unions several posting rows has sorted runs and no globally sorted output, because
posting-row order is not card order. `Candidates::Printings` is contractually ascending, so something has
to produce that order. Three ways, all asserted to produce the identical `Vec<u32>` first
(`card_engine/src/bench_candidate_materialize.rs`, domain 31,508, 564 runs):

| candidates | concat+sort µs | merge µs | bitmap+extract µs | best |
| ---------- | -------------: | -------: | ----------------: | ---- |
| 16 | **0.25** | 0.33 | 0.42 | sort |
| 64 | **0.46** | 1.00 | 0.46 | tie |
| 256 | 1.50 | 3.58 | **0.79** | bitmap 1.9× |
| 1,024 | 5.25 | 16.08 | **1.42** | bitmap 3.7× |
| 4,096 | 21.12 | 79.58 | **3.50** | bitmap 6.0× |

The k-way merge loses everywhere despite every run already being sorted.

## What shipped, and the correction it forced

#845 adopted the bitmap for `range_narrowed` (the range arms), where the sort was the dominant cost —
134–186 µs on a mid-band price range, and 95% of `usd<0.18 t:land`. Whole mix 0.949, target 0.867.

It also corrected this document's own crossover claim: **the crossover is a domain:count ratio (~490:1),
not an absolute count.** "Below ~64 candidates" was derived on the card-space axis, and printing space has
3× the domain, so a count threshold ported between the two is wrong by that factor.

## The remaining sites

- **`arith_tuple_narrow`** — the case that prompted the benchmark in the first place. It concatenates the
  selected rows and calls `sort_unstable`, with a comment naming exactly why the sort is needed.
- **The other posting-union narrowing arms**, which have the same shape.

Both are **card-space**, so the 490:1 figure cannot be reused — it has to be re-derived on that axis. That
is the whole reason these were not swept along with #845: the shipped predicate is a ratio against the
printing domain, and applying it unchanged to a card-space arm would pick the wrong representation on
exactly the small sets where the sort is still faster.

## Acceptance

- Identical `Vec<u32>` before and after at every site, asserted rather than sampled — this is a
  representation change under a contract (`Candidates::Printings` ascending) that downstream consumers
  rely on for correctness, not just for speed.
- The ratio threshold is a named constant with its measured band in the comment, per the project's
  measured-constant convention.
- A kernel micro-benchmark, not end-to-end query timing: at 0.25–3.5 µs these effects are below what
  routed dispatch can resolve.

## Related

- [done/local-engine-candidate-materialize.md](done/local-engine-candidate-materialize.md) — the full
  three-way measurement and the range-arm adoption.
- [done/local-engine-range-breadth-denominator.md](done/local-engine-range-breadth-denominator.md) — the
  change that admitted 16–20k-element sets into the vec path and made the sort worth retiring.
