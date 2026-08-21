# pyparsing Silently Drops Bare Mana Characters It Doesn't Recognize

[#954](https://github.com/jbylund/sylvan_librarian/issues/954).

Confirmed still live on current main (post-#909): `mana:s`, `mana:snow`, `mana:p`, and `mana:hello`
all still silently produce wrong or empty results via the pyparsing parser, even though the hand
parser correctly rejects every one of them.

## Confirmed reachable, right now

```python
>>> generate_sql_query(parsing_f.parse_scryfall_query("mana:s"))
ValueError: Failed to parse query: "mana:s"          # hand parser: correct
>>> generate_sql_query(parse_search_query("mana:s"))
(..., {'p_dict_e30': {}, 'p_int_MA': 0})              # pyparsing: {} matches every card with a cost
>>> generate_sql_query(parse_search_query("mana:snow"))
(..., {'p_dict_...': {'W': [1]}, 'p_int_MQ': 1})      # pyparsing: silently answers mana:w instead
```

## Why

Two independent gaps compound:

1. [`pyparsing_based.py`](../../api/parsing/pyparsing_based.py)'s mana tokenizer regex
   (`simple_mana_symbol = Regex(r"[0-9WUBRGCXYZwubrgcxyz]")`, in two places) accepts `S` as a
   mana-token character at the grammar level, so `mana:snow` becomes a `ManaValueNode` at all — the
   hand parser's own bare-character alphabet doesn't include `S`, so the query never becomes a mana
   value there in the first place.
2. Once a `ManaValueNode` exists, nothing on the pyparsing path calls `first_invalid_mana_symbol` to
   validate it the way `hand_parser.parse_mana_value` does, so
   [`mana_cost_str_to_dict`](../../api/parsing/card_query_nodes.py) receives the raw, unvalidated
   string. Its own character loop only recognizes `WUBRGCX`, silently dropping anything else: `S` in
   `"snow"` vanishes, leaving `{'W': [1]}` from the trailing `w`; every character in
   `"hello"`/`"s"`/`"p"` vanishes, leaving `{}` — a subset of every cost, so the query matches every
   card that has one at all.

## Prior art — do not just reopen or rebase #915

[#915](https://github.com/jbylund/sylvan_librarian/pull/915) diagnosed this exact bug in detail
(verified row counts, a "five copies of what is a cost symbol" table) and proposed unifying every
copy under one shared, public `MANA_COST_ATOMS` constant. Closed rather than rebased: its branch
predates [#941](done/00941-bare-phyrexian-and-variable-symbols.md) (merged), and its proposed
`MANA_COST_ATOMS` value — `_COLORS | frozenset("CSXYZP∞")` — still includes bare `P`, `Y`, `Z` as
valid atoms. #941 established that none of those three ever appears unpaired in a real (non-funny)
mana cost and made `is_valid_mana_symbol("P")` etc. return `False`; reusing #915's constant as-is
would silently regress that fix back in, since the same shared constant would flow into the
bare-character validation this issue is about too.

A fresh fix needs to unify the vocabulary (closing this issue's gap) while keeping #941's exclusions
(not regressing that one) — read both before starting.

## Reference

#915's diagnosis is still a useful map: five separate copies of "what is a cost symbol" existed —
`mana_cost_str_to_dict`, `calculate_cmc`, and `pyparsing_based`'s two `simple_mana_symbol` patterns
each read their own hardcoded charset (all incomplete in different, overlapping ways), while
`mana_symbols.py`'s (post-#941) `_ATOMS` is the one that's actually correct today.

## Fix

The "confirmed reachable" repro above says the hand parser "correctly rejects" `mana:s` — that
framing turned out to be half right. `mana:{s}` (braced) already resolved correctly on both parsers
to `{'S': [1]}`, `cmc >= 1`; `mana:s` (bare) should mean the same thing, per real Scryfall behavior,
but both parsers rejected/mishandled it because `BARE_MANA_ATOMS` in `card_query_nodes.py` — the
bare-character alphabet `mana_cost_str_to_dict`, `calculate_cmc`, and (via `mana_symbols.py`)
`first_invalid_mana_symbol` all read — was `frozenset("WUBRGCX")`, missing `S`. Fixing that one
constant fixed the hand parser and `first_invalid_mana_symbol` together (single source of truth), so
`mana:s` now agrees with `mana:{s}` on both parsers, and `calculate_cmc`'s bare-character regex
gained `S` too (a bare `S` now contributes 1 to cmc, same as its braced form).

The other half — `mana:snow`/`mana:p`/`mana:hello` silently resolving to a plain string comparison
on the pyparsing path — was the grammar gap from "Why" above. `pyparsing_based.py`'s two
`simple_mana_symbol` regexes were widened from an enumerated letter subset to `[0-9A-Za-z]`: which
bare characters are *valid* is `first_invalid_mana_symbol`'s call, not the tokenizer's — a narrower
tokenizer charset just means some bare values never reach that validator and fall through to
`string_value_word` instead. That fallback was also dropped from `mana_value_or_string` entirely
(`mana_value | mana_quoted_value`, no more `| string_value_word`): a mana/devotion condition's rhs is
now always a validated `ManaValueNode`, never an unchecked `StringValueNode`, mirroring
`hand_parser.parse_mana_value`'s shape directly rather than relying on the tokenizer to always be
wide enough.

## Tests

- `first_invalid_mana_symbol("2WWS")` → `None` (was `"S"`); `first_invalid_mana_symbol("SNOW")` →
  `"N"` (the same offender `{s}{n}{o}{w}` reports as `"{N}"`)
- `test_bare_mana_character_parity` (`test_parser_parity.py`): `mana:s`, `mana:snow`, `mana:p`,
  `mana:hello` — hand and pyparsing parsers agree on all four
- `test_full_sql_translation_jsonb_colors` (`test_sql_gen.py`): `mana:s` and `mana:{S}` produce
  identical SQL/parameters, on both parsers
