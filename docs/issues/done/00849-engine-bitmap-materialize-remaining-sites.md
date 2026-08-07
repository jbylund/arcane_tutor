# Adopt Bitmap Materialization at the Remaining Narrowing Call Sites

**DONE.** Filed as [#849](https://github.com/jbylund/sylvan_librarian/issues/849). `expand_csr` — the
oracle-text, artist and flavor CSR expansions — routes through `sorted_ids`. `rarity_candidates` was
measured and deliberately left alone.

The measurement lives in `card_engine/src/bench_expand_materialize.rs`; the end-to-end A/B in
`scripts/bench_expand_materialize.py`. The three-way sort/merge/bitmap comparison this builds on is
[the candidate-materialize record](local-engine-candidate-materialize.md).

## What this issue got wrong

It named `arith_tuple_narrow` and `numeric_candidates` as unadopted. They were adopted by #845
itself — the PR reached further than the issue text written alongside it, and both have called
`sorted_ids` with `n_cards` as the domain since it merged. Nothing was left to do there.

What *was* left was the third shape: the arms that union several **posting rows**.

## The two that were left split on row count, not on ratio

| site | rows unioned | verdict |
| --- | --: | --- |
| `expand_csr` (oracle text / artist / flavor) | 2 – 18,139 | **adopted**, 1.4–4.6× |
| `rarity_candidates` | 1 – 6 | **declined**, the fold still wins |

`rarity_candidates` looks like the single best candidate by the ratio the crossover is stated in —
`rarity<=rare` is 29,712 cards in a domain of 31,508, or 1.1:1, further inside the bitmap's half
than anything else in the engine. It still loses:

| query | rows | k | fold µs | bitmap µs |
| --- | --: | --: | --: | --: |
| `rarity=common` | 1 | 10,630 | **0.96** | 21.96 |
| `rarity>=rare` | 4 | 12,887 | **25.83** | 29.21 |
| `rarity<=uncommon` | 2 | 20,022 | **20.12** | 45.83 |
| `rarity>=uncommon` | 5 | 21,985 | 57.54 | **51.83** |
| `rarity<=rare` | 3 | 29,712 | **60.58** | 67.92 |

Because its fold is not the fold the crossover was measured against. `bench_candidate_materialize`'s
losing merge was a `BinaryHeap` over 564 runs paying a `log k` sift per element;
`rarity_candidates` unions at most 6 already-sorted rows with `union_sorted`, which is a handful of
linear merges, and at one bucket it is a bare `collect` with no merge at all. **Row count decides
between folding and materializing; the domain:count ratio only decides between a sort and a bitmap
once you have already decided to materialize.** The same reasoning already governs `sparse_text_ids`
(`bench_narrow_alloc.rs` section B), which stays a fold for the same reason.

## The card-space re-derivation the issue asked for

It does not bind. The issue's premise was that `MATERIALIZE_BITMAP_RATIO`'s 490:1 was fitted on
printing space (501:1 measured) while card space measured 350:1, so a card-space site might pick
wrong in the 64–90 band. Measured at the real call site, `expand_text_ids`'s `k` on ordinary needles
is 349 to 7,560 — 90:1 down to 4:1, an order of magnitude past either candidate constant — and the
small-`k` end is far below it: `o:"protection from everything"` is `k = 7`, or 4,501:1, where the
ratio test correctly takes the sort (0.04 µs, against 0.25 µs had it scattered). Nothing realistic
lands in the disputed band, so the constant stays one number for all domains.

## What the bitmap is worth at these sites

Best of 200, real corpus, µs. `sort` is the code this replaced; `concat+bmp` concatenates the rows
and then scatters; `direct` is what shipped — it scatters straight from the rows, so the
concatenation disappears with the sort.

| site | needle | rows | k | dom:k | sort | concat+bmp | direct |
| --- | --- | --: | --: | --: | --: | --: | --: |
| oracle | `enters` | 7,172 | 7,560 | 4.2:1 | 54.83 | 21.58 | **16.83** |
| oracle | `flying` | 4,037 | 4,397 | 7.2:1 | 30.17 | 11.58 | **9.42** |
| oracle | `draw a card` | 2,786 | 2,902 | 10.9:1 | 20.46 | 7.46 | **6.50** |
| oracle | `counter target spell` | 336 | 349 | 90.3:1 | 2.33 | 1.21 | **1.04** |
| oracle | `protection from everything` | 7 | 7 | 4501:1 | **0.04** | 0.25 | **0.04** |
| artist | `a` | 1,726 | 77,049 | 1.3:1 | 477.42 | 102.29 | **104.50** |
| artist | `e` | 1,425 | 66,287 | 1.5:1 | 402.00 | 89.88 | **90.71** |
| artist | `john` | 37 | 2,184 | 44.5:1 | 9.71 | 4.29 | **4.17** |
| flavor | `the` | 18,139 | 36,392 | 2.7:1 | 274.00 | 113.21 | **85.08** |
| flavor | `war` | 1,457 | 2,892 | 33.6:1 | 19.25 | 7.71 | **6.33** |
| flavor | `dragon` | 349 | 723 | 134.4:1 | 3.50 | 2.54 | **2.04** |

Not concatenating is worth a further ~1.3× in card space and on the wide flavor sets; on the artist
rows (45 ids each, the widest here) it is a wash, and `direct` is kept for both of them because it
is also the shorter code — one `flat_map`, no intermediate vec.

`direct` walks `offsets` twice, once to size the answer for the route choice and once to emit it.
That cost is inside every number above.

## End to end

Same binary both sides — `CARD_ENGINE_RANGE_MATERIALIZE_BITMAP` picks the route — 3 interleaved
rounds, 4 s per config, floor of the per-round floors. No config here is a range predicate, so the
flag isolates `expand_csr`. `total` matched on every config. Measured on the rebase onto
[#829](https://github.com/jbylund/sylvan_librarian/pull/829), which reworked plan routing.

| group | n | median ratio | range |
| --- | --: | --: | --- |
| oracle | 8 | **0.914** | 0.679 – 1.007 |
| regex | 1 | 0.960 | — |
| artist | 4 | **0.856** | 0.669 – 1.009 |
| flavor | 3 | 0.962 | 0.949 – 0.982 |
| control | 6 | 0.999 | 0.985 – 1.016 |

The per-query numbers matter more than the medians, because the win is a function of `k` and `k`
spans three orders of magnitude within each group:

| query | sort | bitmap | ratio |
| --- | --: | --: | --: |
| `a:a` | 1.506 ms | **1.140 ms** | 0.757 |
| `a:e` | 0.924 ms | **0.618 ms** | 0.669 |
| `o:the` | 200 µs | **136 µs** | 0.679 |
| `o:"you control"` | 310 µs | **279 µs** | 0.898 |
| `o:trample` | 70 µs | **64 µs** | 0.923 |
| `o:sacrifice` | 68 µs | 67 µs | 0.993 |
| `o:landwalk` | 40 µs | 41 µs | 1.007 |

Below roughly a thousand rows the saving is a few µs and disappears into the noise floor — the
controls put that floor at ±1.5%. That is the honest shape of this change: it does nothing for small
text queries and takes 25–35% off the big ones.

The same 22 configs were measured before and after the #829 rebase. Every group median moved by less
than 0.02 and no query's verdict changed, so #829's routing rework neither helps nor hurts this: the
plans these queries pick are the same on both sides (`PlanePopcountOrder` for the controls,
`GatheredScan` / `StreamedSelect` for the targets).

### Which queries reach the arm at all — probed, not assumed

An `eprintln` in `expand_csr` against the real corpus, one query per row. This corrected a wrong
assumption in the first version of this work, which held that a single eligible `o:` word is always
answered by the word dictionary's dense bitplane:

| query | calls | rows |
| --- | --: | --: |
| `o:flying` | **0** | — |
| `o:trample` | 1 | 1,484 |
| `o:landwalk` | 1 | 62 |
| `o:the` | 1 | 15,884 |
| `o:/counters? on/` | 1 | 4,593 |
| `a:a` | 1 | 1,726 |
| `ft:dragon` | 1 | 349 |
| **`ft:the`** | **0** | — |
| `t:creature`, `f:modern`, `c:g`, `name:bolt` | 0 | — |

The dense tier is only ~56 words, so skipping the CSR is the exception (`o:flying`), not the rule —
nearly every `o:` predicate lands here, as do the literal factors of a regex.

`ft:the` is the other direction, and it is why flavor moves least: at 36,392 printings
`range_too_broad_to_narrow` declines the flavor arm *before* `expand_flavor_ids` is called, so the
274 → 85 µs in the kernel table above is a win the query cannot collect. It is a control in the
end-to-end table, not a target. The flavor arm's reachable range is the middle of its distribution,
where it is worth 2–6%.

## Acceptance, against the criteria as filed

- **Identical `Vec<u32>`, asserted rather than sampled.** `sorted_ids` runs both routes and compares
  them on every call in debug builds, which the full debug suite exercises at every new call site —
  verified by a temporary probe, which tripped on `k = 3,554` in a domain of 24,689 as well as on
  fixture-scale sets. The kernel bench also asserts the new `expand_csr` against a verbatim copy of
  the sort it replaced before timing anything.
- **The threshold is a named constant with its measured band.** Unchanged: this reuses
  `MATERIALIZE_BITMAP_RATIO` rather than adding a second one, per the re-derivation above.
- **A kernel micro-benchmark, not end-to-end timing.** Both, in the end. The criterion assumed
  effects of 0.25–3.5 µs; these are 1–380 µs and clear the dispatch noise floor by a wide margin, so
  the end-to-end number is meaningful here and is reported alongside.

## Related

- [local-engine-candidate-materialize.md](local-engine-candidate-materialize.md) — the
  three-way measurement and the range-arm adoption (#845).
- [local-engine-sparse-union-threshold.md](../local-engine-sparse-union-threshold.md) — the other
  posting-union fold that stays a fold, for the same row-count reason.
