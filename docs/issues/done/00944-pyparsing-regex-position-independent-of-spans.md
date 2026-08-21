# pyparsing Reimplements Regex-Value-Position Independently of `spans.py`

**DONE — closed via a fuzz test, not a refactor, on the `mana-atom-and-explanation-followups`
branch, stacked on #909.**

[#944](https://github.com/jbylund/sylvan_librarian/issues/944), found during review of #909.

`pyparsing_based.py` never imports `api/parsing/spans.py`; it reimplements "regex only opens in
value position" as its own grammar sequence (`regex_after_op = comparison_tok + regex_raw`) instead
of using the shared rule. Nothing caught the two drifting apart except `test_parser_parity.py`'s
fixture list happening to include the specific edge case.

## Why not a structural fix

Making pyparsing's grammar literally call `spans.opens_regex` would mean replacing the declarative
`QuotedString`-based token with a custom parser element whose parse action inspects the raw string
and calls into `spans.py` as a guard — real code sharing, but invasive to a grammar-combinator style
that's currently fully declarative, and it touches the tokenizer's hot path for a drift risk that
hasn't actually caused a bug yet (only a *risk* of one, per the issue). Chose the cheaper option:
prove the two rules agree today, and keep proving it on every future change to either.

## Fix

`api/parsing/tests/test_pyparsing_regex_position.py`: fuzzes `spans.opens_regex` against
`pyparsing_based._tokenize_for_implicit_and` across every operator `hand_parser.tokenize` actually
emits (scanned from its source, the same technique `test_spans.py` uses for `COMPARISON_TAIL_CHARS`),
several whitespace runs, and every non-value-position case (bare word, closing paren, `AND` keyword,
start of query) — 41 cases total, all currently agreeing. A future change to either rule that breaks
the agreement fails here instead of silently shipping.

Deliberately scoped to `hand_parser._SPACE`'s four whitespace characters, not `str.isspace()`'s full
Unicode range — whether `opens_regex` itself should accept wider whitespace is #951, a separate,
still-open question.
