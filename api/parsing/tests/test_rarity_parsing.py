"""Tests for rarity search parsing functionality."""

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.nodes import BinaryOperatorNode, StringValueNode
from api.parsing.parsing_f import generate_sql_query
from api.parsing.scryfall_nodes import ScryfallAttributeNode


class TestRarityParsing:
    """Test parsing of rarity search queries."""

    def test_parse_rarity_colon_syntax(self) -> None:
        """Test parsing rarity with colon syntax."""
        query = parse_scryfall_query("r:rare")
        assert isinstance(query.root, BinaryOperatorNode)
        assert isinstance(query.root.lhs, ScryfallAttributeNode)
        assert query.root.lhs.attribute_name == "card_rarity_text"
        assert query.root.operator == ":"
        assert isinstance(query.root.rhs, StringValueNode)
        assert query.root.rhs.value == "rare"

    def test_parse_rarity_full_name_syntax(self) -> None:
        """Test parsing rarity with full name syntax."""
        query = parse_scryfall_query("rarity:uncommon")
        assert isinstance(query.root, BinaryOperatorNode)
        assert isinstance(query.root.lhs, ScryfallAttributeNode)
        assert query.root.lhs.attribute_name == "card_rarity_text"
        assert query.root.operator == ":"
        assert query.root.rhs.value == "uncommon"

    def test_parse_rarity_comparison_operators(self) -> None:
        """Test parsing rarity with comparison operators."""
        test_cases = [
            ("rarity>uncommon", ">"),
            ("rarity>=rare", ">="),
            ("rarity<mythic", "<"),
            ("rarity<=rare", "<="),
            ("rarity=common", "="),
            ("rarity!=rare", "!="),
        ]

        for query_str, expected_op in test_cases:
            query = parse_scryfall_query(query_str)
            assert isinstance(query.root, BinaryOperatorNode)
            assert query.root.operator == expected_op

    def test_parse_all_standard_rarities(self) -> None:
        """Test parsing all standard rarity values."""
        rarities = ["common", "uncommon", "rare", "mythic"]

        for rarity in rarities:
            query = parse_scryfall_query(f"r:{rarity}")
            assert isinstance(query.root, BinaryOperatorNode)
            assert query.root.rhs.value == rarity


class TestRaritySQL:
    """Test SQL generation for rarity queries."""

    def test_rarity_colon_uses_text_column(self) -> None:
        """Test that colon operator uses text column with case-insensitive match."""
        query = parse_scryfall_query("r:rare")
        sql, params = generate_sql_query(query)

        assert "card.card_rarity_text" in sql
        assert "LOWER(" in sql
        assert len(params) == 1
        assert "rare" in params.values()

    def test_rarity_comparison_uses_numeric_column(self) -> None:
        """Test that comparison operators use numeric column."""
        query = parse_scryfall_query("rarity>uncommon")
        sql, params = generate_sql_query(query)

        assert "card.card_rarity_numeric" in sql
        assert ">" in sql
        assert len(params) == 1
        # uncommon should map to 2
        assert 2 in params.values()

    def test_rarity_equality_uses_text_column(self) -> None:
        """Test that equality operator uses text column."""
        query = parse_scryfall_query("rarity=mythic")
        sql, params = generate_sql_query(query)

        assert "card.card_rarity_text" in sql
        assert "LOWER(" in sql
        assert "mythic" in params.values()

    def test_rarity_numeric_mappings(self) -> None:
        """Test that numeric mappings are correct for all standard rarities."""
        test_cases = [
            ("rarity>common", 1),    # common = 1
            ("rarity>=uncommon", 2), # uncommon = 2
            ("rarity<rare", 3),      # rare = 3
            ("rarity<=mythic", 4),   # mythic = 4
        ]

        for query_str, expected_num in test_cases:
            query = parse_scryfall_query(query_str)
            sql, params = generate_sql_query(query)
            assert "card.card_rarity_numeric" in sql
            assert expected_num in params.values()

    def test_rarity_case_insensitive_parsing(self) -> None:
        """Test that rarity parsing is case-insensitive."""
        test_cases = ["RARE", "Rare", "rArE", "rare"]

        for rarity_case in test_cases:
            query = parse_scryfall_query(f"r:{rarity_case}")
            _sql, params = generate_sql_query(query)
            # Should normalize to lowercase
            assert "rare" in params.values()

    def test_unknown_rarity_handling(self) -> None:
        """Test handling of unknown rarity values."""
        # Should not crash, but may return FALSE condition for comparisons
        query = parse_scryfall_query("rarity>unknown_rarity")
        sql, _params = generate_sql_query(query)

        # Should generate SQL that won't match anything
        assert "FALSE" in sql or "card.card_rarity_numeric" in sql

    def test_rarity_not_equal_operator(self) -> None:
        """Test != operator with rarity."""
        query = parse_scryfall_query("rarity!=common")
        sql, params = generate_sql_query(query)

        assert "card.card_rarity_text" in sql
        assert "!=" in sql
        assert "common" in params.values()


class TestRarityIntegration:
    """Integration tests for rarity functionality."""

    def test_rarity_with_other_conditions(self) -> None:
        """Test rarity combined with other search conditions."""
        query = parse_scryfall_query("rarity:rare cmc=3")
        sql, params = generate_sql_query(query)

        # Should contain both rarity and cmc conditions
        assert "card.card_rarity_text" in sql
        assert "card.cmc" in sql
        assert "rare" in params.values()
        assert 3 in params.values()

    def test_complex_rarity_query(self) -> None:
        """Test complex query with rarity comparisons."""
        query = parse_scryfall_query("(rarity>=rare OR rarity=uncommon) AND cmc<=4")
        sql, _params = generate_sql_query(query)

        # Should have both text and numeric rarity columns
        assert "card.card_rarity_numeric" in sql  # for >=
        assert "card.card_rarity_text" in sql     # for =
        assert "card.cmc" in sql


if __name__ == "__main__":
    pytest.main([__file__])
