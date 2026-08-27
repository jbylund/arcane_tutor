"""Query parsing and AST generation for Scryfall search queries."""

from functools import partial

from api.parsing import hand_parser
from api.parsing.hand_parser import ParseError
from api.parsing.nodes import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    ManaValueNode,
    NotNode,
    NumericValueNode,
    OrNode,
    Query,
    QueryContext,
    QueryNode,
    RegexValueNode,
    StringValueNode,
    TrueNode,
)
from api.parsing.parsing_f import balance_partial_query
from api.parsing.post_parse import finalize_query, parse_query
from api.parsing.query_budget import QueryBudgetExceeded
from api.parsing.sql_generation import generate_sql_query

parse_scryfall_query = partial(parse_query, parser_fn=hand_parser.parse_str_to_query)

__all__ = [
    "AndNode",
    "AttributeNode",
    "BinaryOperatorNode",
    "ManaValueNode",
    "NotNode",
    "NumericValueNode",
    "OrNode",
    "ParseError",
    "Query",
    "QueryBudgetExceeded",
    "QueryContext",
    "QueryNode",
    "RegexValueNode",
    "StringValueNode",
    "TrueNode",
    "balance_partial_query",
    "finalize_query",
    "generate_sql_query",
    "parse_query",
    "parse_scryfall_query",
]
