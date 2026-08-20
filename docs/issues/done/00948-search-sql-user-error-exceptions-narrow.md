# `_search_sql`'s Postgres-Error-to-400 Conversion Only Covered Two Exception Classes

**DONE — fixed by catching `psycopg.errors.DataError` instead of `InvalidRegularExpression`
specifically, on the `mana-atom-and-explanation-followups` branch, stacked on #909.**

[#948](https://github.com/jbylund/sylvan_librarian/issues/948), filed during review of #909.

`_search_sql`'s `except psycopg.errors.DatatypeMismatch` / `except psycopg.errors.InvalidRegularExpression`
blocks converted exactly those two Postgres exceptions into a clean `falcon.HTTPBadRequest`. Every
other Postgres exception that a syntactically-valid-but-semantically-bad query can raise fell through
uncaught, surfacing as an opaque 500 — `power/0>1` (`DivisionByZero`) being the motivating example.

## Why `DataError`, not a third narrow handler or `psycopg.errors.Error`

Checked psycopg's actual exception hierarchy against both plausible broadenings:

- **`psycopg.errors.DataError`** (SQLSTATE class 22, "Data Exception") is safe to catch broadly:
  every member — `DivisionByZero`, `NumericValueOutOfRange`, `InvalidTextRepresentation`,
  `StringDataRightTruncation`, and `InvalidRegularExpression` itself, among ~70 others — means
  "syntactically valid SQL, bad data at runtime." That's exactly the "user typo, not a server
  problem" shape the two existing handlers already assumed one exception class at a time.
- **`psycopg.errors.ProgrammingError`** (class 42) was considered and rejected: `DatatypeMismatch`
  lives there (SQLSTATE 42804), but so do `UndefinedColumn`, `UndefinedTable`, `UndefinedFunction`,
  `SyntaxError`, `InsufficientPrivilege`, and `AmbiguousColumn` — symptoms of a bug in *our own*
  `generate_sql_query`, a migration that dropped a column we still reference, or a permissions
  misconfiguration. Catching the whole class would silently convert real bugs into "Invalid Search
  Query" 400s instead of visible 500s that page someone or show up in error tracking.

`DatatypeMismatch` is not itself a `DataError` subclass (confirmed via `issubclass`), so it stays its
own member of the merged catch rather than being subsumed by it.

## Fix

```python
except psycopg.errors.InvalidRegularExpression as err:
    # kept separate purely for this nicer, prefix-stripped message
    reason = regex_error_reason(err.diag.message_primary)
    _raise_query_bad_request(..., description=f"...invalid regular expression: {reason}.", err=err)
except (psycopg.errors.DatatypeMismatch, psycopg.errors.DataError) as err:
    reason = (err.diag.message_primary or "").strip() or "the value is not valid for this comparison"
    _raise_query_bad_request(..., description=f"The search query '{query}' is invalid: {reason}.", err=err)
```

`InvalidRegularExpression` stays a separate, earlier `except` clause purely because it's common
enough to earn a hand-written, prefix-stripped message; being a `DataError` subclass, it would
otherwise fall into the generic branch too. The generic branch's message comes straight from
Postgres's own diagnostic text rather than a bespoke string per error class — more technical-sounding
than a hand-written message would be, but it covers every current and future class-22/`DatatypeMismatch`
member for free instead of needing its own bug report and handler each time, which was #948's actual
complaint (see the "one-class-at-a-time pattern" note this issue's sibling, #942 item A, also flags).

## Tests

`api/tests/test_datatype_mismatch.py`: existing `DatatypeMismatch` tests updated to populate
`.diag.message_primary` (needed now that the generic message reads it) and assert the new message
shape; new `TestDataErrorHandling` covers `DivisionByZero` (the motivating case) and a `DataError`
with no diagnostic text at all, to pin the fallback wording.
