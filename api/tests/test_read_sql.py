"""Tests for db_utils.read_sql and the SQL files it loads."""

from __future__ import annotations

import pathlib

import pytest

from api.utils import db_utils

# Rejected stems. The guard is not just cosmetic: read_sql joins its argument onto a directory, so a
# value with path components would escape it. ".." and "" are called out because `Path(x).name == x`
# is true for both, so a name-only check would let them through.
bad_stem_ids = {
    "parent_traversal": "../db/2025-09-29-great-reset",
    "bare_parent": "..",
    "bare_dot": ".",
    "empty": "",
    "nested_path": "a/b",
    "absolute": "/etc/passwd",
    "dot_slash_prefix": "./get_cards",
}


class TestReadSQL:
    """Test read_sql and SQL file integration."""

    def test_loads_correct_file(self) -> None:
        """Test read_sql returns the requested file's contents."""
        sql_content = db_utils.read_sql("get_cards")
        assert "SELECT" in sql_content
        assert "FROM" in sql_content
        assert "magic.cards" in sql_content
        assert "card_name" in sql_content

    def test_memoized(self) -> None:
        """Test repeated reads are served from the memo rather than the filesystem."""
        db_utils.read_sql.cache_clear()
        first = db_utils.read_sql("get_cards")
        second = db_utils.read_sql("get_cards")
        assert first == second
        info = db_utils.read_sql.cache_info()
        assert (info.hits, info.misses) == (1, 1)

    def test_file_not_found(self) -> None:
        """Test a valid stem with no matching file still raises."""
        with pytest.raises(FileNotFoundError):
            db_utils.read_sql("nonexistent_query")

    @pytest.mark.parametrize(
        argnames=["filename"],
        argvalues=[[bad_stem_ids[name]] for name in sorted(bad_stem_ids)],
        ids=sorted(bad_stem_ids),
    )
    def test_rejects_non_bare_stems(self, filename: str) -> None:
        """Test anything other than a bare stem is rejected instead of joined onto the directory."""
        with pytest.raises(ValueError, match="bare file stem"):
            db_utils.read_sql(filename)

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
            sql_content = db_utils.read_sql(filename)
            assert sql_content.strip(), f"SQL file {filename} should have non-empty content"


if __name__ == "__main__":
    pytest.main([__file__])
