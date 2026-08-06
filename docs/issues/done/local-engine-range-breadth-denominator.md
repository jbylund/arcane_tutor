# Range breadth was measured against the index's own length, so nullable columns looked broader

**DONE — merged in [#845](https://github.com/jbylund/sylvan_librarian/pull/845)** (layer 13, the top of the
cost-model stack). Whole mix 0.905, p99 0.680.

The follow-up this doc names at the end — splitting "is this worth narrowing" from "vec or bitmap", which
would retire the `eur<0.13 t:land` regression — **shipped in the same PR** as its second change: materialize
by bitmap scatter rather than by sorting, whole mix 0.949. So nothing carries forward from here. The
remaining call sites for that materialization are
[#849](https://github.com/jbylund/sylvan_librarian/issues/849).

`eur>0.15 tix>=0.03 tix<=0.03 id:bgruw` took **1.5 ms**. The same query without the last leaf takes
**39 µs**. Adding `id:bgruw` — which matches *every* card, since identity ⊆ {W,U,B,R,G} is universally
true — took the query from a `PrintingCompose` to a whole-corpus scan with `narrowed_repr: none`: the
price ranges contributed nothing at all.

## The denominator

A `PrintingValueIndex` omits null-valued printings — they can never satisfy a comparison. So `idx.len()`
is the *priced subset*, and judging breadth against it overstates breadth by exactly the null rate:

| index | present | % of corpus | breadth inflation |
| --- | --: | --: | --: |
| `released_at` | 97,206 | 100.0% | 1.00× |
| `collector_number` | 97,206 | 100.0% | 1.00× |
| `price_usd` | 81,542 | 83.9% | 1.19× |
| `price_eur` | 81,523 | 83.9% | 1.19× |
| `price_tix` | **54,896** | **56.5%** | **1.77×** |

`tix>=0.03 tix<=0.03` is 16,664 printings — 30.4% of the tix index, 17.1% of the corpus. Judged the first
way it is broad; judged the second it is clearly worth narrowing. The guard asks "is this too big a
fraction of what we would otherwise scan", and what we would otherwise scan is the corpus.

The same guard already used `n_printings` in the collection and frame arms. The range arms were the
inconsistent ones.

## Which gate actually fired

`range_narrowed` has the visible copy of the test, and changing **only** it moved nothing. The gate that
fires first is in `fuse_and_range_children`:

```rust
if !sparse_only || !range_too_broad_to_narrow(e - s, g.idx.len()) {
```

Under `sparse_only`, fusion exists to discover a sparse intersection hiding behind broad halves. Judged
against the index, the tix interval was not sparse, so **the two halves were never fused** — and each
half alone (`tix>=0.03`, `tix<=0.03`) is genuinely broad, so both declined under `broad_ok: false` and
nothing narrowed. The unfused halves never reach `range_narrowed` as an interval at all.

Both call sites now take the corpus, behind `CARD_ENGINE_RANGE_BREADTH_VS_CORPUS`.

## Measured

Per query:

| query | before | after | |
| --- | --: | --: | --: |
| `eur>0.15 tix>=0.03 tix<=0.03 id:bgruw` | 1,415.8 µs | **333.8 µs** | 4.2× |
| `tix>=0.03 tix<=0.03 t:creature` | 612.7 µs | **337.7 µs** | 1.8× |
| `tix>=0.03 tix<=0.03 c:g` | 246.1 µs | **206.2 µs** | 1.2× |

`narrowed_repr` goes `none` → `printings` and `eval_domain` 31,508 → 4,701, with `matches` landing at
4,701 against a true 4,621 (1.017×). Already-sparse ranges (`tix=0.02`, `tix=0.04`, `usd>100`) and
genuinely broad ones (`eur>0.15` at 57%, `cn>200` at 38%) are untouched, as are the always-present
`date`/`cn` indexes where the two denominators are identical by construction.

Interleaved A/B, 10 rounds, 1,368 queries, drift 0.996:

| subset | n | index-denominator | corpus-denominator | ratio |
| --- | --: | --: | --: | --: |
| nullable-range TARGET | 159 | 61.8 ms | 45.0 ms | **0.729** |
| everything else CONTROL | 1,209 | 124.5 ms | 123.6 ms | 0.993 |
| whole mix | 1,368 | 186.3 ms | 168.6 ms | **0.905** |

**p99 0.680** (1,056.9 → 718.5 µs); p50 0.989, p90 1.004.

## The one real regression, and what it points at

`eur<0.13 t:land` went 208.0 → 267.5 µs (1.29×). `eur<0.13` is 20,408 printings: **25.0% of the eur
index** — right at the old cutoff — and 21.0% of the corpus, so it is newly admitted. It then takes
`range_narrowed`'s **vec** path: `collect` + `sort_unstable` over 20,408 `u32`s, because `range_pids`
yields key-major order and `Candidates::Printings` is contractually pid-ascending. That sort costs more
than narrowing a plane which was already only 11,552 cards.

The other apparent regressions are noise: `name:s` spreads 2.52× inside one arm and its other two configs
read 1.01 and 0.99; `tix>=0.02 tix<=0.03` and `tix>=0.03 tix<=0.04` read 0.93–1.01 in their other configs.

**The threshold is deciding two different questions with one number** — *is this worth narrowing at all*
and *vec or bitmap*. Widening the first pushed 16–20k-element sets onto a representation meant for small
sparse ones. On the headline query, `ns_prepare` is 124 µs of the remaining 334 µs, essentially all of it
that sort; scattering the same 16,664 into 1,519 words would be ~20–30 µs.

Splitting them — bitmap above some absolute size, vec below — is the follow-up, worth ~90 µs on the
headline query and it should retire the `eur<0.13` class entirely. Same shape as the `dense` vs `broad`
conflation in [the frame gate](local-engine-is-frame-predicates.md).

## Status

Shipped on by default. Every figure measured on the production corpus; per-query numbers are a minimum of
11 trials after warmup, the A/B is 10 interleaved rounds with a control subset.
