"""Fixtures for the test suite."""
from __future__ import annotations

import os
import random
from typing import TYPE_CHECKING

import logging
import pytest
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from collections.abc import Generator

logging.basicConfig(
    force=True,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

@pytest.fixture(scope="session", name="postgres_container", autouse=True)
def postgres_container_fixture() -> Generator[None]:
    """Fixture to start and stop a postgres container for the session."""
    exposed_port = random.randint(1024, 49151)  # noqa: S311
    container = PostgresContainer(
        image="postgres:18rc1",
        username="testuser",
        password="testpass",  # noqa: S106
        dbname="testdb",
    ).with_bind_ports(5432, exposed_port)
    container.start()
    os.environ.update({
        "PGDATABASE": "testdb",
        "PGHOST": container.get_container_host_ip(),
        "PGPASSWORD": "testpass",
        "PGPORT": str(container.get_exposed_port(5432)),
        "PGUSER": "testuser",
    })
    yield
    container.stop()
