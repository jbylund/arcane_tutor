# `first_invalid_mana_symbol` Can Name the Wrong Offending Symbol

**DONE — fixed in [#940](https://github.com/jbylund/sylvan_librarian/pull/940)**, via the exact
single-pass rewrite this doc proposed below.

[#938](https://github.com/jbylund/sylvan_librarian/issues/938), filed alongside the #936 review follow-ups.

[`first_invalid_mana_symbol`](../../api/parsing/mana_symbols.py#L82) checks every braced symbol
before checking any bare character, regardless of which one actually comes first in the string. On
mixed input this can report a later invalid symbol while a genuinely earlier one goes unmentioned.

## Why

The docstring promises "the first symbol in *value* that no mana cost could contain," but the
implementation is two sequential loops:

```python
for symbol in _BRACED_SYMBOL.findall(value):      # all braced symbols, left to right
    if not is_valid_mana_symbol(symbol):
        return f"{{{symbol}}}"
for char in _BRACED_SYMBOL.sub("", value):         # all bare characters, left to right
    if char not in _DIGITS and char not in _BARE_ATOMS:
        return char
```

Each loop is individually left-to-right, but braced symbols are always checked before bare
characters, no matter where either appears in the original string. `first_invalid_mana_symbol("H{Q}")`
returns `"{Q}"`, even though the bare `H` at position 0 is also invalid and appears earlier than
`{Q}`.

The query is rejected either way — this is a wrong-error-message bug, not an accept/reject
correctness bug. A 400 naming `{Q}` when the user's actual mistake was the leading `H` still points
them somewhere in the query, just not at the first thing they'd need to fix, and it can be actively
misleading if the user pastes the fix for `{Q}` and hits `H` in a second search.

## Fix

Merge into a single position-ordered scan. `finditer` over the braced-symbol pattern already gives
match positions, so an index-annotated walk can decide bare-vs-braced by position instead of by
which loop runs first:

```python
def first_invalid_mana_symbol(value: str) -> str | None:
    cursor = 0
    for match in _BRACED_SYMBOL.finditer(value):
        for char in value[cursor:match.start()]:
            if char not in _DIGITS and char not in _BARE_ATOMS:
                return char
        symbol = match.group(1)
        if not is_valid_mana_symbol(symbol):
            return f"{{{symbol}}}"
        cursor = match.end()
    for char in value[cursor:]:
        if char not in _DIGITS and char not in _BARE_ATOMS:
            return char
    return None
```

This also drops the second full-string pass that `_BRACED_SYMBOL.sub("", value)` does today (see the
efficiency note on the same function from the #909 review) — the rewrite is a single walk plus one
`finditer` pass, not two.

## Tests

`test_mana_symbols.py`:

- `first_invalid_mana_symbol("H{Q}")` → `"H"`, not `"{Q}"`
- `first_invalid_mana_symbol("{Q}H")` → `"{Q}"` (braced invalid genuinely comes first here)
- existing all-braced and all-bare cases stay unchanged, to pin that the merge didn't change
  behavior for inputs that don't mix the two forms
