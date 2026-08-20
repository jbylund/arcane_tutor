# `_EngineDeclinedQueryError` Is a Bare Exception, Safe Only Via Its Single Caller

[#939](https://github.com/jbylund/sylvan_librarian/issues/939), filed alongside the #936 review follow-ups.

[`_EngineDeclinedQueryError`](../../api/api_resource.py#L76) is a plain `Exception`, not a
`falcon.HTTPError`. It is caught and handled correctly today only because `_search` is currently the
sole caller of `_search_engine`.

## Why

The engine declining a query it cannot build — a regex with backreferences or lookaround the Rust
`regex` crate doesn't support, say — is expected, ordinary behavior, not a bug. What changed is how
that decline is signaled internally. Per the class's own docstring, `_search`'s fallback handler used
to tell a decline apart from a genuine engine failure via `isinstance(e, falcon.HTTPBadRequest)`: the
old code converted a decline into a real `HTTPBadRequest` somewhere upstream, and Falcon serializes
any `HTTPError` subclass into a clean response automatically, so *any* caller of that path got a
well-formed 400 for free. The new code raises `_EngineDeclinedQueryError`, a bare `Exception`, and
the only code that knows what to do with it is `_search`'s own `except BaseException` block
([api/api_resource.py:1352-1388](../../api/api_resource.py#L1352-L1388)).

Confirmed via grep that `_search_engine` has exactly one caller (`_search`) as of this writing, so
nothing is broken now. The risk is forward-looking: `_search_engine`/`_search_sql` are already split
out from `_search` for testability, which is exactly the shape that invites a second caller later —
an admin/debug endpoint, a batch job, a second search variant. If one calls `_search_engine` (or
`self._engine.query` under some other wrapper) without going through `_search`'s catch block, a
decline surfaces as an unhandled exception. Falcon has no special handling for a bare `Exception`, so
it becomes an opaque 500 on a query that SQL would have answered fine — exactly the outcome
`_EngineDeclinedQueryError` was introduced to avoid for the one caller it was designed around.

## Fix

Make `_EngineDeclinedQueryError` inherit from `falcon.HTTPBadRequest` (or wrap it in one) instead of
bare `Exception`, so any caller — today's or a future one — gets a correct 400 by construction, the
same guarantee `_QueryError` used to carry before this exception was introduced. `_search`'s
`isinstance(e, _EngineDeclinedQueryError)` check for logging purposes keeps working unchanged either
way, since `isinstance` doesn't care what the class also inherits from.

## Tests

`api/tests/` (or wherever `_search_engine` gets a direct unit test): call `_search_engine` on its own
with a query the engine declines, without going through `_search`, and assert the raised exception
is-a `falcon.HTTPError` with a 400 status — pinning the guarantee for whichever caller shows up next.
