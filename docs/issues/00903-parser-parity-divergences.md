# The two parsers disagree on 15 real queries

[#903](https://github.com/jbylund/sylvan_librarian/issues/903).

`test_parser_parity.py` asserts the hand-rolled and pyparsing parsers emit identical SQL, but it
runs over [`implicit_and_cases.py`](../../api/parsing/tests/implicit_and_cases.py) — a corpus they
both pass. Sweeping the 14,473-query [`benchmarks/wild-queries/wild-corpus.jsonl`](../../benchmarks/wild-queries/)
through both found **15 divergent queries**, from **three** root causes.

Found while measuring the blast radius of [#891](https://github.com/jbylund/sylvan_librarian/issues/891).
Pre-existing on `main` and unrelated to it: 15 before that change, the same 15 after.

## How they were found

Generate SQL + bound params for every distinct corpus query under both parsers, compare. The same
harness answers "did this change alter any real query" and is worth rerunning after parser work —
the 17,881-query sweep takes seconds. Repro is a ~20-line script: import both entry points, diff
`generate_sql_query(...)` output per query.

## A. `is_arithmetic_minus` ignores comparisons nested in a group — 10 of 15

[`preprocess_implicit_and`](../../api/parsing/pyparsing_based.py) decides whether a `-` is binary
subtraction or filter negation. Its `_rhs_introduces_comparison` guard scans forward for a
comparison operator, but counts them **only at `depth == 0`**:

```python
elif t in _COMPARISON_OPERATORS and depth == 0:
    return True
```

For `cmc>1 -(t:elf)` the `(` pushes depth to 1 before the `:` is seen, so the guard returns False,
`is_arithmetic_minus` wins, and no implicit AND is inserted:

| | result |
|---|---|
| hand | `(cmc > 1) AND NOT (card_subtypes @> [Elf])` |
| pyparsing | `cmc > (1 - (card_subtypes @> [Elf]))` |

The pyparsing SQL is not just different, it is **invalid** — PostgreSQL has no `integer - boolean`,
so it is a runtime 500 rather than wrong rows. Two flavors:

- **numeric LHS → bad SQL**: `pow=3 -(name:force or type:elf)`, `usd>50 -(format:modern or format:commander)`
- **year LHS → parse error**: 8 queries shaped like `year:2019 -(oracle:exile or type:enchantment)`

The trigger is only the token *before* the `-` being numeric. `t:elf -(t:goblin or t:orc)` is fine,
and so is `year:2019 -t:elf` without the parens.

**Fix sketch:** track depth in `_rhs_introduces_comparison` so a comparison anywhere inside the
group counts. Worth checking whether the depth-0 restriction was load-bearing for some other case
before changing it — `git log -S_rhs_introduces_comparison` for the original intent.

## B. `and`/`or` in value position eaten by the tokenizer — 2 of 15

`_get_implicit_and_tokenizer` matches `CaselessKeyword("AND"/"OR")` before anything knows it is in
value position, so the value disappears and becomes a boolean operator:

```
o:and power+toughness>10   ->  o: AND power+toughness>10
(o:or o:more) t:land       ->  (o: OR o:more) AND t:land
```

Only on the slow path. `preprocess_implicit_and` short-circuits queries containing none of
`()"'/{+*`, so `o:and power>10` is fine while `o:and power+toughness>10` is not — the `+` is what
disqualifies it. That makes the bug look intermittent.

It sometimes recovers with the right answer, which is worse than failing:
`(oracle:two oracle:or) type:land` preprocesses to a dangling `oracle:` whose value then matches
the following `OR` token as the string `"OR"` — coincidentally what the user meant.

**Fix sketch:** the tokenizer needs to not treat a keyword as an operator when the previous token
is a comparison operator. The hand parser gets this right for free by parsing values in context
rather than pre-tokenizing.

## C. `!` is Scryfall's `=` alias, unimplemented here — 3 of 15

`c!ubg` is not a distinct "exact color" operator. On Scryfall `!` is simply an alias for `=`.
Verified live against api.scryfall.com, 2026-08-08 — `total_cards` for each spelling:

| query | with `!` | with `=` |
|---|---|---|
| `c!g` | 4,904 | 4,904 |
| `c!ubg` | 57 | 57 |
| `id!rg` | 471 | 471 |
| `cmc!3` | 8,077 | 8,077 |
| `pow!3` | 4,079 | 4,079 |
| `usd!5` | 39 | 39 |
| `r!rare` | 11,766 | 11,766 |
| `mana!2G` | 1,014 | 1,014 |
| `devotion!gg` | 1,090 | 1,090 |
| `year!2020` | 3,886 | 3,886 |
| `date!2020-01-01` | 17 | 17 |

The alias is **not** universal. For text-valued fields Scryfall does not treat `!` as an operator
at all — it falls back to reading the `!` as its exact-name prefix, which is precisely what our
hand parser does:

| query | Scryfall | vs `=` spelling |
|---|---|---|
| `t!creature` | 0 | `t=creature` → 18,751 |
| `o!flying` | 0 | `o=flying` → 4,574 |
| `s!khm` | 0 | `s=khm` → 323 |
| `f!modern` | 2 | `f=modern` → 22,450 |

So the hand parser's `c!w` → `name:"c" AND !w` is the **correct** reading for TEXT and LEGALITY
fields, and wrong only for the classes where `!` really is an operator. pyparsing rejects the shape
outright, which is wrong in both directions.

Affected: `c!ubg cmc>=6 f:standard`, `en-kor c!w`, `o:destroy o:creature o:with o:flying c!g`.

**Fix sketch:** accept `!` as an `=` alias for `ParserClass` COLOR, MANA, NUMERIC, RARITY, YEAR and
DATE; keep the existing implicit-name fallback for TEXT and LEGALITY. The wrinkle is tokenization:
`!=` must keep winning over `!` (longest match), and the hand lexer emits `BANG` rather than `OP`,
so `parse_word_primary` has to accept a `BANG` after an alias of a supporting class. That is a
smaller and better-specified change than "implement exact-color matching" — the comparison
semantics already exist, only the spelling is missing.

## Suggested order

**A** first: it produces invalid SQL from a query shape users write, and the fix is one function.
**C** is a missing operator spelling rather than missing semantics, so it is cheap and it removes a
silent-zero-rows case. **B** is the narrowest.

## What shipped so far

First, only the pin: [`test_parser_parity.py`](../../api/parsing/tests/test_parser_parity.py) gained
`test_known_parser_divergences`, parameterized over all 15 wild queries plus 4 minimal repros, each
`xfail(strict=True)`. Fixing any cause turns its entries into `XPASS(strict)` failures, so a fix
cannot land without deleting the entries it resolves — the list can only shrink.

The shared assertion moved into `assert_parsers_agree()` so both parity tests use one comparison.

**A fixed**: `_rhs_introduces_comparison` now counts a comparison operator at any paren depth, not
just depth 0 — a one-line change (dropping `and depth == 0` from the comparison branch). All 12
cause-A entries removed from `KNOWN_DIVERGENCES`; regression cases added to
[`implicit_and_cases.py`](../../api/parsing/tests/implicit_and_cases.py) covering the numeric-LHS,
year-LHS, and single-vs-`OR`-group shapes.

**C fixed**: `!` now aliases `=` on COLOR/MANA/NUMERIC/RARITY/YEAR/DATE in both parsers — the hand
parser accepts a `BANG` token wherever it accepted `OP` for those classes (`parse_word_primary` in
`hand_parser.py`); pyparsing gained an `EQ_ALIAS_OPERATORS = DEFAULT_OPERATORS | Literal("!").set_parse_action(lambda: "=")`
used only by the five eligible `condition`s (`mana_condition`, `color_condition`,
`rarity_condition`, `date_condition`, `year_condition`) plus `unified_numeric_comparison`;
`legality_condition`/`text_condition` are untouched, so `!=` still wins over bare `!` by
longest-match and TEXT/LEGALITY still fall through to the pre-existing exact-name fallback. All 4
cause-C entries removed from `KNOWN_DIVERGENCES`.

Residual, out of scope for this fix: pyparsing hard-rejects `field!value` on TEXT/LEGALITY fields
(e.g. `t!creature`) regardless of class, where the hand parser has always had the fallback
reading — this divergence was never in the wild-corpus sweep (no such query appeared), so it isn't
one of the tracked 15 and is left for a future pass.

B remains open.
