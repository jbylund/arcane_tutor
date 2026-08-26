"""Tests for public search query complexity bounds."""

from __future__ import annotations

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.query_budget import (
    MAX_GROUP_DEPTH,
    MAX_QUERY_UTF8_BYTES,
    QUERY_TOO_LONG_MESSAGE,
    QueryBudgetExceeded,
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


class TestGroupDepthLimit:
    def test_accepts_query_at_max_nesting_depth(self) -> None:
        query = "(" * MAX_GROUP_DEPTH + "name:a" + ")" * MAX_GROUP_DEPTH
        parse_scryfall_query(query)

    def test_rejects_query_one_level_over_max_nesting_depth(self) -> None:
        query = "(" * (MAX_GROUP_DEPTH + 1) + "name:a" + ")" * (MAX_GROUP_DEPTH + 1)
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "depth"
        assert exc_info.value.user_message == QUERY_TOO_LONG_MESSAGE

    def test_sibling_groups_do_not_accumulate_nesting_depth(self) -> None:
        query = " ".join(f"(name:n{i})" for i in range(40))
        parse_scryfall_query(query)


class TestDecklistShape:
    def test_accepts_long_flat_or_chain_within_byte_limit(self) -> None:
        query = " or ".join(f"name:n{i}" for i in range(80))
        assert len(query.encode("utf-8")) <= MAX_QUERY_UTF8_BYTES
        parse_scryfall_query(query)

    def test_accepts_parenthesized_or_chain_with_format_filter(self) -> None:
        body = " or ".join(f'!"Card {i}"' for i in range(60))
        query = f"({body}) f:commander"
        assert len(query.encode("utf-8")) <= MAX_QUERY_UTF8_BYTES
        parse_scryfall_query(query)
