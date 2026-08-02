# `keyword:` is unqueryable for 131 keywords

`keyword:"first strike"` returns nothing. The keyword field is normalized on the query side but not
on the storage side, so any keyword Scryfall does not itself spell in Title Case is unreachable.

## The mismatch

| side | code | result for Scryfall's `"First strike"` |
|---|---|---|
| storage | [`api/card_processing.py:194`](../../api/card_processing.py#L194) — `dict.fromkeys(card.get("keywords", []), True)` | `{"First strike": true}` |
| query | [`api/parsing/card_query_nodes.py:312`](../../api/parsing/card_query_nodes.py#L312) — `val.strip().title()` | looks up `{"First Strike": true}` |

The JSONB containment match never fires. Query-side casing is irrelevant: `"First strike"`,
`"first strike"` and `"FIRST STRIKE"` all `.title()` to the same wrong key.

`.title()` additionally breaks on apostrophes — `Doctor's companion` becomes `Doctor'S Companion`.

## Blast radius

Measured over the 97,206-printing corpus export:

- 770 distinct keywords; **131 unqueryable** (17%)
- 3,298 printing-keyword pairs affected — **5.6%** of all keyword occurrences
- includes the evergreen **First strike** (1,112) and **Double strike** (331)

Largest: `First strike` 1112, `Double strike` 331, `Partner with` 125, `Doctor's companion` 113,
`Cumulative upkeep` 110, `Basic landcycling` 107, `Choose a background` 100, `Start your engines!`
98, `Venture into the dungeon` 84, `Max speed` 78.

## Why the neighbouring fields are fine

`frame:` title-cases at **both** ingest ([`card_processing.py:204-208`](../../api/card_processing.py#L204))
and query ([`card_query_nodes.py:292-297`](../../api/parsing/card_query_nodes.py#L292)), so the two
agree. Oracle tags are lowercase on both sides. Keywords are the only field normalized on one side
only — which is the shape of the bug, and the thing a fix should remove rather than patch.

## Options

1. **Drop the query-side `.title()` and match verbatim.** One line, no migration. Breaks
   `keyword:FLYING` and `keyword:flying`, which work today and are what people type.
2. **Normalize both sides to a case-fold.** Correct and casing-tolerant, but the stored keys change,
   so it needs a reimport, and a case-insensitive JSONB lookup has to keep using the GIN index —
   verify before committing to it.
3. **Title-case at ingest as well.** Symmetric with `frame:`, keeps `keyword:FLYING` working, needs
   a reimport. Loses Scryfall's exact spelling in storage, which nothing currently depends on.

(3) is the smallest change that preserves current query ergonomics; (2) is the most correct. Either
needs a reimport, so the choice should be made once rather than staged.

## Fallout to clean up when this is fixed

[`client/query_sampler.py`](../../client/query_sampler.py)'s `is_queryable_keyword` exists solely to
keep the generator from emitting guaranteed-empty queries. Delete it, and its two call sites, along
with the mismatch — the `keyword` family should draw from the whole vocabulary.
