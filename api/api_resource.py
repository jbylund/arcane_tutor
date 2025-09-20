"""Implementation of the routes of our simple api."""

from __future__ import annotations

import collections
import copy
import csv
import datetime
import hashlib
import inspect
import io
import itertools
import json
import logging
import os
import pathlib
import random
import re
import secrets
import time
import urllib.parse
from typing import Any
from typing import cast as typecast
from urllib.parse import urlparse

import falcon
import orjson
import psycopg
import psycopg.rows
import psycopg.types.json
import psycopg_pool
import requests
from cachetools import LRUCache, TTLCache, cached
from psycopg import Connection

from .parsing import generate_sql_query, parse_scryfall_query
from .tagger_client import TaggerClient

try:
    from honeybadger import honeybadger
except ImportError:
    def honeybadger_error_handler(req: falcon.Request, oops: Exception) -> None:
        """Handle an error with Honeybadger."""
        del req
        logger.error("Error handling request: %s", oops, exc_info=True)
else:
    honeybadger.configure(
        api_key="hbp_mHbJs4KJAOeUhK17Ixr0AzDC0gx8Zt2WG6kH",
        project_root=str(pathlib.Path(__file__).parent.parent),
    )

    def honeybadger_error_handler(req: falcon.Request, oops: Exception) -> None:
        """Handle an error with Honeybadger."""
        logger.error("Error handling request: %s", oops, exc_info=True)
        honeybadger.notify(
            exception=oops,
            context={
                "headers": req.headers,
                "method": req.method,
                "params": req.params,
                "path": req.path,
                "query_string": req.query_string,
                "uri": req.uri,
            },
        )


def orjson_dumps(obj: object) -> str:
    """Dump an object to a string using orjson."""
    return orjson.dumps(obj).decode("utf-8")


# Register for dumping (adapting Python -> DB)
psycopg.types.json.set_json_dumps(dumps=orjson_dumps)
psycopg.types.json.set_json_loads(loads=orjson.loads)

logger = logging.getLogger("apiresource")

# pylint: disable=c-extension-no-member

NOT_FOUND = 404

@cached(cache=LRUCache(maxsize=10_000))
def get_where_clause(query: str) -> tuple[str, dict]:
    """Generate SQL WHERE clause and parameters from a search query.

    Args:
        query: The search query string to parse.

    Returns:
        Tuple of (SQL WHERE clause, parameter dictionary).
    """
    parsed_query = parse_scryfall_query(query)
    return generate_sql_query(parsed_query)


def rewrap(query: str) -> str:
    """Normalize whitespace in a SQL query string.

    Args:
        query: The SQL query string to normalize.

    Returns:
        The query with normalized whitespace.
    """
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
        """Initialize an APIResource object, set up connection pool and action map.

        Sets up the database connection pool and action mapping for the API.
        """
        self._tablename = "staging_table"

        # make a psycopg3 connection pool
        self._conn_pool: psycopg_pool.ConnectionPool = _make_pool()
        self.action_map = {x: getattr(self, x) for x in dir(self) if not x.startswith("_")}
        self.action_map["index"] = self.index_html
        self._query_cache = LRUCache(maxsize=1_000)
        # Create reusable requests session
        self._session = requests.Session()
        version = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d")
        version = f"magic-api/{version}"
        self._session.headers.update({"User-Agent": version})
        # Initialize Tagger client for GraphQL API access
        self._tagger_client = TaggerClient()
        logger.info("Worker with pid has conn pool %s", self._conn_pool)

    @cached(cache={}, key=lambda _self, filename: filename)
    def read_sql(self: APIResource, filename: str) -> str:
        """Read SQL content from a file with caching.

        Args:
            filename: The name of the SQL file (without .sql extension)

        Returns:
            The SQL content as a string
        """
        sql_dir = pathlib.Path(__file__).parent / "sql"
        sql_file = sql_dir / f"{filename}.sql"

        with sql_file.open(encoding="utf-8") as f:
            return f.read().strip()

    def _handle(self: APIResource, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle a Falcon request and set the response.

        Args:
        ----
            req (falcon.Request): The incoming request.
            resp (falcon.Response): The outgoing response.

        """
        if resp.complete:
            logger.info("Request already handled: %s", req.uri)
            return

        parsed = urlparse(req.uri)
        path = parsed.path.strip("/") or "index"

        if path in ("db_ready", "pid"):
            return

        logger.info(
            "Handling request for %s / |%s| / response id: %d",
            req.uri,
            path,
            id(resp),
        )
        path = path.replace(".", "_")
        action = self.action_map.get(path, self._raise_not_found)
        before = time.monotonic()
        try:
            res = action(falcon_response=resp, **req.params)
            resp.media = res
        except TypeError as oops:
            logger.error("Error handling request: %s", oops, exc_info=True)
            raise falcon.HTTPBadRequest(description=str(oops)) from oops
        except falcon.HTTPError:
            raise
        except Exception as oops:
            logger.error("Error handling request: %s", oops, exc_info=True)
            honeybadger_error_handler(req, oops)
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
            ) from oops
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
        params = params or {}

        use_cache = True
        if use_cache:

            def maybe_json_dump(v: object) -> object:
                if isinstance(v, list | dict):
                    return json.dumps(v, sort_keys=True)
                return v

            # need to make params hashable... but it might contain dicts/lists/...
            hashable_params = {k: maybe_json_dump(v) for k, v in params.items()}
            cachekey = (
                query,
                frozenset(hashable_params.items()),
                explain,
            )
            cached_val = self._query_cache.get(cachekey)
            if cached_val is not None:
                return copy.deepcopy(cached_val)

        # wrap params in json
        def maybe_json(v: object) -> object:
            if isinstance(v, list | dict):
                return psycopg.types.json.Jsonb(v)
            return v

        params = {k: maybe_json(v) for k, v in params.items()}
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
            before = time.monotonic()
            cursor.execute(query, params)
            after_query = time.monotonic()
            query_duratrion = after_query - before
            timings["query_duration_ms"] = query_duratrion * 1000
            timings["query_frequency"] = 1 / query_duratrion
            raw_rows = cursor.fetchall()
            result["result"] = [dict(r) for r in raw_rows]
            after_fetch = time.monotonic()
            fetch_duration = after_fetch - after_query
            timings["fetch_duration_ms"] = fetch_duration * 1000
            timings["fetch_frequency"] = 1 / fetch_duration
            total_duration = after_fetch - before
            timings["total_duration_ms"] = total_duration * 1000
            timings["total_frequency"] = 1 / total_duration

        if use_cache:
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

    def get_data(self: APIResource) -> list[dict]:
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
            response = self._session.get("https://api.scryfall.com/bulk-data", timeout=1).json()["data"]
            by_type = {r["type"]: r for r in response}
            oracle_cards_download_uri = by_type["oracle_cards"]["download_uri"]
            response = self._session.get(oracle_cards_download_uri, timeout=30).json()
            with pathlib.Path(cache_file).open("w") as f:
                json.dump(response, f, indent=4, sort_keys=True)
        else:
            logger.info("Cache hit!")
        return response

    def setup_schema(self: APIResource, *_: object, **__: object) -> None:
        """Set up the database schema and apply migrations as needed."""
        # read migrations from the db dir...
        # if any already applied migrations differ from what we want
        # to apply then drop everything
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS migrations (
                    file_name text not null,
                    file_sha256 text not null,
                    date_applied timestamp default now(),
                    file_contents text not null
                )""",
            )
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_migrations_filename ON migrations (file_name)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_migrations_file_sha256 ON migrations USING HASH (file_sha256)",
            )

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
                    conn.commit()

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
                conn.commit()

    def _get_cards_to_insert(self: APIResource) -> list[dict[str, Any]]:
        """Get the cards to insert into the database."""
        all_cards = self.get_data()
        to_insert = {}
        for card in all_cards:
            card_name = card["name"]
            if card_name in to_insert:
                continue
            processed_card = self._preprocess_card(card)
            if processed_card is None:
                continue
            to_insert[card_name] = processed_card
        return list(to_insert.values())

    def _preprocess_card(self: APIResource, card: dict[str, Any]) -> None | dict[str, Any]:
        """Preprocess a card to remove invalid cards and add necessary fields."""
        if set(card["legalities"].values()) == {"not_legal"}:
            return None
        if "paper" not in card["games"]:
            return None
        if "card_faces" in card:
            return None
        if card.get("set_type") == "funny":
            return None
        card_types, _, card_subtypes = (x.strip().split() for x in card.get("type_line", "").title().partition("\u2014"))
        card["card_types"] = card_types
        card["card_subtypes"] = card_subtypes or None
        if not card["card_subtypes"]:
            card.pop("card_subtypes")
        for creature_field in ["power", "toughness"]:
            val = card.setdefault(creature_field, None)
            try:
                numeric_val = int(val)
            except (TypeError, ValueError):
                card[f"{creature_field}_numeric"] = None
            else:
                card[f"{creature_field}_numeric"] = numeric_val
        card["card_colors"] = dict.fromkeys(card["colors"], True)
        card["card_color_identity"] = dict.fromkeys(card["color_identity"], True)
        card["card_keywords"] = dict.fromkeys(card.get("keywords", []), True)
        card["edhrec_rank"] = card.get("edhrec_rank")

        # Extract pricing data if available
        prices = card.get("prices", {})
        card["price_usd"] = prices.get("usd")
        card["price_eur"] = prices.get("eur")
        card["price_tix"] = prices.get("tix")

        # Extract set code for dedicated column
        card["card_set_code"] = card.get("set")

        return card

    def get_stats(self: APIResource, **_: object) -> dict[str, Any]:
        """Get stats about the cards."""
        to_insert = self._get_cards_to_insert()
        key_frequency = collections.Counter()
        for card in to_insert:
            key_frequency.update(k for k, v in card.items() if v not in [None, [], {}])
        return key_frequency.most_common()


    def _setup_complete(self: APIResource) -> True:
        """Return True if the setup is complete."""
        try:
            with self._conn_pool.connection() as conn:
                conn = typecast("Connection", conn)
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) AS num_cards FROM magic.cards")
                    return cursor.fetchall()[0]["num_cards"] > 0
        except Exception as oops:
            logger.error("Error checking if setup is complete: %s", oops, exc_info=True)
            return False

    @cached(cache={}, key=lambda _self, *_args, **_kwargs: None)
    def import_data(self: APIResource, **_: object) -> None:
        """Import data from Scryfall and insert into the database."""
        if self._setup_complete():
            return None

        self.setup_schema()

        to_insert = self._get_cards_to_insert()

        before = time.monotonic()

        # Use the consolidated loading method
        result = self._load_cards_with_staging(to_insert)

        after_transfer = time.monotonic()

        if result["status"] == "success":
            total_time = after_transfer - before
            rate = len(to_insert) / total_time if total_time > 0 else 0
            logger.info(
                "Loaded %d cards in %.2f seconds, rate: %.2f cards/s...",
                result["cards_loaded"],
                total_time,
                rate,
            )

            # Return the sample cards as before
            return result["sample_cards"]
        logger.error("Failed to import data: %s", result["message"])
        return None

    def get_cards(
        self: APIResource,
        *,
        min_name: str | None = None,
        max_name: str | None = None,
        limit: int = 2500,
        **_: object,
    ) -> list[dict[str, Any]]:
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
            query=self.read_sql("get_cards"),
            params={
                "min_name": min_name,
                "max_name": max_name,
                "limit": limit,
            },
        )["result"]

    def get_cards_to_csv(
        self: APIResource,
        *,
        min_name: str | None = None,
        max_name: str | None = None,
        limit: int = 2500,
        falcon_response: falcon.Response | None = None,
    ) -> None:
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


    def search(  # noqa: PLR0913
        self: APIResource,
        *,
        falcon_response: falcon.Response | None = None,
        q: str | None = None,
        query: str | None = None,
        orderby: str | None = None,
        direction: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Run a search query and return results and metadata.

        Args:
            falcon_response: The Falcon response object (unused).
            q: Query string (alternative to query parameter).
            query: Query string (alternative to q parameter).
            direction: Sort direction ('asc' or 'desc').
            limit: Maximum number of results to return.
            orderby: Field to sort by.

        Returns:
            Dict containing search results and metadata.
        """
        del falcon_response
        self.import_data()  # ensures that database is setup
        return self._search(
            query=query or q,
            orderby=orderby,
            direction=direction,
            limit=limit,
        )

    @cached(
        cache=TTLCache(maxsize=1000, ttl=60),
        key=lambda _self, *args, **kwargs: (args, tuple(sorted(kwargs.items()))),
    )
    def _search(
        self: APIResource,
        *,
        query: str | None = None,
        orderby: str | None = None,
        direction: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            where_clause, params = get_where_clause(query)
        except ValueError as err:
            # Handle parsing errors from parse_scryfall_query
            logger.info("ValueError caught for query '%s', raising BadRequest", query)
            raise falcon.HTTPBadRequest(
                title="Invalid Search Query",
                description=f"The search query '{query}' contains invalid syntax. {err}",
            ) from err
        sql_orderby = {
            "cmc": "cmc",
            "edhrec": "edhrec_rank",
            "power": "creature_power",
            "toughness": "creature_toughness",
            "usd": "usd",
        }.get(orderby, "edhrec_rank")
        sql_direction = {
            "asc": "ASC",
            "desc": "DESC",
        }.get(direction, "ASC")
        full_query = f"""
        SELECT
            card_name AS name,
            mana_cost_text AS mana_cost,
            oracle_text AS oracle_text,
            card_artist AS artist,
            cmc,
            raw_card_blob->>'set_name' AS set_name,
            raw_card_blob->>'type_line' AS type_line,
            raw_card_blob->'image_uris'->>'small' AS image_small,
            raw_card_blob->'image_uris'->>'normal' AS image_normal,
            raw_card_blob->'image_uris'->>'large' AS image_large
        FROM
            magic.cards AS card
        WHERE
            {where_clause}
        ORDER BY
            {sql_orderby} {sql_direction} NULLS LAST,
            edhrec_rank ASC NULLS LAST
        LIMIT
            {limit}
        """
        full_query = rewrap(full_query)
        logger.info("Full query: %s", full_query)
        logger.info("Params: %s", params)
        try:
            result_bag = self._run_query(query=full_query, params=params, explain=False)
        except psycopg.errors.DatatypeMismatch as err:
            # Raise BadRequest error for invalid query syntax
            # This happens with standalone arithmetic expressions like "cmc+1"
            logger.info("DatatypeMismatch caught for query '%s', raising BadRequest", query)
            raise falcon.HTTPBadRequest(
                title="Invalid Search Query",
                description=f"The search query '{query}' contains invalid syntax. "
                           "Arithmetic expressions like 'cmc+1' need to be part of a comparison (e.g., 'cmc+1>3').",
            ) from err

        cards = result_bag.pop("result", [])
        total_cards = len(cards)
        if total_cards == limit:
            use_estimate = False
            if use_estimate:
                full_query = f"""
                EXPLAIN (FORMAT JSON)
                SELECT
                    card_name
                FROM
                    magic.cards AS card
                WHERE
                    {where_clause}
                """
                full_query = rewrap(full_query)
                logger.info("Full query: %s", full_query)
                logger.info("Params: %s", params)
                ptr = self._run_query(query=full_query, params=params, explain=False)["result"][0]["QUERY PLAN"][0]["Plan"]
                total_cards = ptr["Plan Rows"]
            else:
                full_query = f"""
                SELECT
                    COUNT(1) AS total_cards
                FROM
                    magic.cards AS card
                WHERE
                    {where_clause}
                """
                full_query = rewrap(full_query)
                logger.info("Full query: %s", full_query)
                logger.info("Params: %s", params)
                count_result_bag = self._run_query(query=full_query, params=params, explain=False)
                total_cards = count_result_bag["result"][0]["total_cards"]
        return {
            "cards": cards,
            "compiled": full_query,
            "params": params,
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

    def get_migrations(self: APIResource, **_: object) -> list[dict[str, str]]:
        """Get the migrations from the filesystem.

        Returns:
        -------
            List[Dict[str, str]]: List of migration metadata dictionaries.

        """
        return get_migrations()

    def get_common_card_types(self: APIResource, **_: object) -> list[dict[str, Any]]:
        """Get the common card types from the database."""
        return self._run_query(
            query=self.read_sql("get_common_card_types"),
        )["result"]

    def get_common_keywords(self: APIResource, **_: object) -> list[dict[str, Any]]:
        """Get the common keywords from the database."""
        return self._run_query(
            query=self.read_sql("get_common_keywords"),
        )["result"]

    def _fetch_cards_from_scryfall(self: APIResource, *, tag: str) -> list[str]:
        """Fetch all card names with a specific tag from Scryfall API.

        This method handles pagination to get the complete list of cards.

        Args:
        ----
            tag (str): The Scryfall tag to search for.

        Returns:
        -------
            List[str]: List of card names that have the specified tag.

        Raises:
        ------
            ValueError: If API request fails or returns invalid data.

        """
        base_url = "https://api.scryfall.com/cards/search"
        params = {"q": f"oracletag:{tag}", "format": "json"}
        all_cards = []

        try:
            while True:
                time.sleep(1 / 10)
                response = self._session.get(base_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    break

                # Extract card names from current page
                page_card_names = [card.get("name") for card in data["data"] if card.get("name")]
                all_cards.extend(page_card_names)

                # Check if there are more pages
                if not data.get("has_more", False):
                    break

                # Get next page URL
                next_page = data.get("next_page")
                if not next_page:
                    break

                # Update base_url and clear params for next page (since next_page is a complete URL)
                base_url = next_page
                params = {}

        except requests.RequestException as oops:
            if oops.response.status_code == NOT_FOUND:
                return all_cards
            msg = f"Failed to fetch data from Scryfall API: {oops}"
            raise ValueError(msg) from oops

        return all_cards

    def update_tagged_cards(
        self: APIResource,
        *,
        tag: str,
        **_: object,
    ) -> dict[str, Any]:
        """Update cards with a specific Scryfall tag.

        Args:
        ----
            tag (str): The Scryfall tag to fetch and apply to cards.

        Returns:
        -------
            Dict[str, Any]: Result summary with updated card count and tag info.

        """
        if not tag:
            msg = "Tag parameter is required"
            raise ValueError(msg)

        # Fetch cards with this tag from Scryfall API (handles pagination)
        card_names = self._fetch_cards_from_scryfall(tag=tag)

        if not card_names:
            return {
                "tag": tag,
                "cards_updated": 0,
                "message": f"No cards found with tag '{tag}' in Scryfall API",
            }

        logger.info("Updating %d cards with tag '%s'", len(card_names), tag)
        # Update cards in database with the new tag
        updated_count = 0
        card_names.sort()
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            # Use SQL update with jsonb concatenation to add the tag
            for card_name_batch in itertools.batched(card_names, 200):  # noqa: B911
                cursor.execute(
                    """
                    UPDATE magic.cards
                    SET card_oracle_tags = card_oracle_tags || %(new_tag)s::jsonb
                    WHERE card_name = ANY(%(card_names)s)
                    """,
                    {
                        "card_names": list(card_name_batch),
                        "new_tag": json.dumps({tag: True}),
                    },
                )
                updated_count += cursor.rowcount
                conn.commit()

        return {
            "tag": tag,
            "cards_updated": updated_count,
            "total_cards_found": len(card_names),
            "message": f"Successfully updated {updated_count} cards with tag '{tag}'",
        }

    def discover_tags_from_scryfall(self: APIResource, **_: object) -> list[str]:
        """Discover all available tags from Scryfall tagger documentation.

        Returns:
        -------
            List[str]: List of all available tag names.

        Raises:
        ------
            ValueError: If API request fails or returns invalid data.

        """
        try:
            response = self._session.get("https://scryfall.com/docs/tagger-tags", timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            msg = f"Failed to fetch tag list from Scryfall: {e}"
            raise ValueError(msg) from e

        # Extract tag names from oracletag search links
        oracletag_pattern = r'/search\?q=oracletag%3A([^"&]+)'
        matches = re.findall(oracletag_pattern, response.text)

        # URL decode the tag names and remove duplicates
        unique_tags = sorted({urllib.parse.unquote(match) for match in matches})

        logger.info("Discovered %d unique tags from Scryfall", len(unique_tags))
        return unique_tags

    def discover_tags_from_graphql(self: APIResource, **_: object) -> list[str]:
        """Discover all available tags from Scryfall tagger using GraphQL API.

        This method uses the SearchTags GraphQL query to fetch all available tags.
        It paginates through all pages to get the complete list.

        Returns:
        -------
            List[str]: List of all available tag slugs.

        Raises:
        ------
            ValueError: If GraphQL request fails or returns invalid data.

        """
        tags = set()
        page = 1

        try:
            while True:
                # Fetch tags for current page
                result = self._tagger_client.search_tags(page=page)
                results = result["results"]
                if not results:
                    break

                # Extract tag slugs from results
                ignored_namespaces = ["artwork", "print"]
                tags.update(
                    tag["slug"] for tag in results
                    if tag["namespace"] not in ignored_namespaces
                )
                non_artwork_tags = [
                    tag
                    for tag in results
                    if tag["namespace"] not in ignored_namespaces
                ]
                logger.info("Discovered %d tags from GraphQL: %s", len(tags), non_artwork_tags)
                page += 1
        except (KeyError, TypeError, ValueError) as e:
            msg = f"Failed to parse GraphQL tag search response: {e}"
            raise ValueError(msg) from e

        # Remove duplicates and sort
        unique_tags = sorted(tags)
        logger.info("Discovered %d unique tags from GraphQL", len(unique_tags))
        return unique_tags

    def _get_tag_relationships(self: APIResource, *, tag: str) -> str | None:
        """Fetch list of relationships for a specific tag using Scryfall tagger GraphQL API.

        Args:
        ----
            tag (str): The tag to get relationships information for.

        Returns:
        -------
            list[dict]: List of relationships for the tag.
        """
        logger.info("Fetching relationships for %s", tag)
        def clean_tag(itag: dict) -> dict:
            return {
                "name": itag["name"],
                "namespace": itag["namespace"],
                "slug": itag["slug"],
            }

        # Use GraphQL API to get tag metadata including hierarchy
        relationships = []
        try:
            tag_data = self._tagger_client.fetch_tag(tag, include_taggings=False)
        except ValueError:
            return relationships
        ancestry = [
            clean_tag(parent["tag"])
            for parent in tag_data.pop("ancestry")
            if parent.get("tag")
        ]
        children = [clean_tag(tag) for tag in tag_data.pop("childTags")]
        tag_data = clean_tag(tag_data)

        for parent in ancestry:
            relationships.append(
                {
                    "parent": parent,
                    "child": tag_data,
                },
            )
        for child in children:
            relationships.append(
                {
                    "parent": tag_data,
                    "child": child,
                },
            )
        # remove the relationships where the parent and child are the same
        return [
            relationship
            for relationship in relationships
            if relationship["parent"]["slug"] != relationship["child"]["slug"]
        ]

    def _populate_tag_hierarchy(self: APIResource, *, tags: list[str]) -> dict[str, Any]:
        """Populate the tag hierarchy table with discovered tags.

        Args:
        ----
            tags (List[str]): List of tag names to process.

        Returns:
        -------
            Dict[str, Any]: Summary of the operation.

        """
        logger.info("Populating tag hierarchy")
        start_time = time.monotonic()
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            tags_in_random_order = list(tags)
            random.shuffle(tags_in_random_order)

            for idx, tag in enumerate(tags_in_random_order):
                if idx:
                    elapsed_time = time.monotonic() - start_time
                    fraction_complete = idx / len(tags_in_random_order)
                    estimated_time_remaining = (elapsed_time / fraction_complete) - elapsed_time
                    estimated_duration = datetime.timedelta(seconds=round(estimated_time_remaining, 1))
                else:
                    estimated_duration = "N/A"
                logger.info(
                    "Processing tag %d of %d: %20s (ETA: %s)",
                    idx + 1,
                    len(tags_in_random_order),
                    tag,
                    estimated_duration,
                )

                relationships = self._get_tag_relationships(tag=tag)

                parent_tags = {r["parent"]["slug"] for r in relationships}
                child_tags = {r["child"]["slug"] for r in relationships}
                all_tags = parent_tags | child_tags

                # record existence of all tags
                cursor.executemany(
                    """
                    INSERT INTO magic.tags (tag)
                    VALUES (%(tag)s)
                    ON CONFLICT (tag) DO NOTHING
                    """,
                    [{"tag": slug} for slug in all_tags],
                )

                cursor.executemany(
                    """
                    INSERT INTO magic.tag_relationships
                        (child_tag, parent_tag)
                    VALUES
                        (%(child_tag)s, %(parent_tag)s)
                    ON CONFLICT (child_tag, parent_tag)
                    DO NOTHING
                    """,
                    [
                        {
                            "child_tag": r["child"]["slug"],
                            "parent_tag": r["parent"]["slug"],
                        } for r in relationships
                    ],
                )
                conn.commit()

        return {
            "duration": time.monotonic() - start_time,
            "message": "Tag hierarchy populated successfully",
            "success": True,
            "tags_processed": len(tags_in_random_order),
        }


    def discover_and_import_all_tags(
        self: APIResource,
        *,
        import_cards: bool = True,
        import_hierarchy: bool = False,
        **_: object,
    ) -> dict[str, Any]:
        """Discover all Scryfall tags and optionally import their card associations.

        Args:
        ----
            import_cards (bool): Whether to import card associations for each tag.
            import_hierarchy (bool): Whether to discover and import tag hierarchy.

        Returns:
        -------
            Dict[str, Any]: Summary of the bulk import operation.

        """
        # Step 1: Discover all available tags
        result = {
            "success": True,
        }
        logger.info("Starting bulk tag discovery and import")
        try:
            all_tags = self.discover_tags_from_scryfall()
        except ValueError as e:
            result.update({
                "success": False,
                "error": str(e),
                "message": "Failed to discover tags from Scryfall",
            })
            return result

        if not all_tags:
            return {
                "success": False,
                "message": "No tags discovered from Scryfall",
            }

        # Step 2: Import tag hierarchy if requested
        if import_hierarchy:
            result["hierarchy"] = self._populate_tag_hierarchy(tags=all_tags)

        # Step 3: Import card associations for each tag if requested
        if import_cards:
            result["card_taggings"] = self._update_all_card_taggings()

        return result

    def _update_all_card_taggings(self: APIResource) -> None:
        """Update all card taggings."""
        logger.info("Updating all card taggings")
        tags = self._get_all_tags()
        start_time = time.monotonic()
        for idx, tag in enumerate(tags):
            if idx:
                elapsed_time = time.monotonic() - start_time
                fraction_complete = idx / len(tags)
                estimated_time_remaining = (elapsed_time / fraction_complete) - elapsed_time
                estimated_duration = datetime.timedelta(seconds=round(estimated_time_remaining, 1))
                logger.info("Updating tag %d of %d: %20s (ETA: %s)", idx + 1, len(tags), tag, estimated_duration)
            self.update_tagged_cards(tag=tag)
        return {
            "duration": time.monotonic() - start_time,
            "message": "All card taggings updated successfully",
            "success": True,
            "tags_processed": len(tags),
        }

    def _get_all_tags(self: APIResource) -> set[str]:
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT tag FROM magic.tags")
            return {r["tag"] for r in cursor.fetchall()}

    def import_card_by_name(
        self: APIResource,
        *,
        card_name: str,
        **_: object,
    ) -> dict[str, Any]:
        """Import a single card by name from Scryfall API.

        Args:
        ----
            card_name (str): The exact name of the card to import.

        Returns:
        -------
            Dict[str, Any]: Result summary with import status and card info.

        """
        if not card_name:
            msg = "card_name parameter is required"
            raise ValueError(msg)

        logger.info("Importing card by name: '%s'", card_name)

        # Check if card already exists in database for backward compatibility
        existing_check = self._run_query(
            query="SELECT card_name FROM magic.cards WHERE card_name = %(card_name)s",
            params={"card_name": card_name},
            explain=False,
        )

        if existing_check["result"]:
            return {
                "card_name": card_name,
                "status": "already_exists",
                "message": f"Card '{card_name}' already exists in database",
            }

        # Use import_cards_by_search with exact name query
        search_result = self.import_cards_by_search(search_query=f'!"{card_name}"')

        # Transform result to maintain backward compatibility with import_card_by_name
        # For filtering cases, we return the original format without card_name for strict compatibility
        if search_result["status"] in ["no_cards_after_preprocessing", "no_cards_before_preprocessing"]:
            # Return the original format for filtering cases
            return {
                "status": search_result["status"],
                "cards_loaded": search_result.get("cards_loaded", 0),
                "sample_cards": search_result.get("sample_cards", []),
                "message": search_result["message"],
            }

        result = {
            "card_name": card_name,
            "status": search_result["status"],
            "message": search_result["message"],
        }

        # Handle the specific case where we need to verify exact name match
        if search_result["status"] == "success" and search_result["cards_loaded"] > 0:
            # Check if any of the loaded cards actually match the requested name
            # This maintains the exact matching behavior from the original implementation
            sample_cards = search_result.get("sample_cards", [])
            if sample_cards:
                exact_matches = [card for card in sample_cards if card.get("name") == card_name]
                if not exact_matches:
                    # If no exact matches were found in the sample,
                    # we still report success since cards were loaded, but adjust the message
                    result["message"] = f"Cards imported for query '!{card_name}' but may not include exact match for '{card_name}'"

            # Add additional fields from load result for compatibility
            result["cards_loaded"] = search_result.get("cards_loaded", 0)
            result["sample_cards"] = search_result.get("sample_cards", [])

        # Handle not_found case with card-specific message
        elif search_result["status"] == "not_found":
            result["message"] = f"Card '{card_name}' not found in Scryfall API"

        # Handle error case with card-specific message
        elif search_result["status"] == "error":
            result["message"] = f"Error fetching card from Scryfall: {search_result['message']}"

        return result

    def import_cards_by_search(
        self: APIResource,
        *,
        search_query: str,
        **_: object,
    ) -> dict[str, Any]:
        """Import cards from Scryfall API using any search query.

        Args:
        ----
            search_query (str): The Scryfall search query to execute.

        Returns:
        -------
            Dict[str, Any]: Result summary with import status and card info.

        """
        if not search_query:
            msg = "search_query parameter is required"
            raise ValueError(msg)

        logger.info("Importing cards by search: '%s'", search_query)

        # Fetch card data from Scryfall API using the provided search query
        try:
            cards = self._scryfall_search(query=search_query)
            if not cards:
                return {
                    "search_query": search_query,
                    "status": "not_found",
                    "message": f"No cards found for search query '{search_query}' in Scryfall API",
                    "cards_loaded": 0,
                    "sample_cards": [],
                }

        except (requests.RequestException, ValueError, KeyError) as e:
            logger.error("Error fetching cards for search '%s' from Scryfall: %s", search_query, e)
            return {
                "search_query": search_query,
                "status": "error",
                "message": f"Error fetching cards from Scryfall: {e}",
                "cards_loaded": 0,
                "sample_cards": [],
            }

        # Insert the cards into the database using the consolidated method
        load_result = self._load_cards_with_staging(cards)

        # Add search_query to the result for consistency
        load_result["search_query"] = search_query

        if load_result["cards_loaded"] > 0:
            # Clear caches to ensure search can find the newly imported cards
            self._query_cache.clear()
            # Clear the search cache by accessing its cache attribute
            if hasattr(self._search, "cache"):
                self._search.cache.clear()

        return load_result


    def _scryfall_search(self: APIResource, *, query: str) -> list[dict[str, Any]]:
        """Search Scryfall API for cards matching the given query.

        This method handles pagination to get the complete list of cards and
        automatically applies filters for paper format and format legality.

        Args:
        ----
            query (str): The search query string for Scryfall.

        Returns:
        -------
            List[Dict[str, Any]]: List of card data from Scryfall API.

        Raises:
        ------
            ValueError: If API request fails or returns invalid data.

        """
        # Add standard filters for paper format and format legality
        # Wrap original query in parentheses to ensure proper filter application
        filters = ["game:paper", "(f:m or f:l or f:c or f:v)"]
        full_query = f"({query}) {' '.join(filters)}"

        base_url = "https://api.scryfall.com/cards/search"
        params = {"q": full_query, "format": "json"}
        all_cards = []

        try:
            while True:
                time.sleep(1 / 10)  # Rate limiting - 10 requests per second max
                response = self._session.get(base_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    break

                # Extract card data from current page
                page_cards = [card for card in data["data"] if card]
                all_cards.extend(page_cards)

                # Check if there are more pages
                if not data.get("has_more", False):
                    break

                # Get next page URL
                next_page = data.get("next_page")
                if not next_page:
                    break

                # Update base_url and clear params for next page
                base_url = next_page
                params = {}

        except requests.RequestException as oops:
            # Check if it's a 404 error - return empty list
            if (hasattr(oops, "response") and oops.response and oops.response.status_code == NOT_FOUND) or "404" in str(oops):
                return all_cards
            msg = f"Failed to fetch data from Scryfall API: {oops}"
            raise ValueError(msg) from oops

        return all_cards

    def _load_cards_with_staging(
        self: APIResource,
        cards: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Load cards into the database using a randomly-named staging table.

        This method consolidates card loading functionality by:
        1. Creating a staging table with a randomly generated suffix
        2. Loading card data into the staging table using COPY for efficiency
        3. Transferring data from staging to the main magic.cards table
        4. Returning random sample cards from staging before cleanup
        5. Dropping the staging table

        Args:
        ----
            cards (List[Dict[str, Any]]): List of card data to load.

        Returns:
        -------
            Dict[str, Any]: Result with:
                - cards_loaded: number of cards successfully loaded
                - sample_cards: list of up to 10 random cards from staging
                - status: "success", "no_cards", or "database_error"
                - message: descriptive message

        """
        if not cards:
            return {
                "status": "no_cards_before_preprocessing",
                "cards_loaded": 0,
                "sample_cards": [],
                "message": "No cards provided for loading",
            }

        cards = list(filter(None, (self._preprocess_card(icard) for icard in cards)))

        if not cards:
            return {
                "status": "no_cards_after_preprocessing",
                "cards_loaded": 0,
                "sample_cards": [],
                "message": "No cards remaining after preprocessing",
            }

        # Generate random staging table name
        staging_suffix = secrets.token_hex(8)
        staging_table_name = f"import_staging_{staging_suffix}"

        try:
            with self._conn_pool.connection() as conn, conn.cursor() as cursor:
                # Create staging table with unique name
                cursor.execute(f"CREATE TEMPORARY TABLE {staging_table_name} (card_blob jsonb)")

                # Load cards into staging table using COPY for efficiency
                with cursor.copy(f"COPY {staging_table_name} (card_blob) FROM STDIN WITH (FORMAT csv, HEADER false)") as copy_filehandle:
                    writer = csv.writer(copy_filehandle, quoting=csv.QUOTE_ALL)
                    writer.writerows([orjson.dumps(card).decode("utf-8")] for card in cards)

                # Get random sample before transfer (up to 10 cards)
                cursor.execute(f"SELECT card_blob FROM {staging_table_name} ORDER BY RANDOM() LIMIT 10")
                sample_cards = [r["card_blob"] for r in cursor.fetchall()]

                # Transfer from staging to main table (always with price fields)
                insert_query = f"""
                    INSERT INTO magic.cards
                    (
                        card_name,               -- 1
                        cmc,                     -- 2
                        mana_cost_text,          -- 3
                        mana_cost_jsonb,         -- 4
                        card_types,              -- 5
                        card_subtypes,           -- 6
                        card_colors,             -- 7
                        card_color_identity,     -- 8
                        card_keywords,           -- 9
                        creature_power,          -- 10
                        creature_power_text,     -- 11
                        creature_toughness,      -- 12
                        creature_toughness_text, -- 13
                        edhrec_rank,             -- 14
                        price_usd,               -- 15
                        price_eur,               -- 16
                        price_tix,               -- 17
                        oracle_text,             -- 18
                        card_set_code,           -- 19
                        card_artist,             -- 20
                        raw_card_blob            -- 21
                    )
                    SELECT
                        card_blob->>'name' AS card_name, -- 1
                        (card_blob->>'cmc')::float::integer AS cmc, -- 2
                        card_blob->>'mana_cost' AS mana_cost_text, -- 3
                        card_blob->'mana_cost' AS mana_cost_jsonb, -- 4
                        card_blob->'card_types' AS card_types, -- 5
                        card_blob->'card_subtypes' AS card_subtypes, -- 6
                        card_blob->'card_colors' AS card_colors, -- 7
                        card_blob->'card_color_identity' AS card_color_identity, -- 8
                        card_blob->'card_keywords' AS card_keywords, -- 9
                        (card_blob->>'power_numeric')::integer AS creature_power, -- 10
                        card_blob->>'power' AS creature_power_text, -- 11
                        (card_blob->>'toughness_numeric')::integer AS creature_toughness, -- 12
                        card_blob->>'toughness' AS creature_toughness_text, -- 13
                        (card_blob->>'edhrec_rank')::integer AS edhrec_rank, -- 14
                        (card_blob->>'price_usd')::real AS price_usd, -- 15
                        (card_blob->>'price_eur')::real AS price_eur, -- 16
                        (card_blob->>'price_tix')::real AS price_tix, -- 17
                        card_blob->>'oracle_text' AS oracle_text, -- 18
                        card_blob->>'card_set_code' AS card_set_code, -- 19
                        card_blob->>'artist' AS card_artist, -- 20
                        card_blob AS raw_card_blob -- 21
                    FROM
                        {staging_table_name}
                    ON CONFLICT (card_name) DO NOTHING
                """

                cursor.execute(insert_query)
                cards_loaded = cursor.rowcount

                # Drop the staging table
                cursor.execute(f"DROP TABLE {staging_table_name}")

                conn.commit()

                return {
                    "status": "success",
                    "cards_loaded": cards_loaded,
                    "sample_cards": sample_cards,
                    "message": f"Successfully loaded {cards_loaded} cards",
                }

        except (psycopg.Error, ValueError, KeyError) as e:
            logger.error("Error loading cards with staging table %s: %s", staging_table_name, e)
            # Try to clean up staging table on error
            try:
                with self._conn_pool.connection() as conn, conn.cursor() as cursor:
                    cursor.execute(f"DROP TABLE IF EXISTS {staging_table_name}")
                    conn.commit()
            except (psycopg.Error, ValueError) as cleanup_error:
                logger.warning("Failed to cleanup staging table %s: %s", staging_table_name, cleanup_error)

            return {
                "status": "database_error",
                "cards_loaded": 0,
                "sample_cards": [],
                "message": f"Error loading cards: {e}",
            }
