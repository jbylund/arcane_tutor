"""Tests for public search query complexity bounds."""

from __future__ import annotations

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.hand_parser import TT, tokenize
from api.parsing.query_budget import (
    MAX_BOOLEAN_CLAUSES,
    MAX_GROUP_DEPTH,
    MAX_QUERY_TOKENS,
    MAX_QUERY_UTF8_BYTES,
    QUERY_TOO_COMPLEX_MESSAGE,
    QUERY_TOO_LONG_MESSAGE,
    QueryBudgetExceeded,
    check_ast_budget,
    check_query_byte_length,
    check_search_param_lengths,
)


def _exact_byte_query(num_bytes: int) -> str:
    assert num_bytes >= 1
    return "a" * num_bytes


class TestQueryByteLimits:
    def test_accepts_query_at_byte_limit(self) -> None:
        check_query_byte_length(_exact_byte_query(MAX_QUERY_UTF8_BYTES))

    def test_rejects_query_one_byte_over_limit(self) -> None:
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            check_query_byte_length(_exact_byte_query(MAX_QUERY_UTF8_BYTES + 1))
        assert exc_info.value.kind == "length"
        assert exc_info.value.user_message == QUERY_TOO_LONG_MESSAGE

    def test_rejects_oversized_unused_query_alias(self) -> None:
        with pytest.raises(QueryBudgetExceeded):
            check_search_param_lengths({"q": "bolt", "query": _exact_byte_query(MAX_QUERY_UTF8_BYTES + 1)})


def _arithmetic_token_query(*, token_target: int) -> str:
    """Build a single-clause arithmetic query with exactly ``token_target`` lexer tokens."""
    for prefix, base_tokens in (("cmc=1", 3), ("cmc>=1", 4)):
        remaining = token_target - base_tokens
        if remaining >= 0 and remaining % 2 == 0:
            return prefix + "+1" * (remaining // 2)
    msg = f"cannot build arithmetic query with exactly {token_target} tokens"
    raise ValueError(msg)


class TestTokenAndDepthLimits:
    def test_rejects_query_with_too_many_tokens(self) -> None:
        query = _arithmetic_token_query(token_target=MAX_QUERY_TOKENS + 1)
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "complexity"
        assert exc_info.value.user_message == QUERY_TOO_COMPLEX_MESSAGE

    def test_accepts_lexer_at_token_limit(self) -> None:
        # 255 is the largest single-clause arithmetic query that hits an odd token budget.
        query = _arithmetic_token_query(token_target=MAX_QUERY_TOKENS - 1)
        tokens = tokenize(query)
        assert len([token for token in tokens if token.type != TT.EOF]) == MAX_QUERY_TOKENS - 1

    def test_rejects_query_with_excessive_group_depth(self) -> None:
        query = "(" * (MAX_GROUP_DEPTH + 1) + "name:a" + ")" * (MAX_GROUP_DEPTH + 1)
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "complexity"


class TestAstBudget:
    def test_rejects_too_many_boolean_clauses(self) -> None:
        query = " or ".join(f"name:n{i}" for i in range(MAX_BOOLEAN_CLAUSES + 1))
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "complexity"

    def test_rejects_derived_predicate_expansion_over_clause_limit(self) -> None:
        query = " or ".join(["is:permanent"] * 6)
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "complexity"

    def test_accepts_small_derived_predicate_before_rewrite(self) -> None:
        parsed = parse_scryfall_query("is:split")
        check_ast_budget(parsed)

    def test_rejects_ast_with_too_many_nodes(self) -> None:
        query = " ".join(f"name:n{i}" for i in range(43))
        with pytest.raises(QueryBudgetExceeded):
            parse_scryfall_query(query)
