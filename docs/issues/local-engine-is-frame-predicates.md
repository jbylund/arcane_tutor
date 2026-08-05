# `is:` and `frame:` are 24% of dispatch time and four unrelated bugs

After the value-major layout, the eur/tix indexes and the grouped orderby walk, `is:`/`frame:` queries
are **24.0% of all remaining dispatch time** in uniform sampled traffic and none of them moved (0.97–1.03
across every value). They are the largest remaining target, and the prefix hides at least four separate
problems.

Measured on the production corpus, `unique=card orderby=edhrec limit=60`, plan-own dispatch:

| predicate | resolves to | count source | true | `eval_domain` | µs |
| --- | --- | --- | --: | --: | --: |
| `frame:2015` | `card_frame_data` | candidates | 22,562 | **31,508** | **603** |
| `is:new` | `card_frame_data` | candidates | 22,562 | **31,508** | **558** |
| `is:permanent` | `Or` over 6 `card_types` | candidates | 24,387 | **31,508** | **641** |
| `is:old` | `Or` over `card_frame_data` | candidates | 7,191 | 7,191 | 399 |
| `frame:2003` | `card_frame_data` | candidates | 8,905 | 8,905 | 322 |
| `frame:1997` | `card_frame_data` | candidates | 6,302 | 6,302 | 251 |
| `is:historic` | `card_frame_data` | candidates | 7,261 | 7,261 | **59** |
| `is:dfc` … `is:flip` | `card_layout` | candidates | 20–388 | **31,508** | 70–209 |

`eval_domain == 31,508` means nothing narrowed at all — a full corpus scan.

## 0. FIRST: the revert below rests on a bad measurement — re-measure before trusting it

Every wall-time figure in this doc's section 1 was taken against a baseline captured hours earlier. The
same build re-measured back-to-back read **180.7 ms then against 220.6 ms later — 22% machine drift** —
while the canary queries stayed flat at 31-32 µs, because three queries cannot see a broad thermal shift.

Interpolating that drift, the hybrid index's "1.084 worse" was plausibly ~0.98, i.e. neutral or better.
**The revert of `fb7f0a9` may therefore be wrong.** Before anything else here:

1. On a quiet machine, capture base and hybrid **back-to-back**, two runs each side, per-query minimum.
2. Include a control subset the change cannot touch (queries with no `frame:`/`is:` leaf) and require it
   to read ~1.00. That is the check that actually caught this: `name:s` reading 2.21x on a query with no
   frame predicate was the tell, not the canaries.
3. Only then judge the aggregate.

`fb7f0a9` is the commit to restore; `9cb7c15` reverted it. The per-query wins in it were measured with 20
reps each and are not in doubt (`frame:2015` 603 -> 185 µs, `is:new` 558 -> 141, `is:old` 399 -> 62,
`frame:2015` under `unique=printing` 834 -> 0.4) and neither is the 130 KB saving, which is deterministic.

## 1. The `frame_data` threshold drops its dense values — IMPLEMENTED AND REVERTED (see 0)

`HybridTagIndex` shipped in `fb7f0a9`: every value stored, dense as a printing bitmap and the sparse
tail as postings. The per-query wins are large and the memory prediction was exact (**130 KB smaller**,
72,158,208 → 72,025,104 bytes):

| query | before | after |
| --- | --: | --: |
| `frame:2015` (card) | 603.1 µs | **185.5 µs** |
| `is:new` (card) | 557.6 µs | **141.1 µs** |
| `is:old` (card) | 399.1 µs | **62.1 µs** |
| `frame:2003` (card) | 322.3 µs | **68.6 µs** |
| `frame:1997` (card) | 250.9 µs | **54.2 µs** |
| `frame:inverted` (card) | 249.5 µs | **50.9 µs** |
| `frame:2015` (printing) | 834 µs | **0.4 µs** |

**But the net is not a win yet.** Paired routed-path wall time, 2,000 realistic queries:

| subset | n | before | after | ratio |
| --- | --: | --: | --: | --: |
| `is:`/`frame:` touched | 56 | 9.1 ms | 8.4 ms | 0.920 |
| everything else | 1,944 | 171.6 ms | 187.5 ms | 1.093 |
| whole mix | 2,000 | 180.7 ms | **195.9 ms** | **1.084** |

A real regression class came with it: **a sparse `And` containing a frame leaf.** `keyword:extort
frame:inverted` 40 → 72 µs, `name:of c:g frame:2003` 107 → 164, `pow<2 frame:2003` 173 → 262,
`t:warrior frame:2015` 161 → 211. Compose applicability is a property of the whole EXPRESSION, so one
composable leaf makes an entire `And` composable however selective its siblings are.

### The mechanism, found — and it is not compose

Two hypotheses were tested and both were wrong. A pre-build sparsity guard (the `Perm` branch learns its
total from `compose_printing_bits` and declines a small one only after paying) changed nothing:
1.81× → 1.85×. The cause is not compose at all, which the counters show directly:

| query | count_source | narrowed_repr | eval_domain | µs |
| --- | --- | --- | --: | --: |
| `pow<2` | candidates | none | 4,043 | **48.0** |
| `pow<2 frame:2003` | candidates | **printing_bits** | **1,302** | **155.7** |

Adding the frame leaf makes the narrowing *more* selective — 4,043 candidates down to 1,302 — and 3.2×
slower, without ever reaching compose. The cost is `narrow_rec`'s `And` arm materialising an 11.9 KB
dense bitmap and converting id spaces to intersect it, for a query whose sibling had already narrowed
enough. That is exactly "the 10× And regressions of the first benchmark round" that `broad_ok` exists to
prevent — and the implementation had bypassed `broad_ok` for dense values, reasoning that a *stored*
bitmap has no scatter to pay.

**That misread the gate.** `broad_ok` means "nothing downstream would consume this usefully", not merely
"the scatter would be wasted". Inside an `And` with a selective sibling the bitmap IS consumed and still
loses.

### Why restoring the gate did not fix it either

Honouring `broad_ok` for dense values moved the whole mix 1.084 → 1.059 and p99 to 549 µs (better than
base), and turned `keyword:extort frame:inverted` from 1.81× into **0.51×**. But it broke others:

| query | base | bypassing `broad_ok` | honouring it |
| --- | --: | --: | --: |
| `keyword:extort frame:inverted` | 39.7 µs | 71.8 (1.81×) | **20.2 (0.51×)** |
| `pow<2 frame:2003` | 173.1 µs | 261.5 (1.51×) | 299.5 (**1.73×**) |
| `set:ps11 frame:2015` | 56.9 µs | **11.7 (0.21×)** | 67.2 (1.18×) |
| `set:hou frame:legendary` | 27.3 µs | **11.0 (0.40×)** | 58.7 (**2.15×**) |

Different sub-populations want opposite settings. **`broad_ok` is a boolean standing in for a cost
decision**, and that is the real blocker: whether materialising a broad child pays depends on the
sibling's selectivity and on the id-space conversion it forces, which is a comparison the cost model
should make and a boolean cannot express.

So this was reverted (the engine change; this analysis stands). Retrying it means giving the `And` arm a
cost-based choice about materialising a broad child — not a better boolean.

Also note the control column sits on the ~9% run-to-run noise floor, and its worst row (`name:s`
624 → 1188 µs) has no frame predicate, so part of it is not attributable. Restructuring `frame_data`
shifts every later field's archive offset, which is a plausible global cache effect and a hazard of any
layout change.

### The original diagnosis, kept for the reasoning

`build_thresholded_tag_index` deliberately discards any value whose postings trip
`range_too_broad_to_narrow`, and its own doc names the casualty:

> values whose posting would be declined by the range guard anyway (frame:2015 covers 66% of
> printings) are simply not stored — the absent-key convention already means "no narrowing", so
> dropped and unknown values both fall back to the scan.

**That reasoning is stale.** The range guard stopped declining broad sets in #636, and the consumer
followed: `narrow_rec`'s `CollectionCmp` arm now scatters a broad printing-space posting list into a
bitmap rather than giving up —

```rust
if !card_space && range_too_broad_to_narrow(v.len(), n_printings) {
    if !broad_ok { return None; }
    let bits = scatter_bits(v.iter().map(|x| u32::from(*x)), n_printings);
    return mk(Candidates::PrintingBits(bits));
}
```

So the build throws away postings the consumer is now equipped to use. `frame:2015` and `is:new` are the
same underlying predicate and together are ~1.16 ms of mean dispatch over 293 sampled queries.

**And the threshold is inverted, not merely stale.** A posting list costs 4 bytes per member; a
printing-space bitmap costs 1 bit per row of the domain. So a bitmap is smaller above `1/32 = 3.1%`
density — and it also removes the query-time scatter entirely, rather than paying it per member. Across
the 29 `frame_data` values in the corpus, 7 are above that crossover and 22 below:

| frame value | printings | density | as postings | as p-bitmap | cheaper | stored today |
| --- | --: | --: | --: | --: | --- | --- |
| `2015` | 64,139 | **66.0%** | 250.5 KB | **11.9 KB** | bitmap | **NOT STORED** |
| `2003` | 16,490 | 17.0% | 64.4 KB | **11.9 KB** | bitmap | postings |
| `1997` | 10,769 | 11.1% | 42.1 KB | **11.9 KB** | bitmap | postings |
| `Legendary` | 10,333 | 10.6% | 40.4 KB | **11.9 KB** | bitmap | postings |
| `Inverted` | 7,244 | 7.5% | 28.3 KB | **11.9 KB** | bitmap | postings |
| `1993` | 5,569 | 5.7% | 21.8 KB | **11.9 KB** | bitmap | postings |
| `Extendedart` | 4,157 | 4.3% | 16.2 KB | **11.9 KB** | bitmap | postings |
| `Showcase` … `Upsidedowndfc` (22 values) | ≤3,006 | ≤3.1% | ≤11.7 KB | 11.9 KB | postings | postings |

The guard drops exactly ONE value, and it is the one where a bitmap wins by the largest margin (21x).
Everything it keeps as postings includes six more values that would also be smaller as bitmaps.

**Fix: per-value cheaper-of-the-two, which is what `BorderPrintingPlanes` and `RarityPrintingPlanes`
already do** — a one-hot plane per dense value plus postings for the sparse tail. `frame_data` is the one
collection index that got neither.

| scheme | total |
| --- | --: |
| all 29 as postings | 491 KB |
| all 29 as printing bitmaps | 344 KB |
| **per-value cheaper-of-the-two** | **111 KB** |
| stored today (all but `2015`, as postings) | 240 KB |

So it is **130 KB smaller than today AND removes the full scan** — not a size-for-speed trade.

**A card-space existential bitmap is a different job, not a substitute.** 3.8 KB per value (112 KB for
all 29), exact for card-mode TOTALS — which is the 132 µs `printing_bits_to_card_bits` projection
discussed in [the walk-modes doc](./local-engine-orderby-walk-modes.md) — but LOOSE for row selection,
since "this card has ≥1 printing with frame 2015" does not say which printing. Printing and artwork mode
still need the printing bitmap. Border already carries both (`PLANE_BORDER` card-space +
`BorderPrintingPlanes` printing-space), and that pairing is the shape to copy.

Verify with `eval_domain`, not with timing: it should stop reading 31,508.

## 2. An `Or` containing `t:battle` loses the plane path entirely

`is:permanent` desugars to `t:creature or t:artifact or t:enchantment or t:land or t:planeswalker or
t:battle`, and that last disjunct costs **11.5x**:

| query | count source | plan | µs |
| --- | --- | --- | --: |
| 5 disjuncts (through `t:planeswalker`) | plane | PlanePopcountOrder | **55** |
| … `or t:snow` (6 disjuncts) | plane | PlanePopcountOrder | 57 |
| `t:instant or t:sorcery or t:snow or t:world or t:basic or t:kindred` (6) | plane | PlanePopcountOrder | 47 |
| … `or t:battle` (6 disjuncts) | **candidates** | StreamedSelect | **497** |
| `t:battle or t:creature` (2 disjuncts) | **candidates** | StreamedSelect | 90 |
| `is:permanent` | **candidates** | StreamedSelect | **641** |

Two candidate explanations are **ruled out** by that table: it is not the disjunct count (six other-type
disjuncts stay on the plane path), and it is not a missing type plane (`TYPE_PLANES = 14` covers every
bit including `TYPE_BATTLE = 1 << 2`, and `PERMANENT_TYPES` includes it).

**The actual reason is not yet identified.** `t:battle` alone acquires as `printing_compose` returning 0
rows in 3.5 µs, which is already odd for a `TypeCmp` — that is the thread to pull. Do not guess: two
plausible mechanisms were already eliminated above.

Whatever it is, the shape of the fix is likely the algebraic one: an empty or non-compilable disjunct
should be *dropped* from an `Or` (`Or(x, ∅) = x`), not poison the whole expression.

## 3. `card_types` has no `Battle`, which may be a correctness bug

Counting `card_types` across the corpus gives 12 distinct names and **`Battle` is not among them**:

    Creature 45,976   Legendary 13,537   Land 11,552   Artifact 10,949   Instant 10,725
    Sorcery 10,626    Enchantment 9,914  Basic 4,196   Planeswalker 1,379  Snow 262
    Kindred 183       World 42

The corpus carries release dates through 2025-02-14, so battles (March of the Machine, 2023) should be
present. If the live store is the same, then `t:battle` silently returns nothing and **`is:permanent`
misses every battle** — a wrong answer, not just a slow one.

Check the importer's type extraction before treating this as a corpus artifact. This is independent of
(2): fixing the plane path would still return zero battles.

## 4. `card_layout` is unindexed

Already scoped and deprioritized in
[local-engine-layout-postings.md](./local-engine-layout-postings.md) — `is:dfc`/`is:transform`/
`is:mdfc`/`is:split`/`is:meld`/`is:leveler`/`is:flip` all resolve to `card_layout`, which has no index
and no `narrow_rec` arm, so each is a full 31,508-card scan for as few as 20 matches.

## The unexplained one

`is:old` narrows to **7,191** cards and takes **399 µs**. `is:historic` narrows to **7,261** cards —
0.4% more — and takes **59 µs**. Same field, same count source, near-identical evaluation domain,
**6.8x** apart. Both are `Or`s over `card_frame_data`.

That gap is not explained by anything above, and it is worth understanding before optimising either:
whatever makes `is:historic` fast is presumably available to `is:old`.

## Order

1. **The `frame_data` threshold** (1) — largest, most certain, and the fix is deleting a stale guard.
2. **The `Battle` data question** (3) — cheap to check and it is a correctness issue, so it outranks
   the remaining performance items even though it is worth less time.
3. **The `is:old` / `is:historic` gap** — diagnosis only, and it may explain more than those two.
4. **The `t:battle` plane poisoning** (2) — worth 11.5x on a common idiom, but needs the mechanism found
   first.
5. `card_layout` (4) — deprioritized as rare.

## Status

Every number above is measured on the production corpus, minimum of 15 runs after warmup. The traffic
share (24.0%) is one 150 s uniform sample of 53,071 priced queries. Nothing is implemented, and two
mechanisms are explicitly open — (2)'s cause and the `is:old`/`is:historic` gap.

`is:` and `frame:` are absent from `REALISTIC_FAMILY_WEIGHTS`, so the same caveat as
[layout](./local-engine-layout-postings.md) applies: the 24% is a uniform-mode figure and the realistic
share is unmodelled. Unlike layout, though, `frame:` IS in the realistic weights (0.5), and the
`is:permanent`/`is:vanilla` shapes reach `card_types` and `oracle_text`, which are heavily weighted — so
parts of this reach realistic traffic through other spellings.

## 5. The `And` arm's cost-based skip — SHIPPED (`3cfd441`)

The general fix the frame_data work turned out to need, and it stands on its own. `narrow_rec`'s `And`
arm already had the rule — `AND_PROBE_FLOOR`'s doc: *"a child with `k < best` becomes the new,
strictly-smaller driver (fewer residual verifications, never a regression)"* — but gated it on
`rank > 0`, and only rank-1 range children had a probe. Rank 0 was assumed cheap to materialise and
never checked, which is false for containment collections (`is:spell` is ~60k printing ids).

`probe_collection_k` gives collections the same cheap size probe, and the skip drops `rank > 0` in
favour of "we know what this child costs". Unprobed rank-0 children keep their benefit of the doubt.

Measured against a **back-to-back** baseline: total 0.969, p50 0.971, p90 0.964, p99 0.967, with
`keyword:extort frame:inverted` at **0.48x** and untouchable queries at 0.98-0.99.

**Open:** `o:owner keyword:flying` at 1.56x. `keyword:flying` is a large *card-space* posting list, and
card-space children were explicitly exempted from the broad guard ("card-space lists need no guard —
same argument as `numeric_candidates`") because materialising card ids is cheap. Skipping one under an
oracle-text driver may be the wrong call, in which case the probe should apply to printing-space
children only. Not diagnosed — and worth re-measuring on a quiet machine before acting, for the reason
in section 0.
