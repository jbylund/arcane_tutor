# Hand-rolled parser parity gaps (resolved)

Discovered via `test_parser_parity.py`, which runs every query in
`implicit_and_cases.py` through both parsers and asserts identical SQL output.
Started with 22 failing cases; all fixed.

## 1. Pyparsing AND/OR precedence

**Query:** `a OR b AND c`

Pyparsing evaluated left-to-right (`(a OR b) AND c`) instead of respecting
AND > OR precedence. Fixed by splitting the grammar into two levels:
`and_expr = factor + ZeroOrMore(AND + factor)` at higher precedence, then
`expr = and_expr + ZeroOrMore(OR + and_expr)`.

## 2. AND/OR flattening

**Queries:** `(foo bar) baz`, `a (b c)`, `(a AND b) c`

Pyparsing was flattening nested AND chains into `AndNode(a, b, c)` while
hand-rolled kept them nested as `AndNode(AndNode(a, b), c)`. Fixed by moving
`flatten_nested_operations` from `pyparsing_based.py` into `nodes.py` and
calling it in both parsers, producing canonical n-ary AND/OR nodes everywhere.

## 3. Pyparsing crash — paren on arithmetic LHS

**Queries:** `(2*power)-1>3`, `(power+toughness)-cmc>0`, `power-(cmc-1)>2`,
`(power+1)-(cmc-1)>0`, and spaced variants.

`arithmetic_term` used `Group(lparen + expr + rparen)`, which wrapped the inner
result in a `ParseResults` sublist. `create_value_node` didn't recognise it as a
`QueryNode`, leaving a raw `ParseResults` in the AST that raised
`TypeError: 'str' object is not callable` when `.to_sql()` was called.

Fixed by dropping the `Group` wrapper — since `lparen` and `rparen` are
suppressed, `(lparen + expr + rparen)` yields exactly one `QueryNode` token
as intended.

## 4. Hand-rolled parser gaps

### 4a. Arithmetic subtraction with spaces

The hand-rolled parser treated a space before `-` as an AND boundary. Fixed by
adding `_spaced_sub_tail`, which triggers when both the `-` and the following
operand have `space_before=True` and the operand is a numeric term. This
correctly distinguishes `power - cmc` (arithmetic) from `power -cmc` (negation).

### 4b. Comparison followed by spaced negated expression

**Queries:** `Power>2 -1+CMC<2`, `power>2 -cmc>0`, and similar.

`parse_num_expr_value` was treating ` -cmc` (space before MINUS, no space after)
as arithmetic subtraction, consuming `cmc` into the RHS and leaving `> 0`
unmatched. Fixed by breaking out of `parse_num_expr_value` when MINUS has
`space_before=True` and the following token has `space_before=False`.

Also fixed a pyparsing issue where `-(cmc-1)>0` was rejected because
`negatable_primary` grabbed just `(cmc-1)` instead of the full condition.
Fixed by adding `paren_expr_term = (lparen + expr + rparen)` as a valid LHS/RHS
in `unified_numeric_comparison`.

### 4c. Spaced paren-minus-paren

**Query:** `(power + 1) - (cmc - 1) > 0`

`_spaced_sub_tail` only handled `-`, so spaced `+` inside parenthesised groups
failed. Replaced with `_spaced_arith_tail`, which handles all four operators
(`+`, `-`, `*`, `/`) with spaces, keeping the `space_before` guard on `-` only
(to preserve the negation disambiguation).

### 4d. Multi-hyphen bare words

**Query:** `a-b-c`

Known attribute aliases (e.g. `a` → artist) without a following operator were
returning early via `_name_node` instead of calling `parse_hyphenated_name`.
Fixed by routing that branch through `parse_hyphenated_name`, which checks for
no-space hyphen continuations.
