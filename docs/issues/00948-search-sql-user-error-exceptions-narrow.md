# `_search_sql`'s Postgres-Error-to-400 Conversion Only Covers Two Exception Classes

[#948](https://github.com/jbylund/sylvan_librarian/issues/948), filed during review of #909.

`_search_sql`'s `except psycopg.errors.DatatypeMismatch` / `except psycopg.errors.InvalidRegularExpression`
blocks convert exactly those two Postgres exceptions into a clean `falcon.HTTPBadRequest` via
`_raise_query_bad_request` ([api/api_resource.py:1619-1638](../../api/api_resource.py#L1619-L1638)).
Every other Postgres exception that a syntactically-valid-but-semantically-bad query can raise falls
through uncaught, surfacing as an opaque 500.

## Why

The parser only checks grammar, not arithmetic semantics — a division, an out-of-range cast, anything
Postgres itself has to evaluate is checked by Postgres alone. `power/0>1` is exactly this shape: it
parses cleanly on both parsers and compiles to a literal `... / 0 ...` division, which Postgres raises
as `psycopg.errors.DivisionByZero` (SQLSTATE `22012`, class `22` "data exception") once the query
actually runs. That's the same "ordinary typo, not a server problem" case `DatatypeMismatch` and
`InvalidRegularExpression` already exist to convert — it just wasn't one of the two exception classes
anyone had hit yet when those handlers were written.

Confirmed reachable: `parsing_f.parse_scryfall_query("power/0>1")` parses without error, so nothing
upstream of Postgres rejects it. Other class-`22` siblings — `NumericValueOutOfRange`,
`InvalidTextRepresentation` — are equally unhandled and equally reachable by an ordinary query typo
(e.g. a numeric comparison against a value that overflows the column's type).

This is the same one-class-at-a-time pattern #942 item A already flags for the *duplication* between
the two existing handlers; this issue is about the *coverage gap* the pattern leaves behind — each
new user-triggerable Postgres error needs its own bug report and its own handler before it stops
500ing.

## Fix

Catch by SQLSTATE class instead of by exception class. `psycopg.errors` exceptions carry
`.sqlstate`, and class `22` ("data exception") and class `42` ("syntax error or access rule
violation") are the Postgres-defined groupings for "the input was bad," as opposed to `08`
(connection), `53` (insufficient resources), `40` (transaction rollback), etc., which are genuine
server-side failures that should keep 500ing.

```python
except psycopg.Error as err:
    if (err.sqlstate or "")[:2] in {"22", "42"}:
        _raise_query_bad_request(
            exc_name=type(err).__name__,
            query=query,
            description=f"The search query '{query}' could not be executed: {err.diag.message_primary}.",
            err=err,
        )
    raise
```

This also lets `_raise_query_bad_request` drop its `exc_name` parameter (already flagged as redundant
state in the #909 review — always `type(err).__name__`) by computing it from `err` directly, since
this handler no longer has a literal string to pass in per call site.

The existing `DatatypeMismatch`/`InvalidRegularExpression` blocks keep their specific, better-worded
`description` strings — this widens the catch, not the two specific messages already tuned for their
cases. A generic fallback description covers everything else in the same class.

## Tests

`api/tests/test_datatype_mismatch.py` / `test_parsing_errors.py` (wherever the shared fixtures live):

- `power/0>1` → 400 with a reasonable message, not a 500
- confirm `DatatypeMismatch` and `InvalidRegularExpression` still get their specific descriptions
  (a fallthrough handler must not swallow the two already-tuned messages)
- a class-`08`/`53`/`40` exception (or a mock raising one) still propagates as a 500 — this is not a
  catch-all for every Postgres error, only the user-input-shaped classes
