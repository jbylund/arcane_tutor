# Bind What Arrived, Not What Was Declared

`ParamBinder.bind` costs 1.28 μs per request for `search`. That was a rounding error against the
figure it was justified with — 2.1% of a 60 μs median query — but the median is the wrong number for
this. On the requests users actually make most, binding is a seventh of the work.

Two changes take it to 0.65 μs with no behaviour change. Both are measured, not projected.

## Why the median misleads

`benchmarks/survey/752-baseline-main.csv`, 520 queries:

| | count | median engine time |
| --- | ---: | ---: |
| whole corpus | 520 | 60.1 μs |
| exact-name (`!"..."`) | 95 (18%) | **7.3 μs** |
| under 10 μs | 92 (18%) | — |

The fastest are 2.7–3.4 μs. Exact-name lookup is the cheapest thing the engine does and among the
most common things a user asks for, so a fixed per-request cost lands hardest exactly where the
engine is fastest:

| | engine | binding | binding share |
| --- | ---: | ---: | ---: |
| exact-name median | 7.3 μs | 1.28 μs | **14.9%** |
| corpus median | 60.1 μs | 1.28 μs | 2.1% |

[#787](https://github.com/jbylund/sylvan_librarian/pull/787) cut coercion from 20.9 μs to 1.4 μs on
the argument that 33% of a query was too much to spend before answering it. 15% is the same argument
with a smaller number.

## Where the 1.28 μs goes

Measured per component, `search` (10 parameters, 6 supplied):

| component | cost |
| --- | ---: |
| walk the 10-entry plan | 740 ns |
| `supplied.keys() & kwargs.keys()` collision check | 180 ns |
| `dict(zip(positional_names, args))` | 160 ns |
| dict copy + update | 90 ns |
| **total** | **1.28 μs** |

Two observations.

**The positional bookkeeping is dead weight on almost every route.** `card` is the only handler of
the 28 marked that declares positional parameters. For the other 27, `args` is always empty, so the
zip builds an empty dict and the intersection compares an empty key view — 340 ns, 27% of the cost,
to discover nothing.

**The plan walk iterates declarations, not arrivals.** It visits all 10 parameters to fill 6 from the
request and 4 from defaults. The defaults do not change between requests.

## The change

**1. Skip positional handling when there are no path segments.** `if args:` around the zip,
collision check and update; otherwise alias `kwargs` directly. Safe because `supplied` is only ever
read after that point — nothing mutates it.

**2. Iterate what was supplied, against a precomputed defaults dict.** Copy `defaults` (built once at
construction from parameters that have one), then walk `supplied.items()` and overwrite. Turns
O(declared) into O(supplied) plus a dict copy, and removes the `has_default` branch from the loop.

Measured, with full semantics — collisions, `None` converter, `ParamCoercionError`, unknown-name
rules all preserved:

```
current                1.32 μs
restructured           0.65 μs   (2.03x)
saving                  671 ns per request
```

Against the exact-name median that is 14.9% → 8.2% of the request.

An earlier prototype measured 0.64 μs but had quietly dropped the error handling; the 0.65 μs above is
the honest number. Do not trust a reported figure from this area that has not been diffed against
`bind`'s actual behaviour.

## Invariants the tests must hold

This is the whole risk. `bind` is small but every branch is load-bearing, and the second change moves
the loop from "declared parameters" to "supplied names" — which is precisely where an omission would
hide. Existing coverage in `api/tests/test_param_binding.py` already pins most of these; the ones
without a test today are marked.

- A parameter with a default and no supplied value gets its default.
- A parameter with **no** default and no supplied value is **absent** from the result, so the handler
  raises `TypeError` rather than receiving `None`. *(no direct test today)*
- A supplied non-string passes through unconverted — this is how `falcon_response` arrives.
- A supplied string is converted per its annotation; failure raises `ParamCoercionError` naming the
  parameter, and for enums the accepted values.
- A declared parameter whose annotation has no converter raises when a **string** arrives, but not
  when an object does.
- An unknown **string** is dropped; an unknown **non-string** passes through, so handlers without
  `**kwargs` still raise `TypeError`. Load-bearing — see the `read_sql` note in the routing plan.
- Positional values map onto positional names in declaration order.
- More positional values than the handler accepts raises `TypeError`.
- A positional and a keyword for the same parameter raises `TypeError` rather than one silently
  winning.
- Defaults are not shared across calls: mutating a returned dict must not affect the next request.
  *(no test today; a new failure mode introduced by copying a precomputed defaults dict — the copy is
  shallow, so a mutable default would be shared)*

The last one is new and specific to this change. No handler currently declares a mutable default, and
a test should assert that stays true rather than relying on it.

## Rejected

**An optimistic fast path with no error handling, falling back to a careful slow path.** Structurally
sound — a failure in the fast path re-runs the careful one, which produces the real diagnostics, and
`converter(value)` with a `None` converter raises `TypeError` and lands in the fallback on its own.
It buys nothing:

```
restructured, full checks     0.63 μs
optimistic + fallback         0.64 μs
```

Python 3.13 has had zero-cost exceptions since 3.11, so the `try` is free when nothing raises, and the
`converter is None` guard is a single `is` comparison. Skipping both costs a `name in converters`
followed by `converters[name]` — two lookups where the careful version does one `.get()`. The saving
is negative. Not worth two code paths that must agree on success semantics, where the fast one is
free to diverge silently.

**Cache the whole bound dict per (path, query-string).** The response cache already does this one
layer up, keyed more precisely. Binding is not where repeat work is.

**Generate and `exec` a specialized binder per handler at construction.** Would beat 0.65 μs, and is
what the fast serialization libraries do. Not worth an `exec` in the request path for a few hundred
nanoseconds, and it would make the traceback from a coercion failure much worse.

**Do nothing.** Defensible on the corpus median, which is why it was not done in #787. The exact-name
distribution is the counter-argument.

## Open questions

- Is exact-name really the dominant production pattern, or is that the corpus's shape? The corpus is
  a benchmark artifact, not sampled traffic. Access logs would settle it, and would also say whether
  the fixed cost matters more than these numbers suggest.
- `_max_positional_args` returns `float` (`inf` for `*args`), so `positional_capacity` is a float and
  the comparison in `_resolve_action` is float-vs-int. Unrelated to this change, but noticed while
  measuring, and an int with a sentinel would be marginally cheaper on every request.
