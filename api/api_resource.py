"""Implementation of the routes of our simple api."""

from __future__ import annotations

import collections
import copy
import csv
import hashlib
import inspect
import io
import json
import logging
import os
import pathlib
import time
from typing import Any
from urllib.parse import urlparse

import falcon
import orjson
import psycopg
import psycopg.rows
import psycopg.types.json
import psycopg_pool
import requests
from cachetools import LRUCache, cached
from parsing import generate_sql_query, parse_scryfall_query


def orjson_dumps(obj: object) -> str:
    """Dump an object to a string using orjson."""
    return orjson.dumps(obj).decode("utf-8")

# Register for dumping (adapting Python -> DB)
psycopg.types.json.set_json_dumps(dumps=orjson_dumps)
psycopg.types.json.set_json_loads(loads=orjson.loads)

logger = logging.getLogger("apiresource")

# pylint: disable=c-extension-no-member

@cached(cache=LRUCache(maxsize=10_000))
def get_where_clause(query: str) -> str:
    parsed_query = parse_scryfall_query(query)
    return generate_sql_query(parsed_query)

def rewrap(query: str) -> str:
    return " ".join(query.strip().split())

def _get_pg_creds() -> dict[str, str]:
    """Get postgres credentials from the environment."""
    mapping = {
        "database": "dbname",
    }
    unmapped = {k[2:].lower(): v for k, v in os.environ.items() if k.startswith("PG")}
    return {mapping.get(k, k): v for k, v in unmapped.items()}

def _make_pool() -> psycopg_pool.ConnectionPool:
    """Create and return a psycopg3 ConnectionPool for PostgreSQL connections."""

    def configure_connection(conn: psycopg.Connection) -> None:
        conn.row_factory = psycopg.rows.dict_row

    creds = _get_pg_creds()
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
    here = pathlib.Path(__file__).parent
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


def can_serialize(iobj: object) -> bool:
    """Check if an object is JSON serializable and not too large.

    Args:
    ----
        iobj (object): The object to check.

    Returns:
    -------
        bool: True if serializable and not too large, False otherwise.

    """
    max_json_object_length = 16_000
    try:
        s = json.dumps(iobj)
        return len(s) < max_json_object_length
    except TypeError:
        return False
    return True


class APIResource:
    """Class implementing request handling for our simple API."""

    def __init__(self: APIResource) -> None:
        """Initialize an APIResource object, set up connection pool and action
        map.
        """
        self._tablename = "staging_table"

        # make a psycopg3 connection pool
        self._conn_pool = _make_pool()
        self.action_map = {x: getattr(self, x) for x in dir(self) if not x.startswith("_")}
        self.action_map["index"] = self.index_html
        self._query_cache = LRUCache(maxsize=1_000)
        logger.info("Worker with pid has conn pool %s", self._conn_pool)

    def _handle(self: APIResource, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle a Falcon request and set the response.

        Args:
        ----
            req (falcon.Request): The incoming request.
            resp (falcon.Response): The outgoing response.

        """
        parsed = urlparse(req.uri)
        path = parsed.path.strip("/")

        if path not in ("db_ready", "pid"):
            logger.info("Handling request for %s", req.uri)

        path = path.replace(".", "_")
        action = self.action_map.get(path, self._raise_not_found)
        before = time.monotonic()
        try:
            res = action(falcon_response=resp, **req.params)
            resp.media = res
        except TypeError as oops:
            logger.error("Error handling request: %s", oops, exc_info=True)
            raise falcon.HTTPBadRequest(description=str(oops))
        except falcon.HTTPError:
            raise
        except Exception as oops:
            logger.error("Error handling request: %s", oops, exc_info=True)
            # walk back to the lowest frame...
            # file / function / locals (if possible)
            stack_info = []
            for iframe in inspect.trace()[1:]:
                stack_info.append(
                    {
                        "file": iframe.filename,
                        "function": iframe.function,
                        "line_no": iframe.lineno,
                        "locals": {k: v for k, v in iframe.frame.f_locals.items() if can_serialize(v)},
                    },
                )

            raise falcon.HTTPInternalServerError(
                title="Server Error",
                description={
                    "exception": str(oops),
                    "stack_info": stack_info,
                },
            )
        finally:
            duration = time.monotonic() - before
            logger.info("Request duration: %f seconds / %s", duration, resp.status)

    def _raise_not_found(self: APIResource, **_: object) -> None:
        """Raise a Falcon HTTPNotFound error with available routes."""
        raise falcon.HTTPNotFound(title="Not Found", description={"routes": {k: v.__doc__ for k, v in self.action_map.items()}})

    def _run_query(
        self: APIResource,
        *,
        query: str,
        params: dict[str, Any] | None = None,
        explain: bool = True,
    ) -> dict[str, Any]:
        """Run a SQL query with optional parameters and explanation.

        Args:
        ----
            query (str): The SQL query to run.
            params (Optional[Dict[str, Any]]): Query parameters.
            explain (bool): Whether to run EXPLAIN on the query.

        Returns:
        -------
            Dict[str, Any]: Query result and metadata.

        """
        cachekey = (
            query,
            frozenset((params or {}).items()),
            explain,
        )
        cached_val = self._query_cache.get(cachekey)
        if cached_val is not None:
            return copy.deepcopy(cached_val)

        params = params or {}
        query = " ".join(query.strip().split())
        explain_query = f"EXPLAIN (FORMAT JSON) {query}"

        result: dict[str, Any] = {}
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute("set statement_timeout = 10000")
            if explain:
                cursor.execute(explain_query, params)
                for row in cursor.fetchall():
                    result["plan"] = row

            result["timings"] = timings = {}
            before = time.time()
            cursor.execute(query, params)
            after_query = time.time()
            timings["query_duration"] = query_duratrion = after_query - before
            timings["query_duration_ms"] = query_duratrion * 1000
            timings["query_frequency"] = 1 / query_duratrion
            raw_rows = cursor.fetchall()
            result["result"] = [dict(r) for r in raw_rows]
            after_fetch = time.time()
            timings["fetch_duration"] = fetch_duration = after_fetch - after_query
            timings["fetch_duration_ms"] = fetch_duration * 1000
            timings["fetch_frequency"] = 1 / fetch_duration
            timings["total_duration"] = total_duration = after_fetch - before
            timings["total_duration_ms"] = total_duration * 1000
            timings["total_frequency"] = 1 / total_duration
        self._query_cache[cachekey] = result
        return copy.deepcopy(result)

    def get_pid(self: APIResource, **_: object) -> int:
        """Just return the pid of the process which served this request.

        Returns:
        -------
            int: The process ID.

        """
        return os.getpid()

    def db_ready(self: APIResource, **_: object) -> bool:
        """Return true if the db is ready.

        Returns:
        -------
            bool: True if the database is ready, False otherwise.

        """
        records = self._run_query(
            query="SELECT relname FROM pg_stat_user_tables",
        )["result"]
        existing_tables = {r["relname"] for r in records}
        return "migrations" in existing_tables

    def get_data(self: APIResource) -> Any:
        """Retrieve card data from cache or Scryfall API.

        Returns:
        -------
            Any: The card data (likely a list of dicts).

        """
        cache_file = "/data/api/foo.json"
        try:
            with pathlib.Path(cache_file).open() as f:
                response = json.load(f)
        except FileNotFoundError:
            logger.info("Cache miss!")
            session = requests.Session()
            response = session.get("https://api.scryfall.com/bulk-data", timeout=1).json()["data"]
            by_type = {r["type"]: r for r in response}
            oracle_cards_download_uri = by_type["oracle_cards"]["download_uri"]
            response = requests.get(oracle_cards_download_uri, timeout=30).json()
            with pathlib.Path(cache_file).open("w") as f:
                json.dump(response, f, indent=4, sort_keys=True)
        else:
            logger.info("Cache hit!")
        return response

    def _setup_schema(self: APIResource) -> None:
        """Set up the database schema and apply migrations as needed."""
        # read migrations from the db dir...
        # if any already applied migrations differ from what we want
        # to apply then drop everything
        with self._conn_pool.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS migrations (
                    file_name text not null,
                    file_sha256 text not null,
                    date_applied timestamp default now(),
                    file_contents text not null
                )""",
                )
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_migrations_filename ON migrations (file_name)")
                cursor.execute("CREATE        INDEX IF NOT EXISTS idx_migrations_file_sha256 ON migrations USING HASH (file_sha256)")

                cursor.execute("SELECT file_name, file_sha256 FROM migrations ORDER BY date_applied")
                applied_migrations = [dict(r) for r in cursor]
                filesystem_migrations = get_migrations()

                already_applied = set()
                for applied_migration, fs_migration in zip(applied_migrations, filesystem_migrations, strict=False):
                    if applied_migration.items() <= fs_migration.items():
                        already_applied.add(applied_migration["file_sha256"])
                    else:
                        already_applied.clear()
                        cursor.execute("DELETE FROM migrations")
                        cursor.execute("DROP SCHEMA IF EXISTS magic CASCADE")

                for imigration in filesystem_migrations:
                    file_sha256 = imigration["file_sha256"]
                    if file_sha256 in already_applied:
                        logger.info("%s was already applied...", imigration["file_name"])
                        continue
                    logger.info("Applying %s ...", imigration["file_name"])
                    cursor.execute(imigration["file_contents"])
                    cursor.execute(
                        """
                        INSERT INTO migrations
                            (  file_name  ,   file_sha256  ,   file_contents  ) VALUES
                            (%(file_name)s, %(file_sha256)s, %(file_contents)s)""",
                        imigration,
                    )

    def _get_cards_to_insert(self: APIResource) -> list[dict[str, Any]]:
        """Get the cards to insert into the database."""
        all_cards = self.get_data()
        to_insert = []
        for card in all_cards:
            if set(card["legalities"].values()) == {"not_legal"}:
                continue
            if "card_faces" in card:
                continue
            if card.get("set_type") == "funny":
                continue
            card_types, _, card_subtypes = (x.strip().split() for x in card.get("type_line", "").title().partition("\u2014"))
            card["card_types"] = card_types
            card["card_subtypes"] = card_subtypes or None
            for creature_field in ["power", "toughness"]:
                val = card.setdefault(creature_field, None)
                try:
                    numeric_val = int(val)
                except (TypeError, ValueError):
                    card[f"{creature_field}_numeric"] = None
                else:
                    card[f"{creature_field}_numeric"] = numeric_val
            card["card_colors"] = {c: True for c in card["colors"]}
            to_insert.append(card)
        return to_insert

    def get_stats(self: APIResource, **_: object) -> dict[str, Any]:
        """Get stats about the cards."""
        to_insert = self._get_cards_to_insert()
        key_frequency = collections.Counter()
        for card in to_insert:
            key_frequency.update(k for k, v in card.items() if v not in [None, [], {}])
        return key_frequency.most_common()

    def import_data(self: APIResource, **_: object) -> None:
        """Import data from Scryfall and insert into the database."""
        self._setup_schema()
        to_insert = self._get_cards_to_insert()

        # card_name | cmc | mana_cost_text | mana_cost_jsonb | raw_card_blob | card_types | card_subtypes | card_colors | creature_power | creature_power_text | creature_toughness | creature_toughness_text
        # type_line	: "Legendary Creature — Elf Druid"
        last_log = 0
        log_interval = 1
        import_times = collections.deque(maxlen=1000)

        from psycopg.types.json import Jsonb
        def maybe_json(v: Any) -> Any:
            if isinstance(v, list | dict):
                return Jsonb(v)
            return v

        with self._conn_pool.connection() as conn:
            with conn.cursor() as cursor:
                for idx, card in enumerate(to_insert):
                    now = time.monotonic()
                    import_times.append(now)
                    if log_interval < now - last_log:
                        try:
                            rate = len(import_times) / (now - import_times[0])
                        except ZeroDivisionError:
                            rate = 0
                        logger.info("Imported %d cards, current rate: %d cards/s...", idx, rate)
                        last_log = now

                    card_with_json = {
                        k: maybe_json(v) for k, v in card.items()
                    }
                    cursor.execute(
                        """
                        INSERT INTO magic.cards
                        ( card_name,   cmc  , mana_cost_text, mana_cost_jsonb, raw_card_blob,     card_types,   card_subtypes  , card_colors, creature_power, creature_power_text, creature_toughness, creature_toughness_text ) VALUES
                        ( %(name)s , %(cmc)s, %(mana_cost)s ,            null,      %(blob)s, %(card_types)s, %(card_subtypes)s,     %(card_colors)s,     %(power_numeric)s,             %(power)s,             %(toughness_numeric)s   ,   %(toughness)s)
                        ON CONFLICT (card_name) DO NOTHING
                        """,
                        card_with_json | {"blob": Jsonb(card)},
                    )

    def get_cards(self: APIResource, *, min_name: str | None = None, max_name: str | None = None, limit: int = 2500, **_: object) -> list[dict[str, Any]]:
        """Get cards by name range.

        Args:
        ----
            min_name (Optional[str]): Minimum card name.
            max_name (Optional[str]): Maximum card name.
            limit (int): Maximum number of cards to return.

        Returns:
        -------
            List[Dict[str, Any]]: List of card records.

        """
        return self._run_query(
            query="""
            SELECT
                *
            FROM
                magic.cards
            WHERE
                (%(min_name)s::text IS NULL OR %(min_name)s::text < card_name) AND
                (%(max_name)s::text IS NULL OR card_name < %(max_name)s::text)
            ORDER BY
                card_name
            LIMIT
                %(limit)s
            """,
            params={
                "min_name": min_name,
                "max_name": max_name,
                "limit": limit,
            },
        )["result"]

    def get_cards_to_csv(self: APIResource, *, min_name: str | None = None, max_name: str | None = None, limit: int = 2500, falcon_response: falcon.Response | None = None) -> None:
        """Write cards as CSV to the Falcon response.

        Args:
        ----
            min_name (Optional[str]): Minimum card name.
            max_name (Optional[str]): Maximum card name.
            limit (int): Maximum number of cards to return.
            falcon_response (falcon.Response): The Falcon response to write to.

        Raises:
        ------
            ValueError: If falcon_response is not provided.

        """
        if falcon_response is None:
            msg = "falcon_response is required"
            raise ValueError(msg)
        raw_cards = self.get_cards(min_name=min_name, max_name=max_name, limit=limit)
        falcon_response.content_type = "text/csv"

        str_buffer = io.StringIO()
        writer = csv.DictWriter(str_buffer, fieldnames=raw_cards[0].keys())
        writer.writeheader()
        writer.writerows(raw_cards)
        str_buffer.seek(0)
        val = str_buffer.getvalue()
        falcon_response.body = val.encode("utf-8")

    def search(self: APIResource, *, q: str | None = None, query: str | None = None, limit: int = 100, **_: object) -> dict[str, Any]:
        """Run a search query and return results and metadata.

        Args:
        ----
            q (Optional[str]): Query string.
            query (Optional[str]): Query string.

        Returns:
        -------
            Dict[str, Any]: Search results and metadata.

        """
        query = query or q
        where_clause = get_where_clause(query)
        full_query = f"""
        SELECT
            card_name AS name,
            mana_cost_text AS mana_cost,
            raw_card_blob->>'oracle_text' AS oracle_text,
            raw_card_blob->>'set_name' AS set_name,
            raw_card_blob->>'type_line' AS type_line,
            raw_card_blob->'image_uris'->>'normal' AS image,
            COUNT(1) OVER() AS total_cards
        FROM
            magic.cards AS card
        WHERE
            {where_clause}
        ORDER BY
            raw_card_blob->>'edhrec_rank' ASC
        LIMIT
            {limit}
        """
        full_query = rewrap(full_query)
        logger.info("Full query: %s", full_query)
        result_bag = self._run_query(query=full_query)
        cards = result_bag.pop("result", [])
        total_cards = 0
        for icard in cards:
            total_cards = icard.pop("total_cards")
        return {
            "cards": cards,
            "compiled": full_query,
            # "parsed": str(parsed_query),
            "query": query,
            "result": result_bag,
            "total_cards": total_cards,
        }

    def index_html(self: APIResource, *, falcon_response: falcon.Response | None = None, **_: object) -> None:
        """Return the index page.

        Args:
        ----
            falcon_response (falcon.Response): The Falcon response to write to.

        """
        self._serve_static_file(filename="index.html", falcon_response=falcon_response)
        falcon_response.content_type = "text/html"

    def search_js(self: APIResource, *, falcon_response: falcon.Response | None = None) -> None:
        """Return the search.js file.

        Args:
        ----
            falcon_response (falcon.Response): The Falcon response to write to.

        """
        self._serve_static_file(filename="search.js", falcon_response=falcon_response)
        falcon_response.content_type = "text/javascript"

    def favicon_ico(self: APIResource, *, falcon_response: falcon.Response | None = None) -> None:
        """Return the favicon.ico file.

        Args:
        ----
            falcon_response (falcon.Response): The Falcon response to write to.
        """
        full_filename = pathlib.Path(__file__).parent / "favicon.ico"
        with pathlib.Path(full_filename).open(mode="rb") as f:
            falcon_response.data = contents = f.read()
        falcon_response.content_type = "image/vnd.microsoft.icon"
        content_length = len(contents)
        logger.info("Favicon content length: %d", content_length)
        falcon_response.headers["content-length"] = content_length

    def _serve_static_file(self: APIResource, *, filename: str, falcon_response: falcon.Response) -> None:
        """Serve a static file to the Falcon response.

        Args:
        ----
            filename (str): The file to serve.
            falcon_response (falcon.Response): The Falcon response to write to.

        """
        full_filename = pathlib.Path(__file__).parent / filename
        with pathlib.Path(full_filename).open() as f:
            falcon_response.text = f.read()
