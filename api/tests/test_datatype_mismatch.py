"""Tests for DatatypeMismatch error handling in search queries.

This module tests that standalone arithmetic expressions in queries
(like "cmc+1") are handled gracefully by returning empty results
instead of throwing DatatypeMismatch exceptions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import psycopg.errors
import pytest

from api.api_resource import APIResource


class TestDatatypeMismatchHandling:
    """Test handling of DatatypeMismatch errors in search functionality."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource()

    def test_search_handles_datatype_mismatch(self) -> None:
        """Test that DatatypeMismatch in search returns empty result."""
        # Mock the _run_query method to raise DatatypeMismatch
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = psycopg.errors.DatatypeMismatch('column "cmc" must appear in the GROUP BY clause or be used in an aggregate function')

            # Call _search with a problematic query
            result = self.api_resource._search(query="cmc+1")

            # Verify we get an empty result instead of an exception
            assert result["cards"] == []
            assert result["total_cards"] == 0
            assert result["query"] == "cmc+1"
            assert "result" in result
            assert result["result"]["result"] == []

    def test_search_handles_datatype_mismatch_in_count_query(self) -> None:
        """Test that DatatypeMismatch in count query also returns empty result."""

        # Mock _run_query to succeed on first call (main query) but fail on count
        def mock_run_query_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            query = kwargs.get("query", args[0] if args else "")
            if "COUNT(1)" in query:
                msg = "datatype mismatch in count query"
                raise psycopg.errors.DatatypeMismatch(msg)
            return {
                "result": [{"name": f"card_{i}"} for i in range(100)],  # Simulate hitting limit
                "timings": {},
            }

        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = mock_run_query_side_effect

            # Call _search - this should trigger the count query due to hitting limit
            result = self.api_resource._search(query="cmc+1", limit=100)

            # Verify the count fallback worked
            assert len(result["cards"]) == 100  # Got the main results
            assert result["total_cards"] == 0   # Count query failed, defaulted to 0

    def test_search_normal_operation_unaffected(self) -> None:
        """Test that normal queries still work correctly."""
        # Mock successful query execution
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.return_value = {
                "result": [{"name": "Lightning Bolt"}],
                "timings": {},
            }

            result = self.api_resource._search(query="name:bolt")

            # Verify normal operation
            assert len(result["cards"]) == 1
            assert result["cards"][0]["name"] == "Lightning Bolt"
            assert result["total_cards"] == 1
            assert result["query"] == "name:bolt"


if __name__ == "__main__":
    pytest.main([__file__])
