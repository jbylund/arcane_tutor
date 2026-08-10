"""Parity tests for the query fixtures shared with the frontend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.parsing import balance_partial_query

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "static" / "fixtures"

BALANCE_QUERIES = json.loads((FIXTURE_DIR / "balance_queries.json").read_text(encoding="utf-8"))

# Queries the parsers accept, which the frontend therefore must not reject before sending. Every
# client-side check that has an opinion about spans (balanceSuffix, validateQuery) runs against this
# same list in the jest suite, so a fourth opinion about where a regex starts cannot silently drop
# a query the backend would have answered (#908).
ACCEPTED_QUERIES = json.loads((FIXTURE_DIR / "accepted_queries.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    argnames=("input_query", "expected_suffix"),
    argvalues=[(case["input"], case["suffix"]) for case in BALANCE_QUERIES],
    ids=[repr(case["input"]) for case in BALANCE_QUERIES],
)
def test_balance_partial_query_matches_frontend_fixture(input_query: str, expected_suffix: str | None) -> None:
    """The Python balancer must match the shared frontend fixture contract."""
    if expected_suffix is None:
        with pytest.raises(ValueError, match=r"Unbalanced closing character.*cannot be balanced"):
            balance_partial_query(input_query)
        return

    assert balance_partial_query(input_query) == input_query + expected_suffix


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[[query] for query in ACCEPTED_QUERIES],
    ids=[repr(query) for query in ACCEPTED_QUERIES],
)
def test_accepted_queries_parse_and_balance_to_themselves(parse_query, query: str) -> None:
    """Both parsers accept every shared accepted query, and balancing leaves it untouched."""
    assert balance_partial_query(query) == query
    parse_query(query)
