# Leading negative literal: sign or negation?

[#901](https://github.com/jbylund/sylvan_librarian/issues/901). Follow-on to
[#891](https://github.com/jbylund/sylvan_librarian/issues/891), which made `power>-1` parse.

After #891 the same number written two ways means two different things:

```
power>-1  →  creature_power > -1
-1<power  →  NOT (1 < creature_power)      i.e. power <= 1
```

## Why they diverge

#891 established one rule with no lookahead:

- `-` where a **filter** can start → negation
- `-` where a **value** must appear → sign

The value case is unambiguous by construction: filter negation and binary subtraction both need a
preceding operand, and the comparison operator has just consumed that position. That is why
[`parse_signed_num_term`](../../api/parsing/hand_parser.py) needs no spacing rule, unlike
`_spaced_arith_tail`.

Leading position is the opposite: what follows a leading `-` becomes a boolean, so negation is the
natural reading, and `-t:creature` is core Scryfall syntax. The divergence above is the price of
that rule, not an oversight in it.

## Proposal

Let the sign win over negation **only when the `-` is followed by a numeric literal**.

| query | today | proposed |
|---|---|---|
| `-1<power` | `NOT (1 < power)` → power <= 1 | `(-1) < power` → power > -1 |
| `-1+power>2` | `NOT ((1 + power) > 2)` → power <= 1 | `((-1) + power) > 2` → power > 3 |
| `-power>0` | `NOT (power > 0)` | unchanged |
| `-cmc+5>1` | `NOT ((cmc + 5) > 1)` | unchanged |
| `-(cmc-1)>0` | `NOT ((cmc - 1) > 0)` | unchanged |
| `-t:creature` | `NOT (t:creature)` | unchanged |

Keying on the *literal* is what keeps the last four rows fixed. The rejected variant is below.

## The cost

`-1<power`, `-2>=cmc` and `-1+power>2` parse today and emit valid, meaningful SQL. The change
**silently flips working queries** — the failure mode with no error message and no deprecation path.
Only the non-comparison forms are currently unusable:

```
-1              →  NOT (1)                 datatype mismatch at PostgreSQL
-1 t:creature   →  NOT (1) AND t:creature  datatype mismatch at PostgreSQL
```

The mitigating argument: the affected shape is a comparison with a bare literal on the **left** and
the attribute on the right, which is rare because `-1<power` is a roundabout spelling of `power>1`
— and `power>1` is exactly the reading the proposal removes. So the queries that break are the ones
whose author was relying on the current rule deliberately.

## Rejected: sign binds to the first term of any arithmetic expression

The broader version — a leading `-` signs the next *term* whatever it is, with `-(expr)` reserved
for negation — was considered and rejected. It turns `-power>3` into `(-power) > 3`, which matches
almost nothing, and it makes parentheses mandatory to negate any numeric comparison. That is a
capability loss on syntax users import from Scryfall, and a silent one.

The parser already encodes the opposing intent: `-(cmc-1)` alone raises *"Cannot negate an
arithmetic expression"*. Negation applies to booleans; a sign has no home outside value position.

## Scope

Both parsers change together, plus the preprocessor that decides AND-insertion around a leading `-`:

- [`api/parsing/hand_parser.py`](../../api/parsing/hand_parser.py) — `parse_factor`
- [`api/parsing/pyparsing_based.py`](../../api/parsing/pyparsing_based.py) — `handle_negation`,
  `preprocess_implicit_and`
- `test_parser_parity.py` holds the two in lockstep over
  [`implicit_and_cases.py`](../../api/parsing/tests/implicit_and_cases.py); the corpus needs the
  flipped cases added, since several of them (`cmp_then_arith_leading_minus`,
  `leading_arith_minus_cmc`) pin the current reading.

## Open question

Whether to do it at all. The divergence is real but affects a rare query shape, and the fix trades a
silent flip for it. Worth deciding deliberately — the alternative is documenting the rule
("a leading `-` always negates") and leaving the asymmetry in place.
