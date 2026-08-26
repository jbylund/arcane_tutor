"""Fixtures for the test suite."""

from __future__ import annotations

if True:
    import warnings

    warnings.filterwarnings("ignore", category=DeprecationWarning)

import logging
import os
import random
import time
from typing import TYPE_CHECKING

# Ryuk is for orphan cleanup when a process dies without stopping containers; the session
# fixture always calls container.stop(), so skip the extra sidecar pull/start in CI.
os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from api.settings import settings

if TYPE_CHECKING:
    from collections.abc import Generator


def _wait_for_database_ready(host: str, port: str, timeout: int = 30) -> None:
    """Poll until the testcontainer accepts connections."""
    connection_params = {
        "host": host,
        "port": port,
        "dbname": "testdb",
        "user": "testuser",
        "password": "testpass",
    }
    start = time.time()
    while time.time() - start < timeout:
        try:
            with psycopg.connect(**connection_params) as conn, conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return
        except (psycopg.Error, OSError):
            time.sleep(0.5)
    msg = f"Database not ready within {timeout} seconds"
    raise RuntimeError(msg)


logging.basicConfig(
    force=True,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


@pytest.fixture(scope="session", name="postgres_container")
def postgres_container_fixture() -> Generator[None]:
    """Start one session-scoped postgres container and export PG* for tests that request it."""
    exposed_port = random.randint(1024, 49151)
    container = PostgresContainer(
        image="postgres:18",
        username="testuser",
        password="testpass",  # noqa: S106
        dbname="testdb",
    ).with_bind_ports(5432, exposed_port)
    container.start()
    host = container.get_container_host_ip()
    port = str(container.get_exposed_port(5432))
    _wait_for_database_ready(host, port)
    os.environ.update(
        {
            "PGDATABASE": "testdb",
            "PGHOST": host,
            "PGPASSWORD": "testpass",
            "PGPORT": port,
            "PGUSER": "testuser",
        },
    )
    yield
    container.stop()


@pytest.fixture
def enable_cache() -> None:
    """Fixture to enable caching for specific tests."""
    original_setting = settings.enable_cache
    settings.enable_cache = True
    yield
    settings.enable_cache = original_setting
