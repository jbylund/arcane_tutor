"""Tests for user-error Postgres exceptions in the SQL search path.

Two things the parser accepts but Postgres rejects have to come back as HTTPBadRequest rather than
leaking as a 500: standalone arithmetic expressions like "cmc+1" (DatatypeMismatch), and invalid
regex patterns like /^[/ (InvalidRegularExpression). The handling lives in _search_sql, so the tests
call it directly (routing between the engine and SQL paths is covered in test_parsing_errors.py).
"""

from __future__ import annotations

import multiprocessing
import time
from unittest.mock import patch

import falcon
import psycopg.errors
import pytest
from psycopg.pq import DiagnosticField

from api.api_resource import APIResource, regex_error_reason
from api.tests.helpers import search_kwargs


class TestDatatypeMismatchHandling:
    """Test handling of DatatypeMismatch errors in the SQL search path."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource(
            last_import_time=multiprocessing.Value("d", time.time(), lock=True),
        )

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        if hasattr(self, "api_resource") and self.api_resource:
            # Close the connection pool to prevent thread pool warnings
            self.api_resource._conn_pool.close()

    def test_search_sql_handles_datatype_mismatch(self) -> None:
        """Test that DatatypeMismatch in _search_sql raises HTTPBadRequest."""
        # Mock the _run_query method to raise DatatypeMismatch
        with (
            patch.object(self.api_resource, "_run_query") as mock_run_query,
        ):
            mock_run_query.side_effect = psycopg.errors.DatatypeMismatch(
                'column "cmc" must appear in the GROUP BY clause or be used in an aggregate function',
            )

            # Call _search_sql with a problematic query and expect HTTPBadRequest
            with pytest.raises(falcon.HTTPBadRequest) as exc_info:
                self.api_resource._search_sql(**search_kwargs("cmc+1"))

            # Verify the error details
            assert exc_info.value.title == "Invalid Search Query"
            assert "cmc+1" in exc_info.value.description
            assert "invalid syntax" in exc_info.value.description.lower()

    def test_search_sql_handles_datatype_mismatch_main_query_only(self) -> None:
        """Test that DatatypeMismatch is only caught on the main query."""
        # Mock _run_query to fail on first call (main query)
        with (
            patch.object(self.api_resource, "_run_query") as mock_run_query,
        ):
            mock_run_query.side_effect = psycopg.errors.DatatypeMismatch(
                "WHERE clause must be type boolean, not type integer",
            )

            # Call _search_sql with a problematic query and expect HTTPBadRequest
            with pytest.raises(falcon.HTTPBadRequest) as exc_info:
                self.api_resource._search_sql(**search_kwargs("cmc+1", limit=100))

            # Verify the error details and that only one query was attempted
            assert exc_info.value.title == "Invalid Search Query"
            assert "cmc+1" in exc_info.value.description
            assert mock_run_query.call_count == 1

    def test_search_sql_normal_operation_unaffected(self) -> None:
        """Test that normal queries still work correctly."""
        # Mock successful query execution
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.return_value = {
                "result": [
                    {"name": "Lightning Bolt", "total_cards_count": None},
                    {"total_cards_count": 1},
                ],
                "timings": {},
            }

            result = self.api_resource._search_sql(**search_kwargs("name:bolt"))

            # Verify normal operation
            assert len(result["cards"]) == 1
            assert result["cards"][0]["name"] == "Lightning Bolt"
            assert result["total_cards"] == 1
            assert result["query"] == "name:bolt"


class TestInvalidRegularExpressionHandling:
    """Test handling of InvalidRegularExpression errors in the SQL search path."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource(
            last_import_time=multiprocessing.Value("d", time.time(), lock=True),
        )

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        if hasattr(self, "api_resource") and self.api_resource:
            self.api_resource._conn_pool.close()

    def test_search_sql_handles_invalid_regular_expression(self) -> None:
        """An unparseable regex is the user's error, so it must be a 400 and not a 500.

        Typeahead balances a half-typed regex into a complete one on every keystroke, so `o:/^[/` is
        an ordinary intermediate state on the way to `o:/^[abc]/` — not something to alert on.
        """
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            # `info=` populates .diag.message_primary the way a real connection would — the plain
            # constructor string does not, so a test built on it can't tell whether
            # `err.diag.message_primary` is actually wired through to the user-facing description.
            mock_run_query.side_effect = psycopg.errors.InvalidRegularExpression(
                "invalid regular expression: brackets [] not balanced",
                info={DiagnosticField.MESSAGE_PRIMARY: b"invalid regular expression: brackets [] not balanced"},
            )

            with pytest.raises(falcon.HTTPBadRequest) as exc_info:
                self.api_resource._search_sql(**search_kwargs("o:/^[/"))

            assert exc_info.value.title == "Invalid Search Query"
            assert "o:/^[/" in exc_info.value.description
            # The Postgres prefix must be stripped exactly once, not left in twice.
            assert "brackets [] not balanced" in exc_info.value.description
            assert exc_info.value.description.count("invalid regular expression") == 1
            assert mock_run_query.call_count == 1


@pytest.mark.parametrize(
    argnames=["message_primary", "expected_reason"],
    argvalues=[
        # Verbatim from Postgres 18 for `'abc' ~ '^['`; the prefix must not survive into a message
        # that already says "invalid regular expression".
        ("invalid regular expression: brackets [] not balanced", "brackets [] not balanced"),
        ("invalid regular expression: quantifier operand invalid", "quantifier operand invalid"),
        # A synthesized error carries no diagnostic to quote.
        (None, "the pattern could not be parsed"),
        ("", "the pattern could not be parsed"),
        ("invalid regular expression: ", "the pattern could not be parsed"),
    ],
    ids=["brackets", "quantifier", "no_diag", "empty_diag", "prefix_only"],
)
def test_regex_error_reason(message_primary: str | None, expected_reason: str) -> None:
    """The reason quoted back to the user drops Postgres's redundant prefix."""
    assert regex_error_reason(message_primary) == expected_reason


if __name__ == "__main__":
    pytest.main([__file__])
