# A 3-byte needle is one trigram and needs no verification — but gets verified, and declined

Status: **defect confirmed by reading the index; the size of the win is not measured.** Filed as
[#859](https://github.com/jbylund/sylvan_librarian/issues/859).

## The trigram is already exact at 3 bytes

A needle of exactly 3 bytes is exactly **one** trigram, so the posting list *is* the complete containment set.
`SortedTrigramIndex`'s own doc says as much — *"trigram → ascending list of dense text ids whose text
**contains** it"* — and three properties make that hold rather than merely sound plausible:

- **The index is lossless.** `build_trigram_index` / `build_oracle_text_index` walk *every* 3-byte window of
  every text (`bytes.windows(3)`), and `finalize_trigram_index` stores each posting in full — sparse as `u16`
  postings, dense as a bitmap. No thresholding, no dropped entries.
- **Index and verify read the same field.** `name_trigram` is built over `card_name_folded`
  (`build_trigram_index(&cards, |c| c.card_name_folded.as_str())`) and the verify reads
  `card_name_folded`; the oracle index is built over `strings[global]` and the verify reads
  `str_at(strings, gid)`. So #649's accent folding cannot desynchronise them, which was the one plausible
  reason a verify might be needed.
- **Both sides match bytewise.** Windows are over bytes and `memmem::find` is bytewise, so a multi-byte UTF-8
  needle behaves identically on both.

`intersect_operands` even names the case in its own comment — *"a 3-byte needle (a single window…)"* — so the
shape is recognised; the consequence just was not drawn.

## It gets verified anyway

Both memoize arms do this (`card_engine/src/filter.rs`):

```rust
let Some(dense) = trigram_candidates(&oracle.trigrams, word) else { return };
let finder = memmem::Finder::new(word.as_bytes()); // built once, reused across the verify scan
// ... finder.find(...) once per candidate
```

The **bigram** arm one size down already gets this right, and its comment states the argument:

> 2-byte needles resolve exactly through the bigram index: the member cards are the complete match set
> (containment IS bigram membership), so no `contains()` verification runs at all.

Identical reasoning applies at 3 bytes. Nothing propagated it upward.

## The larger consequence: the decline gate's premise fails too

Memoization is gated on the needle not being too common:

```rust
Some(min) if min <= oracle.gids.len() / 2 && Self::memoize_pays(min, eval_domain, cards.len()) => {}
_ => return,
```

**That gate exists because the verify scan has to pay for itself.** At 3 bytes there is no verify scan, so the
premise does not hold: materializing the posting list is O(k) with no per-text work. A 3-byte needle should
therefore **always** memoize — and today the engine declines exactly the common needles that cost the most,
sending them to per-card evaluation over full oracle texts.

This is probably the bigger half of the fix, and it is the same shape of error as
[#856](00856-engine-compose-membership-bittest.md): a gate whose justification stopped applying, left in place.

## Measured

`unique=card`, `orderby=name`, limit 60, min of 15 trials after 3 warmups, production corpus:

| query | routed | results | ns/result |
| --- | --: | --: | --: |
| `o:you` | **975.5 µs** | 20,247 | 48 |
| `o:the` | **775.9 µs** | 16,240 | 48 |
| `o:and` | 739.2 µs | 13,691 | 54 |
| `o:tar` | 732.5 µs | 12,605 | 58 |
| `o:qua` | 41.5 µs | 1,708 | 24 |
| `o:zap` | 0.3 µs | 1 | — |
| `name:the` | 47.7 µs | 3,002 | **16** |
| `name:sol` | 4.2 µs | 178 | — |

Two readings. Cost **tracks result count** rather than cliffing, so this is not a single threshold flipping.
And **oracle costs ~3.6× more per result than name** (58 against 16 ns/result), which is what scanning full
oracle texts looks like next to ~20-byte names.

## What is not established

**How much of `o:the`'s 776 µs is the redundant verify** against legitimate work to produce 16,240 rows. That
cannot be separated from outside Rust — `explain_analyze` does not report whether memoization fired — and the
experiment that answers it *is* the fix. So settle it by building it rather than by modelling it.

Do not assume the whole 776 µs is recoverable: emitting 16,240 rows through `StreamedSelect` has a real floor,
and `name:the`'s 16 ns/result suggests the floor is a substantial fraction.

## Two changes, independent

1. **Skip the verify at `len == 3`** — the candidates are the answer.
2. **Bypass the decline gate at `len == 3`** — always memoize, since there is no scan to amortise. Likely the
   bigger win, and the one that currently sends `o:the` and `o:you` down the per-card path.

## Verifying it

Exactness is a claim about the index, so **assert it rather than trusting the argument above**: over a sample
of 3-byte needles, the memoized id set must be byte-identical with and without the verify scan. If a candidate
is ever rejected, the index is not what its doc says, and *that* is the finding instead.

Then rows before timings, as ever — this changes which cards a text predicate matches if the exactness claim is
wrong, which is a wrong-answer bug rather than a slow one.

```bash
.venv/bin/python scripts/bench_short_needle_cliff.py --shm /tmp/needle.store
```

## Related

- [#858](00858-engine-short-needle-text-scan.md) — the tier below: 1-byte needles have no index at all, and
  lose memoization for the same gating reason. Same family, different mechanism, and the two fixes are
  independent.
- [done/00663-engine-oracle-word-index.md](done/00663-engine-oracle-word-index.md) — the word index, which
  covers needles `> 3` bytes and so does not reach this case.
- [done/00649-accent-insensitive-name-search.md](done/00649-accent-insensitive-name-search.md) — why the name
  index and verify both use `card_name_folded`, which is what makes exactness hold here.
