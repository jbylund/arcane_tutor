# `is:vanilla` scans 17,317 cards to find 342, because "text is empty" has no index

`is:vanilla` parses to a two-term AND:

    t:creature  AND  oracle_text = ''

The first term narrows well and the second cannot narrow at all — and the second is the selective one.
That is the worst possible arrangement of two predicates.

| | |
| --- | --: |
| `t:creature` alone | `count_source: plane`, 17,317 cards, **40.2 µs** |
| `is:vanilla` | `count_source: candidates`, `eval_domain` **17,317**, true matches **342** |
| | **173.3 µs** |

So 17,317 cards are read to return 342. The estimate is 17,317 against a true 342 — **50x over** —
because the `= ''` conjunct contributes no selectivity to the estimator either, only to the residual.

## Why it cannot narrow

The trigram index needs a needle of ≥3 bytes; the empty string has no trigrams, so there is nothing to
look up. This is not a gap in the trigram index's coverage, it is outside its domain — no amount of
indexing text CONTENT answers "this text is empty".

Cheap, because the property is per-card and rare: **342 of 31,508 oracle cards (1.09%)** have empty
oracle text, and none have 1-2 characters, so "empty" and "un-trigrammable" are the same set here.

- a card-space postings list of the 342 ids: **~1.4 KB**
- or one bit per card in `BitPlanes` (#630 already exists for "card space: transposed low-cardinality
  dims"): **3.9 KB**, and it composes with the type plane `t:creature` already uses, which would make
  the whole query a two-plane AND

The plane is the better shape precisely because `is:vanilla` is an AND with a plane predicate: both
sides become word-ANDs over 493 words and the query stops touching card structs at all.

`00685-engine-null-vs-empty-text-parity.md` (done) already settled the null-vs-empty semantics this has
to respect — the index must agree with `field_text`'s notion of empty, not invent one.

## Related but different

- **`ft:''` at 653 µs is NOT this bug.** It reports 31,508 true matches, i.e. the parser reads `ft:''`
  as *contains* the empty string, which every text satisfies. That query is slow because it matches
  everything in card mode, not because it fails to narrow. Same for `o:''`.
  Only the `= ''` equality that `is:vanilla` desugars to is the un-narrowable shape.
- **[#623 flavor absent-gram bitmap](./00623-engine-flavor-absent-gram-bitmap.md)** is the same family
  one step out — rejecting a needle absent from the corpus. This is simpler: not "no text contains this
  needle" but "this card's text is empty", a stored per-card fact rather than a corpus-wide one.
- **`is:permanent` (461.7 µs) is a third, separate problem.** It desugars to a 6-way `Or` over
  `card_types` — creature/artifact/enchantment/land/planeswalker/battle — and reports
  `count_source: candidates` with `eval_domain: 31,508`, despite each disjunct alone being a plane
  (`t:creature` is 40.2 µs). A plane-composable `Or` is falling back to the general path; worth its own
  look, and not covered here.

## Status

Measured on the production corpus, not implemented. `is:` predicates are absent from
`REALISTIC_FAMILY_WEIGHTS`, so as with [layout](./local-engine-layout-postings.md) the realistic-traffic
weight is unknown — but unlike layout, the same index would serve any user-written `o:""`-style
emptiness test, and `is:vanilla` is a commonly-typed Scryfall idiom.
