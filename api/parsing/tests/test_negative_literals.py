"""Tests for negative numeric literals in value position, e.g. 'power>-1' (#891)."""

import pytest

from api.parsing.card_query_nodes import CardAttributeNode
from api.parsing.nodes import BinaryOperatorNode, NotNode, NumericValueNode

testcases = {
    "greater_than": {"query": "power>-1", "column": "creature_power", "operator": ">", "value": -1},
    "greater_or_equal": {"query": "power>=-1", "column": "creature_power", "operator": ">=", "value": -1},
    "less_than": {"query": "toughness<-2", "column": "creature_toughness", "operator": "<", "value": -2},
    "equals": {"query": "power=-1", "column": "creature_power", "operator": "=", "value": -1},
    "not_equals": {"query": "power!=-1", "column": "creature_power", "operator": "!=", "value": -1},
    # ':' on a numeric attribute is Scryfall's spelling of '='; it stays ':' in the AST and
    # becomes '=' at SQL generation.
    "colon_operator": {"query": "power:-1", "column": "creature_power", "operator": ":", "value": -1},
    "float_literal": {"query": "power>-1.5", "column": "creature_power", "operator": ">", "value": -1.5},
    "cmc": {"query": "cmc>-1", "column": "cmc", "operator": ">", "value": -1},
    "loyalty": {"query": "loyalty>-1", "column": "planeswalker_loyalty", "operator": ">", "value": -1},
    # The '-' need not hug the operator: nothing else can start a value here.
    "spaces_around_operator": {"query": "power > -1", "column": "creature_power", "operator": ">", "value": -1},
    # cn/number are dual numeric/text aliases — a signed literal picks the numeric branch,
    # the same as a bare one does.
    "dual_class_alias": {"query": "cn>-1", "column": "collector_number_int", "operator": ">", "value": -1},
}


@pytest.mark.parametrize(
    argnames=sorted(next(iter(testcases.values()))),
    argvalues=[[v for k, v in sorted(testcases[testname].items())] for testname in sorted(testcases)],
    ids=sorted(testcases),
)
def test_negative_literal_parses(parse_query, column: str, operator: str, query: str, value: float) -> None:
    """A negative literal on the right of a comparison parses to a negative NumericValueNode."""
    root = parse_query(query).root
    assert isinstance(root, BinaryOperatorNode), f"{query!r} did not parse to a comparison: {root}"
    assert isinstance(root.lhs, CardAttributeNode)
    assert root.lhs.attribute_name == column
    assert root.operator == operator
    assert root.rhs == NumericValueNode(value)


def test_signed_literal_leads_arithmetic_value(parse_query) -> None:
    """Only the leading term of a value may be signed, and it may still start an arithmetic tail."""
    rhs = parse_query("power>-1+2").root.rhs
    assert isinstance(rhs, BinaryOperatorNode)
    assert rhs.lhs == NumericValueNode(-1)
    assert rhs.operator == "+"
    assert rhs.rhs == NumericValueNode(2)


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[
        ("power>-cmc",),  # negating an attribute is not supported, only literals carry a sign
        ("power>cmc+-1",),  # a sign is only valid on the leading term of a value
        ("power>cmc--1",),
    ],
    ids=["negated_attribute", "sign_after_arithmetic_op", "sign_after_subtraction"],
)
def test_unsupported_signs_rejected(parse_query, query: str) -> None:
    """Signs outside the leading term of a value remain parse errors."""
    with pytest.raises(ValueError, match="Failed to parse query"):
        parse_query(query)


def test_leading_minus_is_still_negation(parse_query) -> None:
    """A '-' that opens a factor is filter negation, not a sign — '-1<power' is NOT (1<power)."""
    root = parse_query("-1<power").root
    assert isinstance(root, NotNode)
    assert isinstance(root.operand, BinaryOperatorNode)
    assert root.operand.lhs == NumericValueNode(1)
    assert root.operand.operator == "<"
    assert root.operand.rhs.attribute_name == "creature_power"
