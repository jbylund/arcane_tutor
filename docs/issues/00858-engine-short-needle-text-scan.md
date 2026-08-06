# Single-character text needles have no index and scan every card — 1.16 ms for `o:s`

Status: **problem measured, neither fix measured.** Filed as
[#858](https://github.com/jbylund/sylvan_librarian/issues/858).

This is the engine's actual slow tail. It is worse than anything in
[#852](00852-engine-compose-acquire-p3-p4-ranking.md)'s or
[#856](00856-engine-compose-membership-bittest.md)'s populations — #856's whole population tops out at 78.5 µs,
and `o:s` is **fifteen times** that on its own.

## Measured

`unique=card`, `orderby=name`, limit 60, min of 15 trials after 3 warmups, on the production corpus:

| query | routed | `narrowed_repr` | results |
| --- | --: | --- | --: |
| **`o:s`** | **1,164.8 µs** | none | 30,247 |
| **`ft:s`** | **1,019.8 µs** | none | 19,620 |
| **`name:s`** | **446.6 µs** | none | 19,776 |
| `name:so` | **8.2 µs** | cards | 972 |
| `name:sol` | 4.4 µs | cards | 178 |
| `name:solr` | 0.7 µs | cards | 0 |
| `o:the` | 732.1 µs | cards | 16,240 |

A **54× cliff** between one character and two. And `o:s` at 1.16 ms lines up with the overall max (~1,133 µs)
in [#856's latency profile](00856-engine-compose-membership-bittest.md#it-touches-no-slow-queries-and-that-is-structural),
which is how we know this shape is the tail rather than merely slow in isolation.

## Why: three tiers, and the bottom one is missing

| needle length | mechanism | result |
| --- | --- | --- |
| ≥ 3 bytes | `trigram_candidates` | narrows |
| == 2 bytes | `NameBigramIndex` | **exact** — no `contains()` verification runs at all |
| == 1 byte | *nothing* | full per-card residual scan |

`trigram_candidates` returns `None` at `bytes.len() < 3` (`card_engine/src/lib.rs`). The consequence that makes
it expensive rather than merely unnarrowed: **`memoize_text_predicates` is gated on that returning `Some`** —

```rust
let Some(cand) = trigram_candidates(name_trigram, word) else { return };
let finder = memmem::Finder::new(word.as_bytes()); // built once, reused across the verify scan
```

— so for a 1-byte needle memoization returns early, the filter stays a `TextContains`, and it is evaluated
**per card inside the match loop**. The `Finder`-reuse optimisation on the line below never applies.

`o:s` misses the word index for the same class of reason: `oracle_word_eligible` requires `word.len() > 3`.

## Where the 446 µs goes, and it is not the search

446.6 µs / 31,508 cards = **14.2 ns per card**, ~43 cycles at 3 GHz. `str::contains` on a single-byte needle
lowers to `memchr`, which over a ~20-byte name is a few cycles. So the search is not the cost. Two things are:

- **The row stride.** `AOracleCard` is **288 bytes** (measured with `size_of`), and `card_name_folded` is an
  `InlineStr<61>` (62 bytes) inline in it. Reading every name therefore walks **9.07 MB** of card rows to touch
  **1.95 MB** of name field, of which the actual name content is ~630 KB. Same shape as
  [the `APrinting` width problem](local-engine-aprinting-layout.md), one struct over.
- **Per-card dispatch** through `FilterExpr` 31,508 times.

## Fix A: a unigram index, extending the tier that already works

`NameBigramIndex` already makes 2-byte needles exact, and the 1-byte case is the same structure one size down.
Only ~40 byte values actually occur in lowercased names, most of them dense enough to want a plane, and a plane
is `n_cards / 8` = **3,939 bytes** — so roughly **150 KB per text field**.

`name:s` then becomes an O(1) bitmap read: the 8.2 µs shape `name:so` already gets, which would be **~50×**.

Cheapest change, and it follows a precedent in the same file rather than inventing one.

## Fix B: a name/oracle blob and one `memmem` pass — which already exists here

**This design is already in the tree**, as `OracleWordIndex::sparse_blob`:

- dictionary words concatenated, each preceded by a `\0` — `WORD_BLOB_DELIM`, chosen because no eligible needle
  can contain it, so *a match can never straddle two entries*
- `sparse_word_starts` maps a match's byte offset back to an entry index by binary search
- one `memmem` pass replacing ~6,300 separate `.contains()` calls, **measured 5–6× faster** than the per-item
  loop (`bench_word_dict_scan.rs`)

The load-bearing supporting result is the one next to it: `bench_text_search.rs` found memmem **loses** on many
short separate haystacks, where its setup dominates, and **wins** on one long contiguous scan. Per-card name
scanning today is exactly the losing configuration by construction; blobbing converts it into the winning one,
and cuts traffic from 9.07 MB to ~630 KB.

Two notes for whoever builds it:

- memmem yields **ascending** positions, so a forward pointer over the offsets beats a binary search per match —
  the same trick [#857](00857-engine-membership-merge-sorted-list.md) uses, and it matters here because
  `name:s` matches 63% of cards.
- Once a card matches, restart past the end of that name. That makes the work one short find per card rather
  than one per occurrence.

## Which to do

**A is probably the bigger and cheaper win for the measured symptom**, and should be costed first.

**B is the more general mechanism**, and its case does not rest on 1-char needles: it also attacks the
*verification* pass where narrowing happens but is broad. `o:the` narrows to 16,240 cards and still takes
**732 µs** — A does nothing for that, and B would.

**But check [#859](00859-engine-exact-trigram-no-verify.md) before costing B against `o:the`.** A 3-byte needle
is exactly one trigram, so its verification is *provably redundant* and should be deleted rather than made
faster. That removes `o:the` from B's case, and leaves B justified on needles of 4+ bytes, where the trigram
intersection really is a superset and verification really is needed. They are not exclusive, and B is the deferred
"memmem for `TextContains`" item from the #694/#731 text-search follow-ups.

## Not yet measured — read the estimates as estimates

The problem is measured; **neither fix is**. The ~50× for A is arithmetic from `name:so`'s realized 8.2 µs, and
the 5–6× for B is the word index's result on a *different* blob with a longer needle. Build the kernel
comparison before committing.

One thing that comparison must model, because this session already produced a wrong answer by missing it: a
1-char needle matching 63% of cards is a **branch-unpredictable** workload, where a scan stops being purely
bandwidth-bound. [#856's density curve](00856-engine-compose-membership-bittest.md#what-the-bitmap-probe-costs)
shows the same effect costing 2–6× on a kernel that looked flat when measured only at the extremes.

## Related

- [local-engine-aprinting-layout.md](local-engine-aprinting-layout.md) — the same row-width-versus-scan problem
  for `APrinting`. Note its "don't implement" verdict was about a *misattributed* 55%; the mechanism it
  describes is real and this is another instance of it.
- [done/00663-engine-oracle-word-index.md](done/00663-engine-oracle-word-index.md) — where the blob-plus-memmem
  pattern and its 5–6× came from.
- [done/00649-accent-insensitive-name-search.md](done/00649-accent-insensitive-name-search.md) — why the scanned
  field is `card_name_folded` rather than `card_name_lower`.
- [#859](00859-engine-exact-trigram-no-verify.md) — the tier above: 3-byte needles have an exact index and
  verify anyway, *and* get declined by the same memoize gate that skips 1-byte needles. Independent fix, same
  family.
- [#856](00856-engine-compose-membership-bittest.md) — the latency profile this is measured against, and the
  density-curve caution above.
