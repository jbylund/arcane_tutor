"""Integration tests for mana cost parsing with database functionality."""


from api.parsing import parse_scryfall_query
from api.parsing.parsing_f import generate_sql_query


class TestManaCostIntegration:
    """Test mana cost search functionality with end-to-end integration."""

    def test_mana_cost_exact_match_integration(self) -> None:
        """Test that exact mana cost search generates correct SQL end-to-end."""
        query = "mana={2}{G}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB equality query
        assert "card.mana_cost_jsonb =" in sql
        assert len(params) == 1

        # Parameter should contain the expected mana cost structure
        param_value = next(iter(params.values()))
        expected_value = {"{1}": [1, 2], "{G}": [1]}
        assert param_value == expected_value

    def test_mana_cost_greater_than_integration(self) -> None:
        """Test that mana cost greater than search generates correct SQL."""
        query = "mana>{1}{W}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB strict containment query (contains but not equal)
        assert "card.mana_cost_jsonb @>" in sql
        assert "card.mana_cost_jsonb <>" in sql
        assert " AND " in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{1}": [1], "{W}": [1]}
        assert param_value == expected_value

    def test_mana_cost_less_than_integration(self) -> None:
        """Test that mana cost less than search generates correct SQL."""
        query = "mana<{3}{R}{R}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB strict containment query (is contained by but not equal)
        assert "card.mana_cost_jsonb <@" in sql
        assert "card.mana_cost_jsonb <>" in sql
        assert " AND " in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{1}": [1, 2, 3], "{R}": [1, 2]}
        assert param_value == expected_value

    def test_mana_cost_contains_integration(self) -> None:
        """Test that mana cost contains search (colon operator) generates correct SQL."""
        query = "mana:G"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB containment query
        assert "card.mana_cost_jsonb @>" in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{G}": [1]}
        assert param_value == expected_value

    def test_hybrid_mana_integration(self) -> None:
        """Test that hybrid mana costs generate correct SQL."""
        query = "mana:{W/U}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB containment query for hybrid mana
        assert "card.mana_cost_jsonb @>" in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{W}": [1], "{U}": [1]}
        assert param_value == expected_value

    def test_phyrexian_mana_integration(self) -> None:
        """Test that Phyrexian mana costs generate correct SQL."""
        query = "mana={G/P}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB equality query for Phyrexian mana
        assert "card.mana_cost_jsonb =" in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{G}": [1], "{P}": [1]}
        assert param_value == expected_value

    def test_shorthand_mana_integration(self) -> None:
        """Test that shorthand mana notation works correctly."""
        queries_and_expected = [
            ("mana:W", {"{W}": [1]}),
            ("mana:U", {"{U}": [1]}),
            ("mana:B", {"{B}": [1]}),
            ("mana:R", {"{R}": [1]}),
            ("mana:G", {"{G}": [1]}),
            ("mana:C", {"{C}": [1]}),
        ]

        for query, expected_value in queries_and_expected:
            parsed = parse_scryfall_query(query)
            sql, params = generate_sql_query(parsed)

            assert "card.mana_cost_jsonb @>" in sql
            assert len(params) == 1

            param_value = next(iter(params.values()))
            assert param_value == expected_value, f"Query {query} failed"

    def test_mana_alias_integration(self) -> None:
        """Test that mana cost alias 'm:' works correctly."""
        query = "m={2}{R}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB equality query using the alias
        assert "card.mana_cost_jsonb =" in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{1}": [1, 2], "{R}": [1]}
        assert param_value == expected_value

    def test_complex_mana_cost_query_integration(self) -> None:
        """Test complex queries combining mana cost searches."""
        query = "mana>={2}{G} and mana<={4}{G}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate AND query with two JSONB containment clauses
        assert "AND" in sql
        sql_parts = sql.split(" AND ")
        assert len([part for part in sql_parts if "card.mana_cost_jsonb" in part]) >= 2

        # Should have two parameters
        assert len(params) == 2

        # Parameters should contain the expected mappings
        param_values = list(params.values())
        expected_values = [
            {"{1}": [1, 2], "{G}": [1]},  # {2}{G}
            {"{1}": [1, 2, 3, 4], "{G}": [1]},  # {4}{G}
        ]
        for expected in expected_values:
            assert expected in param_values, f"Expected {expected} in {param_values}"

    def test_mana_cost_or_query_integration(self) -> None:
        """Test OR queries with mana costs."""
        query = "mana:{W} or mana:{U}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate OR query with two JSONB containment clauses
        assert " OR " in sql
        sql_parts = sql.split(" OR ")
        assert len(sql_parts) >= 2
        assert all("card.mana_cost_jsonb @>" in part for part in sql_parts if "card.mana_cost_jsonb" in part)

        # Should have two parameters
        assert len(params) == 2

        # Parameters should contain the expected mappings
        param_values = list(params.values())
        expected_values = [{"{W}": [1]}, {"{U}": [1]}]
        for expected in expected_values:
            assert expected in param_values

    def test_mana_cost_mixed_with_other_attributes_integration(self) -> None:
        """Test mana cost queries combined with other attributes."""
        query = "mana>={2}{G} and cmc<=4"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate AND query with mana cost JSONB and CMC numeric comparison
        assert "AND" in sql
        assert "card.mana_cost_jsonb @>" in sql
        assert "card.cmc <=" in sql

        # Should have two parameters
        assert len(params) == 2

        # Check parameter types
        param_values = list(params.values())
        mana_param = None
        cmc_param = None

        for param in param_values:
            if isinstance(param, dict) and "{1}" in param:
                mana_param = param
            elif isinstance(param, int):
                cmc_param = param

        assert mana_param == {"{1}": [1, 2], "{G}": [1]}
        assert cmc_param == 4

    def test_mana_cost_not_equal_integration(self) -> None:
        """Test mana cost not equal queries."""
        query = "mana!=G"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB not equal query
        assert "card.mana_cost_jsonb <>" in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        expected_value = {"{G}": [1]}
        assert param_value == expected_value

    def test_mana_cost_zero_cost_integration(self) -> None:
        """Test zero mana cost handling."""
        query = "mana={0}"
        parsed = parse_scryfall_query(query)
        sql, params = generate_sql_query(parsed)

        # Should generate JSONB equality query for zero cost
        assert "card.mana_cost_jsonb =" in sql
        assert len(params) == 1

        param_value = next(iter(params.values()))
        # {0} should not generate any requirements, but parsing should not fail
        assert isinstance(param_value, dict)

    def test_variable_mana_cost_integration(self) -> None:
        """Test variable mana cost (X, Y, Z) handling."""
        test_cases = ["mana:X", "mana:Y", "mana:Z"]

        for query in test_cases:
            parsed = parse_scryfall_query(query)
            sql, params = generate_sql_query(parsed)

            assert "card.mana_cost_jsonb @>" in sql
            assert len(params) == 1

            param_value = next(iter(params.values()))
            variable = query.split(":")[1]
            expected_value = {f"{{{variable}}}": [1]}
            assert param_value == expected_value
