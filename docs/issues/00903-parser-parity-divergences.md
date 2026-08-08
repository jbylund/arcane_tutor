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

## C. `c!` exact-color is unsupported, and one parser hides it — 3 of 15

Scryfall's `c!ubg` ("colors are exactly UBG") is implemented by neither parser. pyparsing rejects
it. The hand parser silently splits it into an implicit name and an exact name:

```
c!w  ->  name:"c" AND !w  ->  name LIKE %c% AND name = "w"
```

Zero rows, no error. The divergence is a symptom; the gap is that `c!`/`id!` is not implemented.
Affected: `c!ubg cmc>=6 f:standard`, `en-kor c!w`, `o:destroy o:creature o:with o:flying c!g`.

This is a feature request wearing a bug's clothes, and splitting it out is reasonable — but the
hand parser's silent mis-parse is worth fixing either way, independently of whether `c!` is ever
implemented.

## Suggested order

**A** first: it produces invalid SQL from a query shape users write, and the fix is one function.
**C** is the worst user-visible behaviour (silent zero rows) but is a missing feature rather than a
defect in an implemented one. **B** is the narrowest.

## What shipped so far

No fixes — only the pin. [`test_parser_parity.py`](../../api/parsing/tests/test_parser_parity.py)
gains `test_known_parser_divergences`, parameterized over all 15 wild queries plus 4 minimal repros,
each `xfail(strict=True)`. Fixing any cause turns its entries into `XPASS(strict)` failures, so a
fix cannot land without deleting the entries it resolves — the list can only shrink.

The shared assertion moved into `assert_parsers_agree()` so both parity tests use one comparison.
