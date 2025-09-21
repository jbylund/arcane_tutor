"""Database utility functions for the API."""

import hashlib
import logging
import os
import pathlib

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


def make_pool() -> psycopg_pool.ConnectionPool:
    """Create and return a psycopg3 ConnectionPool for PostgreSQL connections."""

    def configure_connection(conn: psycopg.Connection) -> None:
        conn.row_factory = psycopg.rows.dict_row

    creds = get_pg_creds()
    conninfo = " ".join(f"{k}={v}" for k, v in creds.items())
    pool_args = {
        "configure": configure_connection,
        "conninfo": conninfo,
        "max_size": 2,
        "min_size": 1,
        "open": True,
    }
    logger.info("Pool args: %s", pool_args)
    return psycopg_pool.ConnectionPool(**pool_args)


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
