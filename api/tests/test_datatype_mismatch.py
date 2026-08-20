"""Tests for user-error Postgres exceptions in the SQL search path.

Things the parser accepts but Postgres rejects have to come back as HTTPBadRequest rather than
leaking as a 500: standalone arithmetic expressions like "cmc+1" (DatatypeMismatch), invalid regex
patterns like /^[/ (InvalidRegularExpression), and any other class-22 "data exception" a
syntactically-valid-but-semantically-bad query can raise — division by zero from "power/0>1"
(DivisionByZero) being the motivating case (#948), but the handler covers the whole class, not just
that one member. The handling lives in _search_sql, so the tests call it directly (routing between
the engine and SQL paths is covered in test_parsing_errors.py).
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
        message = 'column "cmc" must appear in the GROUP BY clause or be used in an aggregate function'
        # `info=` populates .diag.message_primary the way a real connection would — the plain
        # constructor string does not (see TestInvalidRegularExpressionHandling below for the same note).
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = psycopg.errors.DatatypeMismatch(
                message,
                info={DiagnosticField.MESSAGE_PRIMARY: message.encode()},
            )

            with pytest.raises(falcon.HTTPBadRequest) as exc_info:
                self.api_resource._search_sql(**search_kwargs("cmc+1"))

            assert exc_info.value.title == "Invalid Search Query"
            assert "cmc+1" in exc_info.value.description
            assert message in exc_info.value.description

    def test_search_sql_handles_datatype_mismatch_main_query_only(self) -> None:
        """Test that DatatypeMismatch is only caught on the main query."""
        message = "WHERE clause must be type boolean, not type integer"
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = psycopg.errors.DatatypeMismatch(
                message,
                info={DiagnosticField.MESSAGE_PRIMARY: message.encode()},
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


class TestDataErrorHandling:
    """Test handling of class-22 DataError exceptions other than InvalidRegularExpression.

    DivisionByZero is #948's motivating case ("power/0>1" is a syntactically valid query Postgres
    rejects at runtime), but the handler catches psycopg.errors.DataError itself, not one member at a
    time — this pins that a DataError subclass no test names explicitly is still caught.
    """

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource(
            last_import_time=multiprocessing.Value("d", time.time(), lock=True),
        )

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        if hasattr(self, "api_resource") and self.api_resource:
            self.api_resource._conn_pool.close()

    def test_search_sql_handles_division_by_zero(self) -> None:
        """power/0>1 parses cleanly but divides by zero at runtime — a 400, not a 500."""
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = psycopg.errors.DivisionByZero(
                "division by zero",
                info={DiagnosticField.MESSAGE_PRIMARY: b"division by zero"},
            )

            with pytest.raises(falcon.HTTPBadRequest) as exc_info:
                self.api_resource._search_sql(**search_kwargs("power/0>1"))

            assert exc_info.value.title == "Invalid Search Query"
            assert "power/0>1" in exc_info.value.description
            assert "division by zero" in exc_info.value.description
            assert mock_run_query.call_count == 1

    def test_search_sql_handles_data_error_with_no_diagnostic(self) -> None:
        """A DataError with no diag.message_primary still gets a sensible fallback message."""
        with patch.object(self.api_resource, "_run_query") as mock_run_query:
            mock_run_query.side_effect = psycopg.errors.NumericValueOutOfRange("out of range")

            with pytest.raises(falcon.HTTPBadRequest) as exc_info:
                self.api_resource._search_sql(**search_kwargs("power=99999999999999"))

            assert exc_info.value.title == "Invalid Search Query"
            assert "the value is not valid for this comparison" in exc_info.value.description


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
