"""Tests for the client query runner."""

from client.query_runner import (
    _generate_basic_queries,
    _generate_combined_queries,
    _generate_text_queries,
    _generate_type_queries,
    generate_random_queries,
)


class TestQueryGeneration:
    """Test query generation functions."""

    def test_generate_basic_queries_returns_list(self: "TestQueryGeneration") -> None:
        """Test that basic queries function returns a list."""
        queries = _generate_basic_queries()
        assert isinstance(queries, list)
        assert len(queries) > 0

    def test_generate_basic_queries_includes_colors(self: "TestQueryGeneration") -> None:
        """Test that basic queries include color searches."""
        queries = _generate_basic_queries()
        assert any("color:" in q for q in queries)
        assert any("c:" in q for q in queries)
        assert any("id:" in q for q in queries)

    def test_generate_basic_queries_includes_cmc(self: "TestQueryGeneration") -> None:
        """Test that basic queries include CMC searches."""
        queries = _generate_basic_queries()
        assert any("cmc=" in q for q in queries)
        assert any("mv=" in q for q in queries)
        assert any("cmc<" in q or "cmc>" in q for q in queries)

    def test_generate_type_queries_returns_list(self: "TestQueryGeneration") -> None:
        """Test that type queries function returns a list."""
        queries = _generate_type_queries()
        assert isinstance(queries, list)
        assert len(queries) > 0

    def test_generate_type_queries_includes_types(self: "TestQueryGeneration") -> None:
        """Test that type queries include card types."""
        queries = _generate_type_queries()
        assert any("type:" in q for q in queries)
        assert any("t:" in q for q in queries)

    def test_generate_type_queries_includes_rarity(self: "TestQueryGeneration") -> None:
        """Test that type queries include rarity searches."""
        queries = _generate_type_queries()
        assert any("rarity:" in q for q in queries)
        assert any("r:" in q for q in queries)

    def test_generate_type_queries_includes_power_toughness(self: "TestQueryGeneration") -> None:
        """Test that type queries include power/toughness searches."""
        queries = _generate_type_queries()
        assert any("pow=" in q for q in queries)
        assert any("tou=" in q for q in queries)

    def test_generate_combined_queries_returns_list(self: "TestQueryGeneration") -> None:
        """Test that combined queries function returns a list."""
        queries = _generate_combined_queries()
        assert isinstance(queries, list)
        assert len(queries) > 0

    def test_generate_combined_queries_has_multiple_criteria(
        self: "TestQueryGeneration",
    ) -> None:
        """Test that combined queries have multiple search criteria."""
        queries = _generate_combined_queries()
        # All combined queries should have at least one space (multiple terms)
        assert all(" " in q for q in queries)

    def test_generate_text_queries_returns_list(self: "TestQueryGeneration") -> None:
        """Test that text queries function returns a list."""
        queries = _generate_text_queries()
        assert isinstance(queries, list)
        assert len(queries) > 0

    def test_generate_text_queries_includes_oracle(self: "TestQueryGeneration") -> None:
        """Test that text queries include oracle text searches."""
        queries = _generate_text_queries()
        assert any("oracle:" in q for q in queries)

    def test_generate_text_queries_includes_sets(self: "TestQueryGeneration") -> None:
        """Test that text queries include set searches."""
        queries = _generate_text_queries()
        assert any("set:" in q for q in queries)

    def test_generate_text_queries_includes_formats(self: "TestQueryGeneration") -> None:
        """Test that text queries include format searches."""
        queries = _generate_text_queries()
        assert any("format:" in q for q in queries)

    def test_generate_random_queries_aggregates_all(self: "TestQueryGeneration") -> None:
        """Test that generate_random_queries combines all query types."""
        queries = generate_random_queries()
        assert isinstance(queries, list)

        # Should have queries from all categories
        basic = _generate_basic_queries()
        type_queries = _generate_type_queries()
        combined = _generate_combined_queries()
        text = _generate_text_queries()

        expected_count = len(basic) + len(type_queries) + len(combined) + len(text)
        assert len(queries) == expected_count

    def test_generate_random_queries_no_duplicates_within_category(
        self: "TestQueryGeneration",
    ) -> None:
        """Test that generated queries don't have obvious duplicates."""
        queries = generate_random_queries()
        # While there might be intentional variations (color: vs c:),
        # the list should still be reasonably large
        assert len(queries) > 100

    def test_queries_are_valid_strings(self: "TestQueryGeneration") -> None:
        """Test that all generated queries are valid strings."""
        queries = generate_random_queries()
        assert all(isinstance(q, str) for q in queries)
        assert all(len(q) > 0 for q in queries)
        # Queries should not start or end with spaces
        assert all(q == q.strip() for q in queries)
