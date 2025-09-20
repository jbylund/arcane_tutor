"""Integration tests for collector number search functionality."""

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.parsing_f import generate_sql_query


class TestCollectorNumberIntegration:
    """Test collector number search functionality with end-to-end integration."""

    def test_number_search_integration(self) -> None:
        """Test that number search generates correct SQL end-to-end."""
        query = "number:123"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate ILIKE query for text matching
        assert "card.collector_number ILIKE" in sql
        assert len(params) == 1

        # Parameter should contain wildcard pattern for ILIKE
        param_value = next(iter(params.values()))
        assert param_value == "%123%"

    def test_cn_search_integration(self) -> None:
        """Test that cn alias generates correct SQL end-to-end."""
        query = "cn:45a"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate ILIKE query for text matching
        assert "card.collector_number ILIKE" in sql
        assert len(params) == 1

        # Parameter should contain wildcard pattern for ILIKE
        param_value = next(iter(params.values()))
        assert param_value == "%45a%"

    def test_quoted_collector_number_integration(self) -> None:
        """Test that quoted collector numbers work correctly."""
        query = 'number:"100"'
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate ILIKE query for text matching
        assert "card.collector_number ILIKE" in sql
        assert len(params) == 1

        # Parameter should contain wildcard pattern for ILIKE
        param_value = next(iter(params.values()))
        assert param_value == "%100%"

    @pytest.mark.parametrize(
        argnames=("query", "expected_pattern"),
        argvalues=[
            ("number:1", "%1%"),
            ("cn:123", "%123%"),
            ("number:45a", "%45a%"),
            ("cn:100b", "%100b%"),
            ("NUMBER:999", "%999%"),
            ("CN:1a", "%1a%"),
        ],
    )
    def test_various_collector_numbers_integration(self, query: str, expected_pattern: str) -> None:
        """Test various collector number formats generate correct SQL."""
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate ILIKE query for text matching
        assert "card.collector_number ILIKE" in sql
        assert len(params) == 1

        # Parameter should match expected pattern
        param_value = next(iter(params.values()))
        assert param_value == expected_pattern

    def test_combined_collector_number_search_integration(self) -> None:
        """Test that combined queries with collector numbers work correctly."""
        query = "number:123 set:dom"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate queries for both collector_number and set
        assert "card.collector_number ILIKE" in sql
        assert "card.card_set_code =" in sql
        assert len(params) == 2

        # Should contain parameters for both conditions
        param_values = set(params.values())
        assert "%123%" in param_values
        assert "dom" in param_values
