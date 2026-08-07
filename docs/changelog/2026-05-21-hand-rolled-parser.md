# Hand-rolled recursive-descent parser replaces pyparsing (#482)

The query parser is now a hand-rolled recursive-descent implementation
([api/parsing/hand_parser.py](../../api/parsing/hand_parser.py)), replacing pyparsing as the
production parse path. On a diverse query set it parses ~49x faster (158k vs 3.2k parses/sec).

## What changed

- `api.parsing.parse_search_query` is the new primary entry point, backed by the hand-rolled
  parser. It handles the full query DSL: arithmetic expressions, negation, implicit AND,
  quoted strings, and parenthesised sub-expressions.
- The pyparsing grammar was extracted from `parsing_f.py` into
  [pyparsing_based.py](../../api/parsing/pyparsing_based.py) and demoted to a comparison-testing
  path; `parsing_f.py` is now a thin shim.
- Shared SQL-generation helpers moved to
  [sql_generation.py](../../api/parsing/sql_generation.py), and `flatten_nested_operations`
  moved into `nodes.py` so both parsers produce canonical n-ary AND/OR trees.

## Parity guarantee

A parametrized parity suite
([test_parser_parity.py](../../api/parsing/tests/test_parser_parity.py)) runs every query in
the consolidated corpus through both parsers and asserts identical SQL output (or that both
fail). The shared `parse_query` fixture in `conftest.py` also runs the pre-existing parser
tests against both implementations automatically.

Parity testing surfaced and fixed several latent pyparsing bugs along the way — AND/OR
precedence, a crash on parenthesised arithmetic LHS, and rejection of negated parenthesised
expressions. Details in
[hand-parser-parity-gaps](2026-05-21-hand-parser-parity-gaps.md).
