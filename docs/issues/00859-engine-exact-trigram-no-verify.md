# A 3-byte needle's trigram set is exact, but `narrow_rec` marks it loose — so every candidate is re-verified

Status: **defect confirmed by reading the code; the size of the win is not measured.** Filed as
[#859](https://github.com/jbylund/sylvan_librarian/issues/859).

**The fix is one guard, and it is not in `memoize_text_predicates`.** `narrow_rec`'s text arm returns
`Narrowed::loose` for every needle of 3 bytes or more; at exactly 3 bytes the set is exact, so it should return
`Narrowed::tight`. Tight propagates to `residual_exact` → `all_match_known`, and the match loop's #634 Step 1
short-circuit then skips `card_pass` **entirely** — no verification anywhere, memoized or not. An earlier draft
of this doc proposed changing memoize instead; that would have been the wrong lever, and is recorded below as
the secondary effect it actually is.

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

## Where it goes wrong: one guard, one word

`narrow_rec` (`card_engine/src/lib.rs`):

```rust
FilterExpr::TextContains { field, word }
    if word.len() >= 3
        && matches!(field, TextSearchField::NameLower | TextSearchField::OracleTextLower) =>
{
    // Trigram candidates are supersets (false positives until the walk
    // verifies), so these sets are loose.
    match field {
        TextSearchField::NameLower => trigram_candidates(&indexes.name_trigram, word)
            .and_then(|v| Narrowed::loose(Candidates::Cards(v))),
        ...
```

**That comment is true at ≥ 4 bytes and false at exactly 3.** At 4+ the intersection of several trigrams really
is a superset — a text can contain `the` and `her` without containing `ther`. At exactly 3 there is one window
and no intersection, so there are no false positives to verify away.

Split the guard: `word.len() == 3` → `Narrowed::tight(...)`, `>= 4` stays loose.

### Why that is the whole fix

`Narrowed::tight` is what `narrow_candidates_exact` turns into `residual_exact`, which becomes
`all_match_known`, which the match loop reads first:

```rust
let all_match = all_match_known
    || match filter.card_pass(card, strings, &mut residual, ...) { ... };
```

So a tight narrowing makes `card_pass` never run — the #634 Step 1 short-circuit, already built and already
load-bearing elsewhere. **Nothing needs to memoize, and nothing needs a faster verify: the verification simply
does not happen.**

### The walk verifies the same field the index is built over, which is what makes tight sound

The one thing that could break exactness is the index and the walk disagreeing about *which* string they mean,
and #649's accent folding makes that a live question. They agree:

| field | index built over | walk evaluates |
| --- | --- | --- |
| `NameLower` | `card_name_folded` | `card_name_folded` (`filter.rs`, with #649's note that "the query word is folded the same way in Python before it reaches TextContains") |
| `OracleTextLower` | `strings[global]` | `str_at(strings, card.oracle_text_lower_id)` |

For oracle the candidates also pass through `expand_text_ids`, a CSR from text id to the cards carrying that
text — exact, so exactness survives the expansion.

## Secondary: the memoize arms become moot, and their gate was already unjustified at 3 bytes

Worth recording, but *not* the fix.

Both memoize arms run a verify scan (`finder.find(...)` per candidate) that is redundant at 3 bytes for the same
reason. The **bigram** arm one size down already gets this right, and states the argument in its comment:

> 2-byte needles resolve exactly through the bigram index: the member cards are the complete match set
> (containment IS bigram membership), so no `contains()` verification runs at all.

And memoization is gated on the needle not being too common (`min <= oracle.gids.len() / 2 && memoize_pays(...)`)
— a gate whose entire premise is that the verify scan must pay for itself. At 3 bytes there is no verify scan,
so the premise does not hold, which is the same shape of error as
[#856](00856-engine-compose-membership-bittest.md): a gate whose justification stopped applying, left in place.

But once the narrowing is tight, none of this matters for correctness or cost — whether memoize fires becomes an
optimisation of a path that no longer runs. Fix the narrowing; leave memoize alone unless it measures.

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

**How much of `o:the`'s 776 µs is verification** against the floor of emitting 16,240 rows. The arithmetic bounds
it: 776 µs / 16,240 candidates = **47.8 ns per candidate**, which is what a `memmem` over a few hundred bytes of
oracle text costs, so most of it is plausibly the verify. But `name:the` runs at 15.9 ns/result *including its
own loose verify over ~20-byte names*, so the pure emit floor is below that — putting `o:the`'s floor under
~258 µs and the likely win at **3–4×**, not 776 µs → nothing.

That is an estimate from two measurements, not a measurement. The experiment that settles it *is* the fix, so
build it rather than model it further.

## The change

Split `narrow_rec`'s text arm at `word.len() == 3` and return `Narrowed::tight`. That is it — the tightness
plumbing, `all_match_known`, and the loop short-circuit all exist already.

## Verifying it

Exactness is a claim about the index, so **assert it rather than trusting the argument above**: over a sample of
3-byte needles, run `card_pass` on every candidate the narrowing returns and require that **none is rejected**.
If any is, the index is not what its doc says and *that* is the finding — a debug assertion is the right home,
since it is cheap to state and this is the invariant the whole change rests on.

The same shape as the guard [#857](00857-engine-membership-merge-sorted-list.md) wants for its ordering
precondition, and for the same reason: marking something tight that is not tight drops rows silently.

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
