"""Parity tests for the query fixtures shared with the frontend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.parsing import balance_partial_query
from api.parsing.hand_parser import LexError, tokenize

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


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[[query] for query in ACCEPTED_QUERIES],
    ids=[repr(query) for query in ACCEPTED_QUERIES],
)
def test_every_prefix_balances_to_a_query_the_lexer_accepts(query: str) -> None:
    """Balancing a half-typed query must never leave a span open.

    Typeahead balances on every keystroke, so every prefix of a query matters, not just the finished
    one. The lexer is the independent authority: it is the thing that rejects an unclosed span, so if
    it accepts the balanced prefix then the balancer and the span rules in api.parsing.spans agree.
    Checking only finished queries is how an unterminated regex went unnoticed — the balancer read on
    past it and closed the user's metacharacters as if they were query structure.
    """
    for length in range(1, len(query) + 1):
        prefix = query[:length]
        try:
            balanced = balance_partial_query(prefix)
        except ValueError:
            # A prefix holding a ')' with no opener cannot be balanced; the frontend reports it as-is.
            continue
        try:
            tokenize(balanced)
        except LexError as exc:
            pytest.fail(f"{prefix!r} balanced to {balanced!r}, which the lexer rejects: {exc}")
