# `keyword:` is unqueryable for 131 keywords

**Resolved** — keywords are lowercase on both sides; see [What shipped](#what-shipped-2-lowercase-on-both-sides).

`keyword:"first strike"` returns nothing. The keyword field is normalized on the query side but not
on the storage side, so any keyword Scryfall does not itself spell in Title Case is unreachable.

## The mismatch

| side | code | result for Scryfall's `"First strike"` |
|---|---|---|
| storage | [`api/card_processing.py:194`](../../../api/card_processing.py#L194) — `dict.fromkeys(card.get("keywords", []), True)` | `{"First strike": true}` |
| query | [`api/parsing/card_query_nodes.py:312`](../../../api/parsing/card_query_nodes.py#L312) — `val.strip().title()` | looks up `{"First Strike": true}` |

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

`frame:` title-cases at **both** ingest ([`card_processing.py:204-208`](../../../api/card_processing.py#L204))
and query ([`card_query_nodes.py:292-297`](../../../api/parsing/card_query_nodes.py#L292)), so the two
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

## What shipped: (2), lowercase on both sides

Lowercase rather than Title Case, for three reasons beyond it being the smaller normalization:

- `.title()` is lossy on real spellings (`Doctor's companion`); `.lower()` has no such failure mode.
- Oracle, art and `is:` tags are already lowercase on both sides, so this leaves one convention for
  collection lookups instead of two.
- The frontend keyword autocomplete already lowercases its catalog
  ([`api_resource.py`](../../../api/api_resource.py) `get_catalog`), so it was suggesting
  `keyword:first strike` — a query that could not match. Lowercase storage makes the suggestion and
  the lookup the same string.

The doc's worry about the GIN index does not apply, because normalization happens at **ingest**
rather than by wrapping the column in `lower()`: the lookup stays an exact `@>` containment on a
lowercased key. The Rust engine passes the value through verbatim
([`filter.rs`](../../../card_engine/src/filter.rs), `card_keywords` branch), so both query paths are
covered by the one Python-side change.

No reimport was needed after all — [`api/db/2026-08-06-01-lowercase-keywords.sql`](../../../api/db/2026-08-06-01-lowercase-keywords.sql)
rewrites the existing rows in place, skipping rows already lowercase or empty.

`client/query_sampler.py`'s `is_queryable_keyword` is deleted along with its two call sites, and the
`keyword` family now draws from the whole vocabulary (`first strike` and `double strike` added to
the fallback list).

## Validation

- `test_card_processing.py::test_preprocess_card_lowercases_keywords` — ingest lowercases
  `First strike`, `Double strike`, `Doctor's companion`, `Flying`.
- `test_sql_gen.py::test_keyword_sql_translation` — `keyword:"first strike"`, `keyword:"FIRST
  STRIKE"` and `keyword:"Doctor's companion"` all emit the containment fragment against the
  lowercased key.
- `test_query_sampler.py::test_every_keyword_survives_the_storage_round_trip` — the round trip holds
  for any typed casing; `test_fallback_keywords_are_in_stored_form` keeps the vocabulary honest.
- `test_engine_property.py` — synthetic corpus keywords are lowercase and include the two-word
  `first strike`, so the engine's `kw:` path is fuzzed against a spelling `.title()` used to mangle.
- The migration's semantics verified directly against PostgreSQL 17: 2 of 4 rows rewritten,
  already-lowercase and empty rows skipped, apostrophes and multi-word keys preserved.
