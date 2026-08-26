"""Keep compose validation tests free of the DB testcontainer autouse fixture."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", name="postgres_container", autouse=True)
def postgres_container() -> None:
    """Override the root autouse PostgresContainer for scripts-only tests."""
