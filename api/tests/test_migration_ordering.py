"""Tests for migration file ordering."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api.utils.db_utils import get_migrations


class TestMigrationOrdering:
    """Test migration file ordering and naming conventions."""

    def test_migration_filenames_start_with_isoformat_date(self) -> None:
        """Test that migration files start with a valid ISO format date (YYYY-MM-DD)."""
        migrations = get_migrations()

        for migration in migrations:
            filename = migration["file_name"]

            # Check that filename starts with YYYY-MM-DD format
            if len(filename) < 10:
                pytest.fail(f"Migration file '{filename}' is too short to contain a date")

            date_part = filename[:10]  # Extract first 10 characters: YYYY-MM-DD

            try:
                # Validate that the date is actually a valid ISO format date
                datetime.fromisoformat(date_part)
            except ValueError as e:
                pytest.fail(f"Migration file '{filename}' does not start with a valid ISO format date '{date_part}': {e}")

    def test_git_add_time_matches_filename_ordering(self) -> None:
        """Test that migrations were added to git in the same order as their filename sorting.

        This ensures that migrations were not only named correctly but were also added to git
        in chronological order, which provides additional confidence in the migration sequence.
        """
        migrations = get_migrations()

        if not migrations:
            pytest.skip("No existing migrations found")

        # Get migration filenames sorted alphabetically
        filenames = sorted(migration["file_name"] for migration in migrations)

        # Get git add times for each migration file
        migration_git_times = []
        repo_root = Path(__file__).parent.parent.parent  # Go up to repo root

        for filename in filenames:
            file_path = repo_root / "api" / "db" / filename

            try:
                # Get the first commit that added this file (oldest commit)
                result = subprocess.run(  # noqa: S603 - safe git command with known args
                    ["git", "log", "--follow", "--format=%ai", "--reverse", "--", str(file_path)],  # noqa: S607
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )

                if result.stdout.strip():
                    # Take the first (oldest) timestamp when file was added
                    git_time_str = result.stdout.strip().split("\n")[0]
                    # Parse git timestamp format: "2025-09-20 01:58:15 -0400"
                    git_time = datetime.strptime(git_time_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                    migration_git_times.append((git_time, filename))
                else:
                    pytest.skip(f"Could not determine git add time for {filename}")

            except subprocess.CalledProcessError as e:
                pytest.skip(f"Git command failed for {filename}: {e}")

        # Sort by (git_time, filename) - this is what @jbylund requested
        git_time_sorted = [filename for _, filename in sorted(migration_git_times)]

        # Compare with filename-only sorting
        filename_sorted = sorted(filenames)

        # Assert that both orderings are the same
        assert git_time_sorted == filename_sorted, (
            f"Migration files were not added to git in the same order as their filename sorting. "
            f"Git add time order: {git_time_sorted}\n"
            f"Filename order: {filename_sorted}\n"
            f"This suggests migrations may have been added out of chronological order."
        )


if __name__ == "__main__":
    pytest.main([__file__])
