# `_EngineDeclinedQueryError` Is a Bare Exception, Safe Only Via Its Single Caller

**DONE — fixed by deleting the wrapper, on the `mana-atom-and-explanation-followups` branch, stacked
on #909.**

[#939](https://github.com/jbylund/sylvan_librarian/issues/939), filed alongside the #936 review
follow-ups.

`_EngineDeclinedQueryError` was a plain `Exception`, not a `falcon.HTTPError`. It was caught and
handled correctly only because `_search` was currently the sole caller of `_search_engine`.

## Why the fix isn't "make it an HTTPError"

The obvious-looking fix — subclass `falcon.HTTPBadRequest` so any future caller gets a clean 400 for
free even without special handling — was rejected: an engine decline isn't an HTTP-layer concept, and
forcing it to look like one conflates the two layers for no real benefit here, since nothing
currently lets it escape to an HTTP response anyway (it's swallowed to fall through to SQL).

## Fix

`card_engine.QueryError` — the actual exception `_search_engine` was catching before wrapping it — is
already engine-specific: nothing else in the call chain raises it (confirmed against
`card_engine/src/filter.rs`'s `build_filter`, whose `Result<FilterExpr, String>` covers six distinct
failure reasons — an unrecognized AST node, a bad date, invalid regex syntax, regex unsupported on an
attribute, text-substring unsupported on an attribute, an unknown field — all surfacing as the same
`QueryError`). Wrapping it in `_EngineDeclinedQueryError` added a second type that only `_search`'s own
handler understood, without narrowing what actually gets caught.

Deleted the wrapper class entirely. `_search_engine` now lets `QueryError` propagate unwrapped
(logging at info first); `_search`'s handler checks `isinstance(e, _QueryError)` directly instead of
the synthetic type. Net effect: a future caller of `_search_engine` that doesn't special-case anything
still sees a normal `card_engine.QueryError` — a real, specific, engine-owned exception — rather than
either an opaque bare `Exception` or a fabricated HTTP type.

## Tests

`api/tests/test_parsing_errors.py`: updated to construct/assert against `card_engine.QueryError`
directly rather than `_EngineDeclinedQueryError`; `test_query_error_propagates_unwrapped` (renamed
from `test_query_error_is_raised_as_engine_declined_query`) now asserts `_search_engine` raises
`QueryError` itself, not a wrapped type.
