"""Tests for query parsing functionality."""

import pytest

from api import parsing
from api.parsing import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    NumericValueNode,
    OrNode,
    QueryNode,
    StringValueNode,
)


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        ("cmc=3", BinaryOperatorNode(AttributeNode("cmc"), "=", NumericValueNode(3))),
        (
            "cmc=3 power=3",
            AndNode(
                [
                    BinaryOperatorNode(AttributeNode("cmc"), "=", NumericValueNode(3)),
                    BinaryOperatorNode(AttributeNode("power"), "=", NumericValueNode(3)),
                ],
            ),
        ),
        ("name:'power'", BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("power"))),
        ('name:"power"', BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("power"))),
        (
            "cmc+cmc<power+toughness",
            BinaryOperatorNode(
                BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("cmc")),
                "<",
                BinaryOperatorNode(AttributeNode("power"), "+", AttributeNode("toughness")),
            ),
        ),
        (
            "cmc+1<power",
            BinaryOperatorNode(BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)), "<", AttributeNode("power")),
        ),
        (
            "cmc<power+1",
            BinaryOperatorNode(AttributeNode("cmc"), "<", BinaryOperatorNode(AttributeNode("power"), "+", NumericValueNode(1))),
        ),
        (
            "cmc+1<power+2",
            BinaryOperatorNode(
                BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)),
                "<",
                BinaryOperatorNode(AttributeNode("power"), "+", NumericValueNode(2)),
            ),
        ),
        ("cmc+power", BinaryOperatorNode(AttributeNode("cmc"), "+", AttributeNode("power"))),
        ("cmc-power", BinaryOperatorNode(AttributeNode("cmc"), "-", AttributeNode("power"))),
        (
            "cmc + 1 < power",
            BinaryOperatorNode(BinaryOperatorNode(AttributeNode("cmc"), "+", NumericValueNode(1)), "<", AttributeNode("power")),
        ),
        # Test cases for the numeric < attribute bug
        ("0<power", BinaryOperatorNode(NumericValueNode(0), "<", AttributeNode("power"))),
        ("1<power", BinaryOperatorNode(NumericValueNode(1), "<", AttributeNode("power"))),
        ("3>cmc", BinaryOperatorNode(NumericValueNode(3), ">", AttributeNode("cmc"))),
        ("0<=toughness", BinaryOperatorNode(NumericValueNode(0), "<=", AttributeNode("toughness"))),
        # Test cases for pricing attributes
        ("usd>10", BinaryOperatorNode(AttributeNode("usd"), ">", NumericValueNode(10))),
        ("eur<=5", BinaryOperatorNode(AttributeNode("eur"), "<=", NumericValueNode(5))),
        ("tix<1", BinaryOperatorNode(AttributeNode("tix"), "<", NumericValueNode(1))),
        ("usd=2.5", BinaryOperatorNode(AttributeNode("usd"), "=", NumericValueNode(2.5))),
        ("eur!=10", BinaryOperatorNode(AttributeNode("eur"), "!=", NumericValueNode(10))),
        ("tix>=0.5", BinaryOperatorNode(AttributeNode("tix"), ">=", NumericValueNode(0.5))),
    ],
)
def test_parse_to_nodes(test_input: str, expected_ast: QueryNode) -> None:
    """Test that queries parse into the expected AST structure."""
    observed = parsing.parse_search_query(test_input).root

    # Compare the full AST structure
    assert observed == expected_ast, f"\nExpected: {expected_ast}\nObserved: {observed}"


def test_parse_simple_condition() -> None:
    """Test parsing a simple condition."""
    query = "cmc:2"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("cmc"), ":", NumericValueNode(2))
    assert result.root == expected


def test_parse_and_operation() -> None:
    """Test parsing AND operations."""
    query = "a AND b"
    result = parsing.parse_search_query(query).root

    assert result == AndNode(
        [
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("a")),
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("b")),
        ],
    )


def test_parse_or_operation() -> None:
    """Test parsing OR operations."""
    query = "a OR b"
    result = parsing.parse_search_query(query).root
    assert result == OrNode(
        [
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("a")),
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("b")),
        ],
    )


def test_parse_implicit_and() -> None:
    """Test parsing implicit AND operations."""
    query = "a b"
    result = parsing.parse_search_query(query).root
    assert result == AndNode(
        [
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("a")),
            BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("b")),
        ],
    )


def test_parse_complex_nested() -> None:
    """Test parsing complex nested queries."""
    query = "cmc:2 AND (oracle:flying OR oracle:haste)"
    result = parsing.parse_search_query(query)

    assert isinstance(result, parsing.Query)
    assert isinstance(result.root, AndNode)
    assert len(result.root.operands) == 2
    # The right side should be an OR operation
    assert isinstance(result.root.operands[1], OrNode)


def test_parse_quoted_strings() -> None:
    """Test parsing quoted strings."""
    query = 'name:"Lightning Bolt"'
    observed_ast = parsing.parse_search_query(query)
    expected_ast = BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("Lightning Bolt"))
    assert observed_ast.root == expected_ast


def test_parse_set_searches() -> None:
    """Test parsing set search queries."""
    # Test full 'set:' syntax
    query = "set:iko"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("set"), ":", StringValueNode("iko"))
    assert result.root == expected

    # Test 's:' shorthand
    query = "s:thb"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("s"), ":", StringValueNode("thb"))
    assert result.root == expected

    # Test case insensitivity
    query = "SET:m21"
    result = parsing.parse_search_query(query)
    expected = BinaryOperatorNode(AttributeNode("SET"), ":", StringValueNode("m21"))
    assert result.root == expected


def test_parse_different_operators() -> None:
    """Test parsing different comparison operators."""
    operators = [">", "<", ">=", "<=", "=", "!="]

    for op in operators:
        query = f"cmc{op}3"
        result = parsing.parse_search_query(query)
        expected = BinaryOperatorNode(AttributeNode("cmc"), op, NumericValueNode(3))
        assert result.root == expected


def _generate_pricing_operator_test_cases() -> list[tuple[str, BinaryOperatorNode]]:
    """Generate test cases for pricing operators."""
    operators = [">", "<", ">=", "<=", "=", "!="]
    pricing_attrs = ["usd", "eur", "tix"]

    test_cases = []
    for attr in pricing_attrs:
        for op in operators:
            query = f"{attr}{op}5"
            expected = BinaryOperatorNode(AttributeNode(attr), op, NumericValueNode(5))
            test_cases.append((query, expected))

    return test_cases


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=_generate_pricing_operator_test_cases(),
)
def test_parse_pricing_operators(test_input: str, expected_ast: BinaryOperatorNode) -> None:
    """Test parsing different comparison operators with pricing attributes."""
    result = parsing.parse_search_query(test_input)
    assert result.root == expected_ast, f"Failed for {test_input}"


def test_parse_combined_pricing_queries() -> None:
    """Test parsing combined queries with pricing attributes."""
    # Test combining pricing with other attributes
    query1 = "cmc<=3 usd<5"
    result1 = parsing.parse_search_query(query1)
    expected1 = AndNode([
        BinaryOperatorNode(AttributeNode("cmc"), "<=", NumericValueNode(3)),
        BinaryOperatorNode(AttributeNode("usd"), "<", NumericValueNode(5)),
    ])
    assert result1.root == expected1

    # Test combining multiple pricing attributes
    query2 = "usd>10 OR eur<5"
    result2 = parsing.parse_search_query(query2)
    expected2 = OrNode([
        BinaryOperatorNode(AttributeNode("usd"), ">", NumericValueNode(10)),
        BinaryOperatorNode(AttributeNode("eur"), "<", NumericValueNode(5)),
    ])
    assert result2.root == expected2


def test_parse_empty_query() -> None:
    """Test parsing empty or None queries."""
    # Empty string
    result = parsing.parse_search_query("")
    assert isinstance(result, parsing.Query)

    # None
    result = parsing.parse_search_query(None)
    assert isinstance(result, parsing.Query)


def test_name_vs_name_attribute() -> None:
    """Test that we can distinguish between the string 'name' and card names."""
    # This should create a BinaryOperatorNode for "name" (searching for cards with "name" in their name)
    query1 = "name"
    result1 = parsing.parse_search_query(query1)
    expected = BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("name"))
    assert result1.root == expected

    # This should create a BinaryOperatorNode for name:value (same as bare word "value")
    query2 = "name:value"
    result2 = parsing.parse_search_query(query2)
    expected = BinaryOperatorNode(AttributeNode("name"), ":", StringValueNode("value"))
    assert result2.root == expected

    # This should create a BinaryOperatorNode for cmc operations
    query3 = "cmc:3"
    result3 = parsing.parse_search_query(query3)
    expected = BinaryOperatorNode(AttributeNode("cmc"), ":", NumericValueNode(3))
    assert result3.root == expected

    # This should create a BinaryOperatorNode for other attributes
    query4 = "oracle:flying"
    result4 = parsing.parse_search_query(query4)
    expected = BinaryOperatorNode(AttributeNode("oracle"), ":", StringValueNode("flying"))
    assert result4.root == expected


@pytest.mark.parametrize(
    argnames="operator",
    argvalues=["AND", "OR"],
)
def test_nary_operator_associativity(operator: str) -> None:
    """Test that AND operator associativity now creates the same AST structure."""
    # These should now create the same AST structure with n-ary operations
    query1 = f"a {operator} (b {operator} c)"
    query2 = f"(a {operator} b) {operator} c"

    result1 = parsing.parse_search_query(query1)
    result2 = parsing.parse_search_query(query2)

    # With n-ary operations, both should now create the same AST structure
    # Both should be: AndNode([a, b, c])
    assert result1 == result2

