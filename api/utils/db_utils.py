"""Database utility functions for the API."""

import atexit
import hashlib
import logging
import os
import pathlib
import random
import sys

import orjson
import psycopg
import psycopg.types.json
import psycopg_pool

logger = logging.getLogger(__name__)


def get_pg_creds() -> dict[str, str]:
    """Get postgres credentials from the environment."""
    mapping = {
        "database": "dbname",
    }
    unmapped = {k[2:].lower(): v for k, v in os.environ.items() if k.startswith("PG")}
    return {mapping.get(k, k): v for k, v in unmapped.items()}

def get_testcontainers_creds() -> dict[str, str]:
    """Get postgres credentials from the testcontainers environment."""
    logger.warning("Using an ephemeral postgres container...")
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415
    exposed_port = random.randint(1024, 49151)  # noqa: S311
    container = PostgresContainer(
        image="postgres:18rc1",
        username="testuser",
        password="testpass",  # noqa: S106
        dbname="testdb",
    ).with_bind_ports(5432, exposed_port)
    container.start()
    return {
        "dbname": "testdb",
        "host": container.get_container_host_ip(),
        "password": "testpass",
        "port": container.get_exposed_port(5432),
        "user": "testuser",
    }


def configure_connection(conn: psycopg.Connection) -> None:
    """Configure a connection to use dict_row as the row factory."""
    conn.row_factory = psycopg.rows.dict_row

def make_pool() -> psycopg_pool.ConnectionPool:
    """Create and return a psycopg3 ConnectionPool for PostgreSQL connections."""
    creds = get_pg_creds()
    if not creds:
        creds = get_testcontainers_creds()
    conninfo = " ".join(f"{k}={v}" for k, v in creds.items())
    pool_args = {
        "configure": configure_connection,
        "conninfo": conninfo,
        "max_size": 2,
        "min_size": 1,
        "open": True,
    }
    logger.info("Pool args: %s", pool_args)
    pool = psycopg_pool.ConnectionPool(**pool_args)

    def cleanup() -> None:
        # The logger may be shut down during interpreter exit
        # so we use stderr instead
        sys.stderr.write(f"Closing connection pool in pid {os.getpid()}\n")
        pool.close()
        sys.stderr.write(f"Connection pool closed in pid {os.getpid()}\n")

    atexit.register(cleanup)
    return pool


def get_migrations() -> list[dict[str, str]]:
    """Get the migrations from the filesystem.

    Returns:
    -------
        List[Dict[str, str]]: List of migration metadata dictionaries.

    """
    # generate migrations + their hashes
    here = pathlib.Path(__file__).parent.parent
    migrations_dir = here / "db"
    migrations = []
    for dirname, _, child_files in migrations_dir.walk():
        for ichild in sorted(child_files):
            if not ichild.lower().endswith(".sql"):
                continue
            fullpath = dirname / ichild
            with pathlib.Path(fullpath).open() as filehandle:
                contents = filehandle.read().strip()
            migrations.append(
                {
                    "file_contents": contents,
                    "file_sha256": hashlib.sha256(contents.encode()).hexdigest(),
                    "file_name": ichild,
                },
            )
    return migrations

def maybe_json(v: object) -> object:
    """Wrap a value in a Jsonb object if it is a list or dict."""
    if isinstance(v, list | dict):
        return psycopg.types.json.Jsonb(v)
    return v


def orjson_dumps(obj: object) -> str:
    """Dump an object to a string using orjson."""
    return orjson.dumps(obj).decode("utf-8")

# Register for dumping (adapting Python -> DB)
psycopg.types.json.set_json_dumps(dumps=orjson_dumps)
psycopg.types.json.set_json_loads(loads=orjson.loads)
