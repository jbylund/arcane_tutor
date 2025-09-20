"""Tests for migration file ordering."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from api.api_resource import get_migrations


class TestMigrationOrdering:
    """Test migration file ordering and naming conventions."""

    def test_migrations_are_in_alphabetical_order(self) -> None:
        """Test that migration files are returned in alphabetical order."""
        migrations = get_migrations()

        # Extract just the filenames
        filenames = [migration["file_name"] for migration in migrations]

        # Check that the list is sorted
        assert filenames == sorted(filenames), (
            f"Migration files are not in alphabetical order. "
            f"Expected: {sorted(filenames)}, Got: {filenames}"
        )

    def test_migration_naming_convention(self) -> None:
        """Test that migration files follow the YYYY-MM-DD-description.sql naming convention."""
        migrations = get_migrations()

        # Pattern for YYYY-MM-DD-description.sql
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.sql$")

        for migration in migrations:
            filename = migration["file_name"]
            assert date_pattern.match(filename), (
                f"Migration file '{filename}' does not follow the "
                f"YYYY-MM-DD-description.sql naming convention"
            )

    def test_migration_dates_are_valid(self) -> None:
        """Test that migration files have valid dates in their names."""
        migrations = get_migrations()

        for migration in migrations:
            filename = migration["file_name"]
            # Extract date part (first 10 characters: YYYY-MM-DD)
            date_part = filename[:10]

            try:
                # Validate that the date is actually valid
                datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError as e:
                pytest.fail(f"Migration file '{filename}' has invalid date '{date_part}': {e}")

    def test_new_migrations_sort_after_existing_ones(self) -> None:
        """Test that any new migration files would sort after existing ones.

        This test validates that if someone were to add a new migration file today,
        it would properly sort after all existing migration files. It also checks
        that no existing migration has a date that would allow new migrations
        to be inserted out of order.
        """
        migrations = get_migrations()

        if not migrations:
            # If there are no migrations, this test is not applicable
            pytest.skip("No existing migrations found")

        # Get all migration filenames in sorted order
        filenames = sorted(migration["file_name"] for migration in migrations)

        # Check that all migrations appear to be in proper chronological order
        # by ensuring that no migration has a date older than what we'd expect for "old" migrations
        earliest_expected_date = "2024-01-01"  # Reasonable cutoff for this project's migrations

        for filename in filenames:
            date_part = filename[:10]  # Extract YYYY-MM-DD
            assert date_part >= earliest_expected_date, (
                f"Migration '{filename}' has date '{date_part}' which is suspiciously old. "
                f"This could indicate someone added a migration with an old date that would "
                f"be applied out of order."
            )

        # Get the latest migration filename
        latest_migration = filenames[-1]  # Last in sorted order

        # Generate a hypothetical "today" migration filename
        today = datetime.now(UTC)
        today_migration = f"{today.strftime('%Y-%m-%d')}-new-feature.sql"

        # Verify that a migration created today would sort after the latest existing one
        assert today_migration >= latest_migration, (
            f"A new migration created today ('{today_migration}') would not sort after "
            f"the latest existing migration ('{latest_migration}'). This suggests that "
            f"existing migration files have future dates, which could cause ordering issues."
        )

    def test_no_duplicate_migration_dates(self) -> None:
        """Test that migrations with the same date have different descriptions to maintain order."""
        migrations = get_migrations()

        # Group migrations by date
        date_groups: dict[str, list[str]] = {}
        for migration in migrations:
            filename = migration["file_name"]
            date_part = filename[:10]  # YYYY-MM-DD

            if date_part not in date_groups:
                date_groups[date_part] = []
            date_groups[date_part].append(filename)

        # Check that migrations with the same date are properly ordered
        for date, filenames in date_groups.items():
            if len(filenames) > 1:
                # Multiple migrations on the same date should be sorted by their full filename
                assert filenames == sorted(filenames), (
                    f"Migrations for date {date} are not in alphabetical order: {filenames}"
                )

    def test_get_migrations_function_exists_and_returns_expected_format(self) -> None:
        """Test that get_migrations function exists and returns data in expected format."""
        migrations = get_migrations()

        assert isinstance(migrations, list), "get_migrations should return a list"

        for migration in migrations:
            assert isinstance(migration, dict), "Each migration should be a dictionary"

            # Check required keys
            required_keys = {"file_name", "file_sha256", "file_contents"}
            assert required_keys <= migration.keys(), (
                f"Migration dictionary missing required keys. "
                f"Expected: {required_keys}, Got: {migration.keys()}"
            )

            # Validate data types
            assert isinstance(migration["file_name"], str), "file_name should be a string"
            assert isinstance(migration["file_sha256"], str), "file_sha256 should be a string"
            assert isinstance(migration["file_contents"], str), "file_contents should be a string"

            # Basic validation
            assert migration["file_name"].endswith(".sql"), "file_name should end with .sql"
            assert len(migration["file_sha256"]) == 64, "file_sha256 should be 64 characters (SHA256)"
            assert len(migration["file_contents"]) > 0, "file_contents should not be empty"


if __name__ == "__main__":
    pytest.main([__file__])
