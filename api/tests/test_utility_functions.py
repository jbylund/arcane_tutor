"""Tests for utility functions in api_resource module."""

from __future__ import annotations

from api.api_resource import get_where_clause, rewrap
from api.utils.error_monitoring import can_serialize


def test_can_serialize_valid_objects() -> None:
    """Test can_serialize with valid serializable objects."""
    # Test with simple types
    assert can_serialize("string") is True
    assert can_serialize(123) is True
    assert can_serialize(123.45) is True
    assert can_serialize(True) is True
    assert can_serialize(None) is True

    # Test with collections
    assert can_serialize([1, 2, 3]) is True
    assert can_serialize({"key": "value"}) is True
    assert can_serialize({"nested": {"data": [1, 2, 3]}}) is True


def test_can_serialize_invalid_objects() -> None:
    """Test can_serialize with non-serializable objects."""
    # Test with non-serializable objects
    assert can_serialize(object()) is False
    assert can_serialize(lambda x: x) is False
    assert can_serialize({1, 2, 3}) is False


def test_can_serialize_large_objects() -> None:
    """Test can_serialize with objects that exceed size limit."""
    # Create a large string that exceeds the 16KB limit
    large_string = "x" * 20000
    assert can_serialize(large_string) is False


def test_rewrap_normalizes_whitespace() -> None:
    """Test rewrap function normalizes whitespace in SQL queries."""
    # Test with various whitespace patterns
    assert rewrap("SELECT * FROM table") == "SELECT * FROM table"
    assert rewrap("  SELECT   *   FROM   table  ") == "SELECT * FROM table"
    assert rewrap("SELECT\n*\nFROM\ntable") == "SELECT * FROM table"
    assert rewrap("SELECT\t*\tFROM\ttable") == "SELECT * FROM table"
    assert rewrap("  \n  SELECT  \n  *  \n  FROM  \n  table  \n  ") == "SELECT * FROM table"


def test_get_where_clause_caching() -> None:
    """Test that get_where_clause uses caching."""
    # This tests the @cached decorator functionality
    query = "cmc:3"
    result1 = get_where_clause(query)
    result2 = get_where_clause(query)

    # Results should be identical (cached)
    assert result1 == result2
