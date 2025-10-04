"""Tests for SQL generation of regex searches."""

from __future__ import annotations

from typing import Any

import pytest

from api.parsing.nodes import Query
from api.parsing.parsing_f import parse_scryfall_query


class TestRegexSQLGeneration:
    """Test that regex searches generate correct SQL with PostgreSQL regex operators."""

    @pytest.mark.parametrize(
        ("query", "expected_pattern"),
        [
            ("regex:/test/", "test"),
            ("re:/test/", "test"),
            ("regex:/^destroy/", "^destroy"),
            ("re:/target$/", "target$"),
            ("regex:/\\d+/", "\\d+"),
            ("regex:/flying|vigilance/", "flying|vigilance"),
        ],
    )
    def test_regex_generates_postgres_regex_operator(self, query: str, expected_pattern: str) -> None:
        """Test that regex searches generate SQL with PostgreSQL's ~* operator."""
        result = parse_scryfall_query(query)
        assert isinstance(result, Query)

        context: dict[str, Any] = {}
        sql = result.to_sql(context)

        # Should use PostgreSQL's case-insensitive regex operator ~*
        assert "~*" in sql
        assert "card.oracle_text" in sql

        # Context should contain the regex pattern
        assert len(context) == 1
        param_value = next(iter(context.values()))
        assert param_value == expected_pattern

    def test_regex_does_not_use_ilike(self) -> None:
        """Test that regex searches use ~* operator, not ILIKE pattern matching."""
        result = parse_scryfall_query("regex:/test/")
        assert isinstance(result, Query)

        context: dict[str, Any] = {}
        sql = result.to_sql(context)

        # Should NOT use ILIKE
        assert "ILIKE" not in sql
        # Should use regex operator
        assert "~*" in sql

        # Context should contain the exact pattern, not a wildcard pattern
        param_value = next(iter(context.values()))
        assert "%" not in param_value  # No SQL wildcards
        assert param_value == "test"

    def test_regex_with_complex_pattern_sql(self) -> None:
        """Test SQL generation for complex regex patterns."""
        result = parse_scryfall_query("regex:/^tap.*creature$/")
        assert isinstance(result, Query)

        context: dict[str, Any] = {}
        sql = result.to_sql(context)

        assert "card.oracle_text ~*" in sql
        param_value = next(iter(context.values()))
        assert param_value == "^tap.*creature$"

    def test_regex_combined_with_other_attributes_sql(self) -> None:
        """Test that regex combined with other attributes generates correct SQL."""
        result = parse_scryfall_query("regex:/flying/ cmc=3")
        assert isinstance(result, Query)

        context: dict[str, Any] = {}
        sql = result.to_sql(context)

        # Should have both conditions with AND
        assert "card.oracle_text ~*" in sql
        assert "card.cmc" in sql
        assert "AND" in sql

        # Context should have two values
        assert len(context) == 2

    def test_regex_with_or_condition_sql(self) -> None:
        """Test regex with OR generates proper SQL."""
        result = parse_scryfall_query("regex:/flying/ OR power>5")
        assert isinstance(result, Query)

        context: dict[str, Any] = {}
        sql = result.to_sql(context)

        assert "card.oracle_text ~*" in sql
        assert "card.creature_power" in sql
        assert "OR" in sql

    def test_regex_negation_sql(self) -> None:
        """Test negated regex generates proper SQL."""
        result = parse_scryfall_query("-regex:/flying/")
        assert isinstance(result, Query)

        context: dict[str, Any] = {}
        sql = result.to_sql(context)

        assert "NOT" in sql
        assert "card.oracle_text ~*" in sql

    def test_regex_comparison_with_text_search(self) -> None:
        """Test that regex search differs from regular text search in SQL generation."""
        # Regular text search
        text_result = parse_scryfall_query("oracle:flying")
        text_context: dict[str, Any] = {}
        text_sql = text_result.to_sql(text_context)

        # Regex search
        regex_result = parse_scryfall_query("regex:/flying/")
        regex_context: dict[str, Any] = {}
        regex_sql = regex_result.to_sql(regex_context)

        # Text search should use ILIKE with wildcards
        assert "ILIKE" in text_sql
        text_param = next(iter(text_context.values()))
        assert "%" in text_param

        # Regex search should use ~* without wildcards
        assert "~*" in regex_sql
        regex_param = next(iter(regex_context.values()))
        assert "%" not in regex_param


if __name__ == "__main__":
    pytest.main([__file__])
