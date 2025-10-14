"""Tests for layout and border parsing functionality."""

from __future__ import annotations

from typing import Any

import pytest

from api.parsing.nodes import AndNode, Query
from api.parsing.parsing_f import parse_scryfall_query
from api.parsing.scryfall_nodes import ScryfallAttributeNode, ScryfallBinaryOperatorNode


class TestLayoutBorderParsing:
    """Test parsing of layout and border search queries."""

    @pytest.mark.parametrize(("query", "expected_attr", "expected_value"), [
        ("layout:normal", "card_layout", "normal"),
        ("layout:split", "card_layout", "split"),
        ("layout:flip", "card_layout", "flip"),
        ("layout:transform", "card_layout", "transform"),
        ("layout:double_faced_token", "card_layout", "double_faced_token"),
        ("layout:meld", "card_layout", "meld"),
        ("layout:leveler", "card_layout", "leveler"),
        ("layout:saga", "card_layout", "saga"),
        ("layout:adventure", "card_layout", "adventure"),
        ("layout:planar", "card_layout", "planar"),
        ("layout:scheme", "card_layout", "scheme"),
        ("layout:vanguard", "card_layout", "vanguard"),
        ("layout:token", "card_layout", "token"),
        ("layout:emblem", "card_layout", "emblem"),
        ("border:black", "print_border", "black"),
        ("border:white", "print_border", "white"),
        ("border:borderless", "print_border", "borderless"),
        ("border:silver", "print_border", "silver"),
        ("border:gold", "print_border", "gold"),
    ])
    def test_parse_layout_and_border_queries(self, query: str, expected_attr: str, expected_value: str) -> None:
        """Test parsing of layout and border search queries."""
        result = parse_scryfall_query(query)

        assert isinstance(result, Query)
        binary_op = result.root
        assert isinstance(binary_op, ScryfallBinaryOperatorNode)
        assert isinstance(binary_op.lhs, ScryfallAttributeNode)
        assert binary_op.lhs.attribute_name == expected_attr
        assert binary_op.operator == ":"
        assert binary_op.rhs.value == expected_value

    def test_parse_combined_layout_border_query(self) -> None:
        """Test parsing combined layout and border queries."""
        query = "layout:split border:black"
        result = parse_scryfall_query(query)

        assert isinstance(result, Query)
        # Should be an AND operation between two binary operator nodes
        and_node = result.root
        assert isinstance(and_node, AndNode)

        # Extract the two binary operator nodes from the AND
        conditions = and_node.operands
        assert len(conditions) == 2

        # Check that we have both layout and border conditions
        attributes = {cond.lhs.attribute_name for cond in conditions}
        assert attributes == {"card_layout", "print_border"}

        values = {cond.rhs.value for cond in conditions}
        assert values == {"split", "black"}

    def test_parse_layout_with_quotes(self) -> None:
        """Test parsing layout searches with quoted values."""
        query = 'layout:"double_faced_token"'
        result = parse_scryfall_query(query)

        assert isinstance(result, Query)
        binary_op = result.root
        assert isinstance(binary_op, ScryfallBinaryOperatorNode)
        assert binary_op.lhs.attribute_name == "card_layout"
        assert binary_op.rhs.value == "double_faced_token"

    def test_parse_border_with_quotes(self) -> None:
        """Test parsing border searches with quoted values."""
        query = 'border:"borderless"'
        result = parse_scryfall_query(query)

        assert isinstance(result, Query)
        binary_op = result.root
        assert isinstance(binary_op, ScryfallBinaryOperatorNode)
        assert binary_op.lhs.attribute_name == "print_border"
        assert binary_op.rhs.value == "borderless"

    def test_parse_complex_query_with_layout_border(self) -> None:
        """Test parsing complex queries that include layout and border."""
        query = "layout:normal border:black cmc=3"
        result = parse_scryfall_query(query)

        assert isinstance(result, Query)
        # Should be nested AND operations
        # The exact structure may vary depending on parsing precedence,
        # but we should have all three conditions
        def extract_attributes(node: Any) -> list[tuple[str, Any]]:
            """Recursively extract all attribute nodes from a parse tree."""
            if isinstance(node, ScryfallBinaryOperatorNode) and hasattr(node.lhs, "attribute_name"):
                return [(node.lhs.attribute_name, node.rhs.value)]
            if isinstance(node, AndNode):
                attrs = []
                for child in node.operands:
                    attrs.extend(extract_attributes(child))
                return attrs
            return []

        attributes = extract_attributes(result.root)
        expected_attrs = [("card_layout", "normal"), ("print_border", "black"), ("face_cmc", 3)]

        # Sort both lists to compare regardless of order
        attributes.sort()
        expected_attrs.sort()
        assert attributes == expected_attrs


if __name__ == "__main__":
    pytest.main([__file__])
