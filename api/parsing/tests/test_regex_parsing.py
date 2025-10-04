"""Tests for regex pattern parsing."""

from __future__ import annotations

import pytest

from api.parsing.nodes import Query, RegexValueNode
from api.parsing.parsing_f import parse_scryfall_query
from api.parsing.scryfall_nodes import ScryfallAttributeNode, ScryfallBinaryOperatorNode


class TestRegexParsing:
    """Test that regex patterns are parsed correctly."""

    @pytest.mark.parametrize(
        ("query", "expected_pattern"),
        [
            ("regex:/test/", "test"),
            ("re:/test/", "test"),
            ("regex:/^tap/", "^tap"),
            ("re:/untap$/", "untap$"),
            ("regex:/\\d+/", "\\d+"),
            ("regex:/[0-9]+/", "[0-9]+"),
            ("regex:/flying|vigilance/", "flying|vigilance"),
            ("regex:/tap.*creature/", "tap.*creature"),
        ],
    )
    def test_regex_pattern_parsing(self, query: str, expected_pattern: str) -> None:
        """Test that regex patterns are parsed correctly."""
        result = parse_scryfall_query(query)
        assert isinstance(result, Query)
        assert isinstance(result.root, ScryfallBinaryOperatorNode)
        assert isinstance(result.root.lhs, ScryfallAttributeNode)
        assert result.root.lhs.attribute_name == "oracle_text"
        assert isinstance(result.root.rhs, RegexValueNode)
        assert result.root.rhs.value == expected_pattern

    def test_regex_with_escaped_slash(self) -> None:
        """Test that escaped forward slashes in regex patterns are handled correctly."""
        result = parse_scryfall_query(r"regex:/test\/pattern/")
        assert isinstance(result, Query)
        assert isinstance(result.root, ScryfallBinaryOperatorNode)
        assert isinstance(result.root.rhs, RegexValueNode)
        assert result.root.rhs.value == r"test\/pattern"

    def test_regex_alias_re_works(self) -> None:
        """Test that the 're:' alias works the same as 'regex:'."""
        result1 = parse_scryfall_query("regex:/test/")
        result2 = parse_scryfall_query("re:/test/")

        assert isinstance(result1, Query)
        assert isinstance(result2, Query)
        assert isinstance(result1.root, ScryfallBinaryOperatorNode)
        assert isinstance(result2.root, ScryfallBinaryOperatorNode)
        assert result1.root.lhs.attribute_name == result2.root.lhs.attribute_name
        assert result1.root.rhs.value == result2.root.rhs.value

    def test_regex_with_complex_patterns(self) -> None:
        """Test parsing of complex regex patterns."""
        test_cases = [
            ("regex:/^destroy target/", "^destroy target"),
            ("re:/\\bflying\\b/", "\\bflying\\b"),
            ("regex:/tap.*untap/", "tap.*untap"),
            ("re:/[wubrg]{2,}/", "[wubrg]{2,}"),
        ]

        for query, expected_pattern in test_cases:
            result = parse_scryfall_query(query)
            assert isinstance(result, Query)
            assert isinstance(result.root.rhs, RegexValueNode)
            assert result.root.rhs.value == expected_pattern

    def test_regex_combined_with_other_conditions(self) -> None:
        """Test that regex can be combined with other search conditions."""
        result = parse_scryfall_query("regex:/flying/ cmc=3")
        assert isinstance(result, Query)
        # Should have an AND node combining the regex condition and the cmc condition
        assert hasattr(result.root, "operands")

    def test_regex_with_or_condition(self) -> None:
        """Test regex combined with OR."""
        result = parse_scryfall_query("regex:/flying/ OR cmc=3")
        assert isinstance(result, Query)
        # Should have an OR node
        assert hasattr(result.root, "operands")

    def test_regex_with_negation(self) -> None:
        """Test negated regex patterns."""
        result = parse_scryfall_query("-regex:/flying/")
        assert isinstance(result, Query)
        # Should have a NOT node
        assert hasattr(result.root, "operand")


if __name__ == "__main__":
    pytest.main([__file__])
