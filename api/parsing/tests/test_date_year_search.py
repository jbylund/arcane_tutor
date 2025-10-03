"""Tests for date and year search functionality."""

import itertools

import pytest

from api import parsing
from api.parsing import AttributeNode, BinaryOperatorNode, StringValueNode


@pytest.mark.parametrize(
    argnames=("searchattr", "searchoperator", "searchvalue"),
    argvalues=list(
        itertools.product(
            ["date", "year"],
            [":", "=", ">", "<", ">=", "<="],
            ["2025-02-02", "2025"],
        ),
    ),
)
def test_date_year_search_parsing(searchattr: str, searchoperator: str, searchvalue: str) -> None:
    """Test that date and year searches parse correctly with all operators."""
    query_str = f"{searchattr}{searchoperator}{searchvalue}"
    parsed = parsing.parse_scryfall_query(query_str)

    # Should parse to a BinaryOperatorNode
    assert isinstance(parsed.root, BinaryOperatorNode)
    assert isinstance(parsed.root.lhs, AttributeNode)
    assert parsed.root.operator == searchoperator

    # RHS should be a StringValueNode with the search value
    assert isinstance(parsed.root.rhs, StringValueNode)
    assert parsed.root.rhs.value == searchvalue


@pytest.mark.parametrize(
    argnames=("query", "expected_sql_fragment"),
    argvalues=[
        # Date searches should use the full date
        ("date:2025-02-02", "card.released_at = "),
        ("date=2025-02-02", "card.released_at = "),
        ("date>2025-02-02", "card.released_at > "),
        ("date<2025-02-02", "card.released_at < "),
        ("date>=2025-02-02", "card.released_at >= "),
        ("date<=2025-02-02", "card.released_at <= "),
        # Year searches should use date ranges for index usage
        ("year:2025", "<= card.released_at AND card.released_at <"),
        ("year=2025", "<= card.released_at AND card.released_at <"),
        ("year>2025", "card.released_at >="),
        ("year<2025", "card.released_at <"),
        ("year>=2025", "card.released_at >="),
        ("year<=2025", "card.released_at <"),
    ],
)
def test_date_year_sql_generation(query: str, expected_sql_fragment: str) -> None:
    """Test that date and year searches generate correct SQL."""
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Check that the SQL contains the expected fragment
    assert expected_sql_fragment in sql
    # Check that parameters were added to context (1 for date, 1 or 2 for year)
    assert len(context) >= 1


def test_date_search_full_date() -> None:
    """Test date search with full date format."""
    parsed = parsing.parse_scryfall_query("date:2025-02-02")
    context = {}
    sql = parsed.to_sql(context)

    assert "card.released_at = " in sql
    # Should have a parameter with the date string
    assert "2025-02-02" in context.values()


def test_year_search_numeric() -> None:
    """Test year search with numeric year."""
    parsed = parsing.parse_scryfall_query("year:2025")
    context = {}
    sql = parsed.to_sql(context)

    # Year search should convert to date range: 2025-01-01 <= released_at < 2026-01-01
    assert "card.released_at" in sql
    assert "<= card.released_at AND card.released_at <" in sql
    # Should have parameters with date strings
    assert "2025-01-01" in context.values()
    assert "2026-01-01" in context.values()


def test_year_search_with_date_format() -> None:
    """Test year search with date format (should extract year)."""
    parsed = parsing.parse_scryfall_query("year:2025-02-02")
    context = {}
    sql = parsed.to_sql(context)

    # Should extract year 2025 and convert to date range
    assert "card.released_at" in sql
    assert "<= card.released_at AND card.released_at <" in sql
    assert "2025-01-01" in context.values()
    assert "2026-01-01" in context.values()


def test_date_year_combined_query() -> None:
    """Test combining date/year searches with other conditions."""
    parsed = parsing.parse_scryfall_query("year:2025 AND cmc=3")
    context = {}
    sql = parsed.to_sql(context)

    assert "card.released_at" in sql
    assert "card.cmc = " in sql
    assert "2025-01-01" in context.values()
    assert 3 in context.values()
