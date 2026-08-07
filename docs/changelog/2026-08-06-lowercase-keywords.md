# `keyword:` reaches every keyword, not just the Title Case ones

## Problem

`keyword:"first strike"` returned nothing, as did every other keyword whose Scryfall spelling is not
Title Case — 131 of the 770 distinct keywords in the corpus, covering 3,298 printing-keyword pairs
(5.6% of all keyword occurrences), including the evergreen `First strike` (1,112 printings) and
`Double strike` (331).

The field was normalized on one side only. Storage kept Scryfall's string verbatim
(`{"First strike": true}`); the query looked up `val.strip().title()` (`{"First Strike": true}`), so
the JSONB containment match never fired. Query-side casing made no difference — `First strike`,
`first strike` and `FIRST STRIKE` all title-cased to the same wrong key. `.title()` also mangled
apostrophes: `Doctor's companion` (113 printings) was looked up as `Doctor'S Companion`.

## Fix

Both sides now lowercase, which is what the neighbouring oracle/art/`is:` tag collections already do:

- `preprocess_card()` lowercases keyword keys at ingest (`api/card_processing.py`).
- `get_keywords_comparison_object()` lowercases the query value instead of title-casing it.
- `api/db/2026-08-06-01-lowercase-keywords.sql` rewrites the existing rows in place, so the change
  deploys without waiting on a bulk reimport.

Lowercase rather than Title Case on both sides because `.title()` is lossy on real keyword spellings
(`Doctor's companion`), and because the frontend's keyword autocomplete already lowercases the
catalog it serves — it was suggesting `keyword:first strike`, a query guaranteed to return nothing.

The lookup stays an exact `@>` containment against a lowercased key, so the existing GIN index is
used unchanged; the Rust engine passes the value through verbatim and needed no change.

`client/query_sampler.py`'s `is_queryable_keyword` — which existed only to stop the generator
emitting guaranteed-empty queries — is deleted, and the `keyword` family now draws from the whole
vocabulary.

See [docs/issues/done/00825-keyword-title-case-mismatch.md](../issues/done/00825-keyword-title-case-mismatch.md)
for the corpus measurements and the options weighed.

## Trade-offs

- Stored keywords no longer carry Scryfall's exact capitalization. Nothing displays them: the
  frontend never renders `card_keywords`, and the autocomplete catalog lowercased them already.
- Existing deployments need the migration (or a reimport) applied before the query-side change, or
  every `keyword:` returns nothing rather than just the 131.
