"""Tests for devotion and date SQL generation functionality."""

import pytest

from api import parsing


@pytest.mark.parametrize(
    argnames=("input_query", "expected_sql_contains", "expected_param_check"),
    argvalues=[
        # Devotion tests - check that SQL contains the devotion counting logic
        (
            "devotionw>=5",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'W'), 0)", ">="],
            lambda ctx: 5 in ctx.values(),
        ),
        (
            "devotionw=3",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'W'), 0)", "="],
            lambda ctx: 3 in ctx.values(),
        ),
        (
            "devotionu>2",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'U'), 0)", ">"],
            lambda ctx: 2 in ctx.values(),
        ),
        (
            "devotionb<=4",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'B'), 0)", "<="],
            lambda ctx: 4 in ctx.values(),
        ),
        (
            "devotionr<3",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'R'), 0)", "<"],
            lambda ctx: 3 in ctx.values(),
        ),
        (
            "devotiong>=2",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'G'), 0)", ">="],
            lambda ctx: 2 in ctx.values(),
        ),
        (
            "devotionc=1",
            ["COALESCE(jsonb_array_length(card.mana_cost_jsonb->'C'), 0)", "="],
            lambda ctx: 1 in ctx.values(),
        ),
        # Year tests - check that SQL contains year extraction
        (
            "year=2020",
            ["EXTRACT(YEAR FROM card.released_at)", "="],
            lambda ctx: 2020 in ctx.values(),
        ),
        (
            "year>=2020",
            ["EXTRACT(YEAR FROM card.released_at)", ">="],
            lambda ctx: 2020 in ctx.values(),
        ),
        (
            "year>2019",
            ["EXTRACT(YEAR FROM card.released_at)", ">"],
            lambda ctx: 2019 in ctx.values(),
        ),
        (
            "year<=2020",
            ["EXTRACT(YEAR FROM card.released_at)", "<="],
            lambda ctx: 2020 in ctx.values(),
        ),
        (
            "year<2021",
            ["EXTRACT(YEAR FROM card.released_at)", "<"],
            lambda ctx: 2021 in ctx.values(),
        ),
        # Date tests - check that SQL uses released_at field
        (
            "date=2020-01-01",
            ["card.released_at", "="],
            lambda ctx: "2020-01-01" in ctx.values(),
        ),
        (
            "date>=2020-01-01",
            ["card.released_at", ">="],
            lambda ctx: "2020-01-01" in ctx.values(),
        ),
        (
            "date>2020-06-15",
            ["card.released_at", ">"],
            lambda ctx: "2020-06-15" in ctx.values(),
        ),
        (
            "date<=2020-12-31",
            ["card.released_at", "<="],
            lambda ctx: "2020-12-31" in ctx.values(),
        ),
        (
            "date<2021-01-01",
            ["card.released_at", "<"],
            lambda ctx: "2021-01-01" in ctx.values(),
        ),
    ],
)
def test_devotion_date_sql_generation(
    input_query: str, expected_sql_contains: list[str], expected_param_check: callable,
) -> None:
    """Test SQL generation for devotion and date queries."""
    parsed = parsing.parse_scryfall_query(input_query)
    context = {}
    observed_sql = parsed.to_sql(context)

    # Check that all expected SQL fragments are present
    for expected_fragment in expected_sql_contains:
        assert expected_fragment in observed_sql, f"Expected '{expected_fragment}' in SQL: {observed_sql}"

    # Check that the parameter context is correct
    assert expected_param_check(context), f"Parameter check failed for context: {context}"


def test_devotion_sql_all_colors() -> None:
    """Test devotion SQL generation for all colors."""
    colors = {
        "w": "W",
        "u": "U",
        "b": "B",
        "r": "R",
        "g": "G",
        "c": "C",
    }

    for color_lower, color_upper in colors.items():
        query = f"devotion{color_lower}>=3"
        parsed = parsing.parse_scryfall_query(query)
        context = {}
        sql = parsed.to_sql(context)

        # Check that the SQL contains the correct color code
        assert f"card.mana_cost_jsonb->'{color_upper}'" in sql, f"SQL should reference {color_upper} for {query}"
        assert "COALESCE(jsonb_array_length(" in sql, f"SQL should count array length for {query}"
        assert 3 in context.values(), f"Context should contain value 3 for {query}"





def test_year_extraction_sql() -> None:
    """Test year SQL generation extracts year from date."""
    query = "year=2020"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    assert "EXTRACT(YEAR FROM card.released_at)" in sql
    assert "::INTEGER" in sql
    assert "=" in sql
    assert 2020 in context.values()


def test_date_comparison_sql() -> None:
    """Test date SQL generation uses released_at field."""
    query = "date>=2020-01-01"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    assert "card.released_at" in sql
    assert ">=" in sql
    assert "2020-01-01" in context.values()


def test_devotion_with_colon_operator() -> None:
    """Test devotion with colon operator converts to equals."""
    query = "devotionw:3"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Colon should be converted to equals
    assert "=" in sql
    assert 3 in context.values()


def test_year_with_colon_operator() -> None:
    """Test year with colon operator converts to equals."""
    query = "year:2020"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Colon should be converted to equals
    assert "=" in sql
    assert 2020 in context.values()


def test_date_with_colon_operator() -> None:
    """Test date with colon operator converts to equals."""
    query = "date:2020-01-01"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Colon should be converted to equals
    assert "=" in sql
    assert "2020-01-01" in context.values()


def test_devotion_combined_with_other_queries() -> None:
    """Test devotion combined with other search criteria."""
    query = "devotionw>=3 AND cmc<=4"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Should contain both devotion check and cmc check
    assert "COALESCE(jsonb_array_length(card.mana_cost_jsonb->'W'), 0)" in sql
    assert "card.cmc" in sql
    assert "AND" in sql


def test_date_combined_with_other_queries() -> None:
    """Test date combined with other search criteria."""
    query = "date>=2020-01-01 AND cmc=3"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Should contain both date check and cmc check
    assert "card.released_at" in sql
    assert "card.cmc" in sql
    assert "AND" in sql


def test_year_combined_with_other_queries() -> None:
    """Test year combined with other search criteria."""
    query = "year>=2020 AND type:creature"
    parsed = parsing.parse_scryfall_query(query)
    context = {}
    sql = parsed.to_sql(context)

    # Should contain both year extraction and type check
    assert "EXTRACT(YEAR FROM card.released_at)" in sql
    assert "card.card_types" in sql
    assert "AND" in sql
