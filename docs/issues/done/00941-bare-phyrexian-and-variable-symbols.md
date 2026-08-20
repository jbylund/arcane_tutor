# `_ATOMS` Accepted Bare Symbols No Real Cost Ever Has: `P`, `Y`, `Z`

**DONE — fixed on the `mana-atom-and-explanation-followups` branch, stacked on #909.**

[#941](https://github.com/jbylund/sylvan_librarian/issues/941), found during review of #909.

`_ATOMS` in [api/parsing/mana_symbols.py](../../api/parsing/mana_symbols.py) included `P`, `Y`, and
`Z` as standalone valid mana atoms, but none of the three ever appears unpaired/alone in a real cost:

- `P` (Phyrexian) only ever appears paired with a colour, e.g. `{W/P}` — `_PART_SHAPES` already gets
  this right, there is no bare-`P` shape in it.
- `Y`/`Z` are real symbols, but printed exactly once, together with `X` and each other, on *The
  Ultimate Nightmare of Wizards of the Coast® Customer Service*'s `{X}{Y}{Z}{R}{R}` (Unglued) —
  confirmed via the Scryfall API. `preprocess_card` filters every `set_type == "funny"` card out of
  the corpus, so no mana cost a real search can match ever contains them, the same reasoning already
  applied to `∞` and `H`.

Checked against the 97,206-card corpus in `benchmarks/bitplanes/corpus.jsonl`: `X` (a real bare atom)
appears 1,984 times; `S` (snow) appears 3 times; bare `P`, `Y`, and `Z` appear **0** times each.
`mana:{P}`, `mana:{Y}`, `mana:{Z}` therefore silently returned zero rows instead of 400ing — the same
"matches nothing, should have errored" failure mode `{T}`/`{Q}`/`{E}` correctly reject today.

## Fix

Dropped `P`, `Y`, `Z` from `_ATOMS`, leaving `_COLORS | frozenset("CSX")`. `Y`/`Z` moved to
`test_mana_symbols.py`'s `_REAL_BUT_NOT_A_COST` alongside `∞`/`HW`/`HR`; `P` stays reachable only
through the paired shapes `_PART_SHAPES` already enumerates (`{W/P}`, `{2/P}`, hybrid-phyrexian).

## Tests

- `is_valid_mana_symbol("P")`, `("Y")`, `("Z")` → `False`
- `is_valid_mana_symbol("W/P")`, `("2/P")` → unaffected, still `True`
- `first_invalid_mana_symbol("{P}")` → `"{P}"`
