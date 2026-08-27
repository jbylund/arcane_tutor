"""Post-parse pipeline — the single seam from query string to production AST.

Two string front ends (hand-rolled production parser, legacy pyparsing oracle for
parity tests) produce different pre-rewrite trees. Both must pass through
``finalize_query`` so rewrites, regex limits, and compound normalization stay
identical. ``parse_scryfall_query`` is the only production entry point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.parsing.hand_parser import parse_query as parse_hand_query
from api.parsing.pyparsing_based import parse_search_query_raw
from api.parsing.query_budget import check_query_byte_length
from api.parsing.regex_budget import validate_regex_patterns
from api.parsing.rewrite import flatten_and_deduplicate_compounds, rewrite_query

if TYPE_CHECKING:
    from api.parsing.nodes import Query


def finalize_query(query: Query) -> Query:
    """Apply semantic rewrites, regex static limits, then flatten+dedupe normalization."""
    query = rewrite_query(query)
    validate_regex_patterns(query)
    return flatten_and_deduplicate_compounds(query)


def _check_public_query_length(query: str | None) -> None:
    if query is not None:
        check_query_byte_length(query)


def parse_scryfall_query(query: str | None) -> Query:
    """Parse a search query with the production hand parser and post-parse pipeline."""
    _check_public_query_length(query)
    return finalize_query(parse_hand_query(query))


def parse_pyparsing_query(query: str | None) -> Query:
    """Same pipeline as ``parse_scryfall_query``, using the pyparsing front end (parity tests)."""
    _check_public_query_length(query)
    return finalize_query(parse_search_query_raw(query))
