"""Tests for mana cost parsing functionality."""

import pytest

from api.parsing import parse_scryfall_query
from api.parsing.parsing_f import generate_sql_query
from api.parsing.scryfall_nodes import parse_mana_cost_string


@pytest.mark.parametrize(
    argnames=("mana_cost", "expected_result"),
    argvalues=[
        # Basic mana costs
        ("{2}{G}", {"{1}": [1, 2], "{G}": [1]}),
        ("{1}", {"{1}": [1]}),
        ("{3}", {"{1}": [1, 2, 3]}),
        ("{5}", {"{1}": [1, 2, 3, 4, 5]}),

        # Colored mana
        ("{G}", {"{G}": [1]}),
        ("{W}", {"{W}": [1]}),
        ("{U}", {"{U}": [1]}),
        ("{B}", {"{B}": [1]}),
        ("{R}", {"{R}": [1]}),
        ("{C}", {"{C}": [1]}),

        # Shorthand notation
        ("G", {"{G}": [1]}),
        ("W", {"{W}": [1]}),
        ("U", {"{U}": [1]}),
        ("B", {"{B}": [1]}),
        ("R", {"{R}": [1]}),
        ("C", {"{C}": [1]}),
        ("X", {"{X}": [1]}),
        ("Y", {"{Y}": [1]}),
        ("Z", {"{Z}": [1]}),

        # Multiple colored mana
        ("{W}{W}", {"{W}": [1, 2]}),
        ("{G}{G}{G}", {"{G}": [1, 2, 3]}),
        ("{R}{U}{B}", {"{R}": [1], "{U}": [1], "{B}": [1]}),

        # Mixed mana costs
        ("{3}{W}{W}", {"{1}": [1, 2, 3], "{W}": [1, 2]}),
        ("{1}{R}{G}", {"{1}": [1], "{R}": [1], "{G}": [1]}),

        # Hybrid mana
        ("{W/U}", {"{W}": [1], "{U}": [1]}),
        ("{U/B}", {"{U}": [1], "{B}": [1]}),
        ("{B/R}", {"{B}": [1], "{R}": [1]}),
        ("{R/G}", {"{R}": [1], "{G}": [1]}),
        ("{G/W}", {"{G}": [1], "{W}": [1]}),

        # Generic/color hybrid
        ("{2/W}", {"{1}": [1, 2], "{W}": [1]}),
        ("{2/U}", {"{1}": [1, 2], "{U}": [1]}),
        ("{2/B}", {"{1}": [1, 2], "{B}": [1]}),
        ("{2/R}", {"{1}": [1, 2], "{R}": [1]}),
        ("{2/G}", {"{1}": [1, 2], "{G}": [1]}),

        # Phyrexian mana
        ("{W/P}", {"{W}": [1], "{P}": [1]}),
        ("{U/P}", {"{U}": [1], "{P}": [1]}),
        ("{B/P}", {"{B}": [1], "{P}": [1]}),
        ("{R/P}", {"{R}": [1], "{P}": [1]}),
        ("{G/P}", {"{G}": [1], "{P}": [1]}),

        # Complex combinations
        ("{2}{W/U}", {"{1}": [1, 2], "{W}": [1], "{U}": [1]}),
        ("{1}{R/G}{B}", {"{1}": [1], "{R}": [1], "{G}": [1], "{B}": [1]}),

        # Snow mana
        ("{S}", {"{S}": [1]}),

        # Energy
        ("{E}", {"{E}": [1]}),
    ],
)
def test_parse_mana_cost_string(mana_cost: str, expected_result: dict) -> None:
    """Test that mana cost strings parse into correct JSONB representations."""
    result = parse_mana_cost_string(mana_cost)
    assert result == expected_result, f"Expected {expected_result}, got {result}"


@pytest.mark.parametrize(
    argnames=("query", "expected_ast_type", "expected_operator", "expected_value"),
    argvalues=[
        ("mana={2}{G}", "ScryfallBinaryOperatorNode", "=", "{2}{G}"),
        ("mana>{2}{G}", "ScryfallBinaryOperatorNode", ">", "{2}{G}"),
        ("mana<{2}{G}", "ScryfallBinaryOperatorNode", "<", "{2}{G}"),
        ("mana>={W/U}", "ScryfallBinaryOperatorNode", ">=", "{W/U}"),
        ("mana<={R/P}", "ScryfallBinaryOperatorNode", "<=", "{R/P}"),
        ("mana!=G", "ScryfallBinaryOperatorNode", "!=", "G"),
        ("mana:G", "ScryfallBinaryOperatorNode", ":", "G"),
        ("m={3}{W}{W}", "ScryfallBinaryOperatorNode", "=", "{3}{W}{W}"),
    ],
)
def test_mana_cost_query_parsing(query: str, expected_ast_type: str, expected_operator: str, expected_value: str) -> None:
    """Test that mana cost queries parse into correct AST structures."""
    parsed = parse_scryfall_query(query)
    root = parsed.root

    assert type(root).__name__ == expected_ast_type
    assert root.operator == expected_operator
    assert root.rhs.value == expected_value
    assert root.lhs.attribute_name == "mana_cost_jsonb"


@pytest.mark.parametrize(
    argnames=("query", "expected_sql_pattern", "expected_param_value"),
    argvalues=[
        # Exact match
        (
            "mana={2}{G}",
            "(card.mana_cost_jsonb = %(p_dict_",
            {"{1}": [1, 2], "{G}": [1]},
        ),
        # Greater than (strict containment)
        (
            "mana>{2}{G}",
            "(card.mana_cost_jsonb @> %(p_dict_",
            {"{1}": [1, 2], "{G}": [1]},
        ),
        # Less than (is contained by)
        (
            "mana<{2}{G}",
            "(card.mana_cost_jsonb <@ %(p_dict_",
            {"{1}": [1, 2], "{G}": [1]},
        ),
        # Greater than or equal (containment)
        (
            "mana>={W/U}",
            "(card.mana_cost_jsonb @> %(p_dict_",
            {"{W}": [1], "{U}": [1]},
        ),
        # Less than or equal (is contained by)
        (
            "mana<={R/P}",
            "(card.mana_cost_jsonb <@ %(p_dict_",
            {"{R}": [1], "{P}": [1]},
        ),
        # Not equal
        (
            "mana!=G",
            "(card.mana_cost_jsonb <> %(p_dict_",
            {"{G}": [1]},
        ),
        # Contains (colon operator)
        (
            "mana:G",
            "(card.mana_cost_jsonb @> %(p_dict_",
            {"{G}": [1]},
        ),
        # Shorthand attribute alias
        (
            "m={3}{W}{W}",
            "(card.mana_cost_jsonb = %(p_dict_",
            {"{1}": [1, 2, 3], "{W}": [1, 2]},
        ),
    ],
)
def test_mana_cost_sql_generation(query: str, expected_sql_pattern: str, expected_param_value: dict) -> None:
    """Test that mana cost queries generate correct SQL with proper JSONB operations."""
    parsed = parse_scryfall_query(query)
    sql, params = generate_sql_query(parsed)

    # Check that SQL contains the expected pattern
    assert expected_sql_pattern in sql, f"Expected '{expected_sql_pattern}' in SQL: {sql}"

    # Check that we have exactly one parameter
    assert len(params) == 1, f"Expected exactly one parameter, got {len(params)}"

    # Check that the parameter value is correct
    param_value = next(iter(params.values()))
    assert param_value == expected_param_value, f"Expected param {expected_param_value}, got {param_value}"


def test_mana_cost_complex_queries() -> None:
    """Test complex mana cost queries with logical operators."""
    # Test AND query
    parsed = parse_scryfall_query("mana>={2}{G} and mana<={3}{G}")
    sql, params = generate_sql_query(parsed)

    assert "AND" in sql
    assert len(params) == 2

    # Test OR query
    parsed = parse_scryfall_query("mana:{W} or mana:{U}")
    sql, params = generate_sql_query(parsed)

    assert " OR " in sql
    assert len(params) == 2


def test_mana_cost_edge_cases() -> None:
    """Test edge cases in mana cost parsing."""
    # Empty components should not break parsing
    test_cases = [
        "{0}",  # Zero mana cost
        "{X}",  # Variable mana cost
        "{W}{U}{B}{R}{G}",  # WUBRG
    ]

    for case in test_cases:
        result = parse_mana_cost_string(case)
        assert isinstance(result, dict), f"Expected dict for {case}, got {type(result)}"
        assert len(result) > 0, f"Expected non-empty result for {case}"


def test_mana_cost_comparison_semantics() -> None:
    """Test that mana cost comparison semantics are correct based on the issue description."""
    # According to the issue:
    # "a mana cost is greater than another if it includes all the same symbols and more,
    # and it's less if it includes only a subset of symbols"

    # {2}{G} should be represented as {"{1}": [1, 2], "{G}": [1]}
    # This means a card with this mana cost needs:
    # - At least 1 green mana (exactly 1)
    # - Between 1 and 2 generic mana

    result_2g = parse_mana_cost_string("{2}{G}")
    expected_2g = {"{1}": [1, 2], "{G}": [1]}
    assert result_2g == expected_2g

    # {1}{G} should be {"{1}": [1], "{G}": [1]}
    result_1g = parse_mana_cost_string("{1}{G}")
    expected_1g = {"{1}": [1], "{G}": [1]}
    assert result_1g == expected_1g

    # {3}{G}{G} should be {"{1}": [1, 2, 3], "{G}": [1, 2]}
    result_3gg = parse_mana_cost_string("{3}{G}{G}")
    expected_3gg = {"{1}": [1, 2, 3], "{G}": [1, 2]}
    assert result_3gg == expected_3gg


def test_mana_cost_attribute_routing() -> None:
    """Test that mana queries are routed to the correct database column."""
    # Both 'mana' and 'm' should route to mana_cost_jsonb
    queries = ["mana:{G}", "m:{G}"]

    for query in queries:
        parsed = parse_scryfall_query(query)
        assert parsed.root.lhs.attribute_name == "mana_cost_jsonb"
