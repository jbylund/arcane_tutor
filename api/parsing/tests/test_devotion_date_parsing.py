"""Tests for devotion and date parsing functionality."""

import pytest

from api import parsing
from api.parsing import (
    NumericValueNode,
    StringValueNode,
)
from api.parsing.scryfall_nodes import (
    ScryfallAttributeNode,
    ScryfallBinaryOperatorNode,
)


@pytest.mark.parametrize(
    argnames=("test_input", "expected_ast"),
    argvalues=[
        # Devotion tests with various operators
        ("devotionw=1", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), "=", NumericValueNode(1))),
        ("devotionw>=5", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">=", NumericValueNode(5))),
        ("devotionw>3", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">", NumericValueNode(3))),
        ("devotionw<=2", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), "<=", NumericValueNode(2))),
        ("devotionw<4", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), "<", NumericValueNode(4))),
        # Test all colors
        ("devotionu>=2", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">=", NumericValueNode(2))),
        ("devotionb>=2", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">=", NumericValueNode(2))),
        ("devotionr>=2", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">=", NumericValueNode(2))),
        ("devotiong>=2", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">=", NumericValueNode(2))),
        ("devotionc>=2", ScryfallBinaryOperatorNode(ScryfallAttributeNode("mana_cost_jsonb"), ">=", NumericValueNode(2))),
        # Date tests
        ("date=2020-01-01", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), "=", StringValueNode("2020-01-01"))),
        ("date>=2020-01-01", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), ">=", StringValueNode("2020-01-01"))),
        ("date>2020-01-01", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), ">", StringValueNode("2020-01-01"))),
        ("date<=2020-12-31", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), "<=", StringValueNode("2020-12-31"))),
        ("date<2020-12-31", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), "<", StringValueNode("2020-12-31"))),
        # Year tests - year is parsed as a string first but will be converted to int in SQL generation
        ("year=2020", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), "=", StringValueNode("2020"))),
        ("year>=2020", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), ">=", StringValueNode("2020"))),
        ("year>2020", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), ">", StringValueNode("2020"))),
        ("year<=2020", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), "<=", StringValueNode("2020"))),
        ("year<2020", ScryfallBinaryOperatorNode(ScryfallAttributeNode("released_at"), "<", StringValueNode("2020"))),
    ],
)
def test_devotion_date_parsing(test_input: str, expected_ast: ScryfallBinaryOperatorNode) -> None:
    """Test that devotion and date queries parse into the expected AST structure."""
    observed = parsing.parse_scryfall_query(test_input).root

    # Compare the full AST structure
    assert observed == expected_ast, f"\nExpected: {expected_ast}\nObserved: {observed}"


def test_devotion_parsing_colors() -> None:
    """Test devotion parsing for all colors."""
    colors = ["w", "u", "b", "r", "g", "c"]

    for color in colors:
        query = f"devotion{color}>=3"
        result = parsing.parse_scryfall_query(query)
        assert result is not None
        assert result.root.lhs.attribute_name == "mana_cost_jsonb"
        assert result.root.operator == ">="
        assert result.root.rhs.value == 3





def test_date_parsing_formats() -> None:
    """Test date parsing with various date formats."""
    test_cases = [
        "date=2020-01-01",
        "date>=2020-01-01",
        "date>2020-01-01",
        "date<=2020-12-31",
        "date<2020-12-31",
    ]

    for query in test_cases:
        result = parsing.parse_scryfall_query(query)
        assert result is not None
        assert result.root.lhs.attribute_name == "released_at"


def test_year_parsing() -> None:
    """Test year parsing with various operators."""
    test_cases = [
        ("year=2020", "=", "2020"),
        ("year>=2020", ">=", "2020"),
        ("year>2020", ">", "2020"),
        ("year<=2020", "<=", "2020"),
        ("year<2020", "<", "2020"),
    ]

    for query, operator, year_val in test_cases:
        result = parsing.parse_scryfall_query(query)
        assert result is not None
        assert result.root.lhs.attribute_name == "released_at"
        assert result.root.operator == operator
        assert result.root.rhs.value == year_val
