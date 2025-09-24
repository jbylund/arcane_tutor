"""Tests for operator strategy functionality."""

from __future__ import annotations

import pytest

from api.parsing.db_info import OperatorStrategy
from api.parsing.scryfall_nodes import get_operator_strategy


class TestOperatorStrategy:
    """Test operator strategy functionality."""

    def test_get_operator_strategy_exact_fields(self) -> None:
        """Test that exact match fields return EXACT strategy."""
        exact_fields = ["card_set_code", "card_layout", "card_border"]

        for field in exact_fields:
            strategy = get_operator_strategy(field)
            assert strategy == OperatorStrategy.EXACT, f"Field {field} should use EXACT strategy"

    def test_get_operator_strategy_pattern_fields(self) -> None:
        """Test that pattern match fields return PATTERN strategy."""
        pattern_fields = ["card_name", "card_artist", "oracle_text", "flavor_text"]

        for field in pattern_fields:
            strategy = get_operator_strategy(field)
            assert strategy == OperatorStrategy.PATTERN, f"Field {field} should use PATTERN strategy"

    def test_get_operator_strategy_unknown_field_defaults_to_pattern(self) -> None:
        """Test that unknown fields default to PATTERN strategy."""
        unknown_field = "nonexistent_field"
        strategy = get_operator_strategy(unknown_field)
        assert strategy == OperatorStrategy.PATTERN, "Unknown field should default to PATTERN strategy"

    def test_operator_strategy_enum_values(self) -> None:
        """Test that OperatorStrategy enum has expected values."""
        assert OperatorStrategy.EXACT == "exact"
        assert OperatorStrategy.PATTERN == "pattern"


if __name__ == "__main__":
    pytest.main([__file__])
