# `mana:""` and `devotion:""` Silently Match Every Card

[#937](https://github.com/jbylund/sylvan_librarian/issues/937), filed alongside the #936 review follow-ups.

`first_invalid_mana_symbol("")` returns `None`, so an empty mana value passes validation and reaches
the database as a query that matches everything.

## Why

[`first_invalid_mana_symbol`](../../api/parsing/mana_symbols.py) exists specifically to reject a
bare character that `mana_cost_str_to_dict`/`calculate_cmc` would otherwise silently drop instead of
erroring — its own docstring calls out `'Q'` in `'2WWQ'` as the motivating case. The empty string is
the same failure mode taken to its edge: both of its scan loops (`_BRACED_SYMBOL.findall("")` and
iterating `""`) run zero times, so there is no character for either loop to reject on.

Traced end-to-end, `mana:""` and `devotion:""` parse successfully on both parsers and produce:

```
SQL:    (%(dict)s <@ card.mana_cost_jsonb AND card.cmc >= %(int)s)
params: {dict: {}, int: 0}
```

An empty dict is a jsonb-subset of every card's `mana_cost_jsonb`, and `cmc >= 0` is always true, so
the query returns the entire corpus instead of a 400 — the exact "matches everything" shape the
module was written to prevent for stray characters, just left open for the empty case.

This is not a regression: the pre-fix code returned a `StringValueNode("")` here instead of a
`ManaValueNode("")`, and both node types read `.value` generically in `_handle_mana_cost_comparison`
(and in the Rust engine's `rhs_value_str`), so the SQL/params produced are identical either way. The
gap has existed since before #909; that PR's validation pass just didn't happen to close it.

## Fix

Reject an empty value explicitly, at the top of `first_invalid_mana_symbol`. The simplest option
returns a sentinel that flows through the existing `Unknown mana symbol: {symbol}` message:

```python
def first_invalid_mana_symbol(value: str) -> str | None:
    if not value:
        return "(empty)"
    ...
```

`"(empty)"` can never collide with a real symbol (parens aren't in `_BARE_ATOMS` or any braced form),
so it is unambiguous as an offending-symbol placeholder without adding a second return shape that
every caller would need to branch on.

## Tests

`test_mana_symbols.py` and `test_regex_patterns.py` (wherever the shared mana-value fixtures live):

- `mana:""`, `devotion:""` → 400, not a 200 with every card
- confirm the fix doesn't affect `mana:0` or `mana:{0}` (a real, zero-cost value that must keep
  parsing and matching only colorless-free cards, not "empty")

## Status: deferred, not scheduled

Checked live against `api.scryfall.com` (2026-08-22) before implementing this, since the fix above
assumes "reject the whole query" is the right target behavior — it isn't obviously so:

- `mana:""` and `mana=""` both return a genuine HTTP 500 from Scryfall's own API. There is no
  working reference behavior to copy for the mana case specifically.
- The closest non-crashing analogues, `o:""` and `devotion:""` standalone, return "All of your
  terms were ignored" — but embedded in a compound query, Scryfall does not reject the whole
  query: `t:creature devotion:""` returns the same 18,753 cards as plain `t:creature`. Scryfall's
  actual behavior is "silently drop just the empty term," not "reject the whole query."

That means the fix as scoped above would diverge from Scryfall in the compound case: it rejects
`t:creature devotion:""` outright, where Scryfall answers it (correctly, matching `t:creature`
alone) by dropping the empty term. The bug is real for the standalone case (matching the entire
unfiltered corpus is clearly wrong), but "reject the whole query" overcorrects for the compound
one, and "drop just the term" would need different, more invasive plumbing (the empty leaf would
need to disappear from the AST rather than erroring at parse time).

Decided not to act on this for now. If revisited: the stated preference leans toward "reject the
whole query" over "silently drop the term" on general principle (`P AND (invalid Q)` should not
silently reduce to just `P` — an invalid subexpression should poison the whole expression), even
though that's a deliberate divergence from Scryfall's own "ignore the bad term" behavior rather
than a faithful port of it.
