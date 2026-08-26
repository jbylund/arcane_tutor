"""Tests for post-rewrite regex static limits."""

from __future__ import annotations

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.query_budget import QUERY_REGEX_REJECTED_MESSAGE, InvalidRegexPatternError, QueryBudgetExceeded
from api.parsing.regex_budget import MAX_PATTERN_UTF8_BYTES, MAX_REGEX_LEAVES_PER_QUERY


class TestRegexBudgetAcceptsLegitPatterns:
    @pytest.mark.parametrize(
        "query",
        [
            "o:/draw .* cards?/",
            "t:creature o:/^{T}:/",
            "name:/\\bizzet\\b/",
            "o:/(?<!non)artifact/",
            "o:/(destroy|exile) target creature/",
            "o:/\\broll(ed)?\\b.*\\b(d\\d+|die|dice)\\b/",
        ],
    )
    def test_accepts_documented_patterns(self, query: str) -> None:
        parse_scryfall_query(query)


class TestRegexLeafLimit:
    def test_accepts_ten_regex_leaves(self) -> None:
        query = " ".join("o:/(?=.*draw)/" for _ in range(MAX_REGEX_LEAVES_PER_QUERY))
        parse_scryfall_query(query)

    def test_rejects_eleven_regex_leaves(self) -> None:
        query = " ".join("o:/(?=.*draw)/" for _ in range(MAX_REGEX_LEAVES_PER_QUERY + 1))
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "regex_leaves"
        assert exc_info.value.user_message == QUERY_REGEX_REJECTED_MESSAGE


class TestRegexPatternLimits:
    def test_rejects_oversized_pattern(self) -> None:
        query = f"o:/.{'a' * MAX_PATTERN_UTF8_BYTES}/"
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "regex_pattern"

    def test_rejects_stacked_numeric_quantifiers(self) -> None:
        with pytest.raises(InvalidRegexPatternError):
            parse_scryfall_query("o:/a{10}{10}{10}{10}{10}/")

    def test_rejects_wide_alternation(self) -> None:
        alts = "|".join(f"w{i}" for i in range(500))
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(f"o:/{alts}/")
        assert exc_info.value.kind == "regex_pattern"

    def test_plain_literal_lowering_skips_regex_budget(self) -> None:
        long_literal = "a" * (MAX_PATTERN_UTF8_BYTES + 1)
        parse_scryfall_query(f"o:/{long_literal}/")

    def test_rejects_backreferences(self) -> None:
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query("o:/(a)\\1/")
        assert exc_info.value.kind == "regex_pattern"

    def test_accepts_grouped_numeric_repeat(self) -> None:
        parse_scryfall_query("o:/(?:a{10}){10}/")

    def test_rejects_nested_numeric_repeat_product(self) -> None:
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query("o:/(?:a{50}){50}/")
        assert exc_info.value.kind == "regex_pattern"

    def test_rejects_conditional_groups(self) -> None:
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query("o:/(a)(?(1)b)/")
        assert exc_info.value.kind == "regex_pattern"

    def test_accepts_escaped_lookaround_literal(self) -> None:
        parse_scryfall_query(r"o:/\(\?=not a lookaround/")


class TestRegexOperatorCoverage:
    @pytest.mark.parametrize(
        "query",
        [
            "o=/draw/",
            "o!=/draw/",
            "name=/^bolt$/",
        ],
    )
    def test_equality_operators_count_regex_leaves(self, query: str) -> None:
        parse_scryfall_query(query)

    def test_equality_operator_leaf_limit(self) -> None:
        query = " ".join("o=/draw/" for _ in range(MAX_REGEX_LEAVES_PER_QUERY + 1))
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(query)
        assert exc_info.value.kind == "regex_leaves"

    def test_equality_operator_pattern_limit(self) -> None:
        with pytest.raises(QueryBudgetExceeded) as exc_info:
            parse_scryfall_query(f"o!=/.{'a' * MAX_PATTERN_UTF8_BYTES}/")
        assert exc_info.value.kind == "regex_pattern"
