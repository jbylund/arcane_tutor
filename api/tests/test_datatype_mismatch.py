"""Tests for DatatypeMismatch error handling in search queries.

This module tests that standalone arithmetic expressions in queries
(like "cmc+1") are handled gracefully by returning empty results
instead of throwing DatatypeMismatch exceptions.
"""

from __future__ import annotations

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

    def test_search_handles_datatype_mismatch_main_query_only(self) -> None:
        """Test that DatatypeMismatch is only caught on the main query."""
        # Mock _run_query to fail on first call (main query)
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = psycopg.errors.DatatypeMismatch(
                "WHERE clause must be type boolean, not type integer",
            )

            # Call _search with a problematic query
            result = self.api_resource._search(query="cmc+1", limit=100)

            # Verify we get an empty result and the function returns early
            assert result["cards"] == []
            assert result["total_cards"] == 0
            assert result["query"] == "cmc+1"

            # Verify the main query was called only once (early return on error)
            assert mock_run_query.call_count == 1

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
