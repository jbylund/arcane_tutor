# A 3-byte needle's trigram set is exact, but `narrow_rec` marks it loose — so every candidate is re-verified

Status: **prototyped and measured 2026-08-06; change reverted, not committed.** Filed as
[#859](https://github.com/jbylund/sylvan_librarian/issues/859).

**Measured: `o:the` 783.9 µs → 168.5 µs (4.7×), `o:tar` 9.3×, and 0 of 104 result cells changed.** The full
engine test suite passes. Details under [Prototyped](#prototyped-and-measured).

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

## Prototyped and measured

Built as described — `let mk = if word.len() == 3 { Narrowed::tight } else { Narrowed::loose };` — measured, then
**reverted**. Nothing is committed. Release build, Docker down, min of 9 trials, same store both sides.

**Correctness first: 0 of 104 cells changed.** 26 queries × 4 shapes, comparing total, row count, and a SHA over
the returned row-identity sequence in order. The set deliberately includes the shapes where tightness propagates
and could go wrong: negations (`-o:the`, `-name:the`), conjunctions (`o:the t:creature`, `o:the -t:land`,
`name:the o:the`, `o:the c:g cmc<=3`), a disjunction (`o:the or o:you`), 2- and 4-byte controls (`o:th`,
`o:they`, `o:ther`), and all three distinct-ons at two offsets each. The full engine suite also passes in debug —
153 tests, including `fuzz_row_identity_matches_reference` and `force_plan_differential_agreement`.

**Engine-side (`explain_analyze` routed_ns):**

| query | before | after | |
| --- | --: | --: | --: |
| `o:the` | 783.9 µs | **168.5 µs** | **4.7×** |

**End-to-end through `engine.query`** (includes Python marshalling, so these ratios are *conservative* — fixed
overhead sits on both sides and pulls the ratio toward 1):

| query / shape | before | after | |
| --- | --: | --: | --: |
| `o:tar` card/name | 1,559.4 µs | **167.0 µs** | **9.3×** |
| `o:and` card/name | 1,255.1 | **178.3** | 7.0× |
| `o:the` card/name offset 600 | 1,257.0 | **220.8** | 5.7× |
| `o:you` card/name | 1,089.8 | **263.2** | 4.1× |
| `o:the` card/name | 823.0 | **212.7** | 3.9× |
| `o:the -t:land` | 929.5 | **220.8** | 4.2× |
| `o:the t:creature` | 511.3 | **227.1** | 2.3× |
| `name:the` | 88.8 | **46.5** | 1.9× |
| `ft:the o:the` | 2,465.7 | **1,464.5** | 1.7× |
| `o:qua` | 85.2 | **51.6** | 1.6× |

1-byte needles are unchanged, as expected — that is [#858](00858-engine-short-needle-text-scan.md), a different
mechanism.

**The earlier 3–4× estimate was low, and here is why.** It put the emit floor at `name:the`'s 15.9 ns/result —
but `name:the` was itself paying a loose verify, and improved 1.9×. So the floor was inflated by the very defect
being measured. The lesson generalises: do not derive a floor from another query that has the same bug.

**Compositions benefit more than the bare predicate**, which was not predicted. `o:the -t:land` gains 4.2×
against the bare `o:the`'s 3.9×, because a tight text child lets the `And` skip `card_pass` for the whole
conjunction rather than just the text leaf.

**One shape gets nothing, and it opened a separate finding.** `o:the or o:you` stays slow: the union of the two
exact sets is 23,675 cards against a breadth guard that discards anything over 23,631, so the whole narrowing is
thrown away 44 cards short. Relaxing that guard for tight sets is worth a further 4.4× — but attempting it
**fails `fuzz_row_identity_matches_reference`** on a filter with no text in it, which means some narrowing
already over-claims tightness and the guard has been masking it. That is
[#860](00860-engine-broad-tight-narrowing-discarded.md), and it does not block this change.

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
