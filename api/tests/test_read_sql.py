"""Tests for the read_sql method functionality."""

from __future__ import annotations

import pathlib

import pytest

from api.api_resource import APIResource
from api.settings import settings


class TestReadSQL:
    """Test the read_sql method and SQL file integration."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource()

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        if hasattr(self, "api_resource") and self.api_resource:
            # Close the connection pool to prevent thread pool warnings
            self.api_resource._conn_pool.close()

    def test_read_sql_method_exists(self) -> None:
        """Test that the read_sql method exists."""
        assert hasattr(self.api_resource, "read_sql")
        assert callable(self.api_resource.read_sql)

    def test_read_sql_caching_enabled(self) -> None:
        """Test that the read_sql method is cached when caching is enabled."""
        # Enable caching
        original_setting = settings.enable_cache
        try:
            settings.enable_cache = True
            # Verify that the method has a cache attribute (from @cached decorator)
            assert hasattr(self.api_resource.read_sql, "cache")
        finally:
            settings.enable_cache = original_setting

    def test_read_sql_caching_disabled(self) -> None:
        """Test that the read_sql method still works when caching is disabled."""
        # Disable caching
        original_setting = settings.enable_cache
        try:
            settings.enable_cache = False
            # Method should still have cache attribute but won't use it
            assert hasattr(self.api_resource.read_sql, "cache")
            # Verify method still works
            sql_content = self.api_resource.read_sql("get_cards")
            assert "SELECT" in sql_content
        finally:
            settings.enable_cache = original_setting

    def test_read_sql_loads_correct_file(self) -> None:
        """Test that read_sql loads the correct SQL file content."""
        # Test loading one of our actual SQL files
        sql_content = self.api_resource.read_sql("get_cards")

        # Verify it contains expected SQL keywords
        assert "SELECT" in sql_content
        assert "FROM" in sql_content
        assert "magic.cards" in sql_content
        assert "card_name" in sql_content

    def test_read_sql_file_not_found(self) -> None:
        """Test that read_sql raises appropriate error for missing files."""
        with pytest.raises(FileNotFoundError):
            self.api_resource.read_sql("nonexistent_query")

    def test_sql_files_exist(self) -> None:
        """Test that all expected SQL files exist."""
        sql_dir = pathlib.Path(__file__).parent.parent / "sql"

        expected_files = [
            "get_cards.sql",
            "get_common_card_types.sql",
            "get_common_keywords.sql",
        ]

        for filename in expected_files:
            sql_file = sql_dir / filename
            assert sql_file.exists(), f"SQL file {filename} should exist"
            assert sql_file.is_file(), f"SQL file {filename} should be a file"

    def test_sql_files_have_content(self) -> None:
        """Test that SQL files have non-empty content."""
        expected_files = [
            "get_cards",
            "get_common_card_types",
            "get_common_keywords",
        ]

        for filename in expected_files:
            sql_content = self.api_resource.read_sql(filename)
            assert sql_content, f"SQL file {filename} should have content"
            assert len(sql_content.strip()) > 0, f"SQL file {filename} should have non-empty content"


if __name__ == "__main__":
    pytest.main([__file__])
