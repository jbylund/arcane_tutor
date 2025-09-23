"""Implementation of the routes of our simple api."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from multiprocessing.synchronize import RLock as LockType

if True:  # imports
    import collections
    import copy
    import csv
    import datetime
    import functools
    import inspect
    import itertools
    import logging
    import multiprocessing
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
    import requests
    from cachetools import LRUCache, TTLCache, cached
    from psycopg import Connection

    from .parsing import generate_sql_query, parse_scryfall_query
    from .parsing.scryfall_nodes import mana_cost_str_to_dict
    from .tagger_client import TaggerClient
    from .utils import db_utils, error_monitoring

    if TYPE_CHECKING:
        import psycopg_pool


logger = logging.getLogger(__name__)

# pylint: disable=c-extension-no-member
DEFAULT_IMPORT_GUARD = multiprocessing.RLock()
NOT_FOUND = 404


def _convert_string_to_type(str_value: str, param_type: Any) -> Any:  # noqa: ANN401
    """Convert a string value to the specified type.

    Args:
        str_value: The string value to convert
        param_type: The target type annotation

    Returns:
        The converted value, or the original string if conversion fails/unsupported
    """
    # Handle special cases for no type annotation
    if param_type in {inspect.Parameter.empty, Any, "object"}:
        result = str_value
    # Convert to boolean
    elif param_type is bool or str(param_type) == "<class 'bool'>" or param_type == "bool":
        result = str_value.lower() in ("true", "1", "yes", "on")
    # Convert to integer
    elif param_type is int or str(param_type) == "<class 'int'>" or param_type == "int":
        try:
            result = int(str_value)
        except ValueError:
            result = str_value  # Keep as string if conversion fails
    # Convert to float
    elif param_type is float or str(param_type) == "<class 'float'>" or param_type == "float":
        try:
            result = float(str_value)
        except ValueError:
            result = str_value  # Keep as string if conversion fails
    else:
        # For all other types (including str), keep as string
        result = str_value

    return result


def make_type_converting_wrapper(func: callable) -> callable:
    """Create a wrapper that converts string arguments to the types expected by the function.

    Args:
        func: The function to wrap with type conversion

    Returns:
        A new function that converts string arguments to match the function's signature
    """
    sig = inspect.signature(func)

    # Check if function needs type conversion wrapper
    # If signature has no parameters or only has 'self', return function as-is
    params = [p for name, p in sig.parameters.items() if name not in ("self", "_")]
    if not params:
        return func

    def convert_args(**str_kwargs: str) -> dict[str, Any]:
        """Convert string keyword arguments to match function signature types."""
        converted_kwargs = {}

        for param_name, param in sig.parameters.items():
            if param_name in str_kwargs:
                str_value = str_kwargs[param_name]
                converted_kwargs[param_name] = _convert_string_to_type(str_value, param.annotation)
            elif param_name not in ("self", "_"):
                # Parameter not provided, use default if available
                if param.default != inspect.Parameter.empty:
                    converted_kwargs[param_name] = param.default

        return converted_kwargs

    def wrapper(**raw_kwargs: Any) -> Any:  # noqa: ANN401
        """Wrapper function that converts arguments and calls the original function."""
        # Filter out string parameters that need conversion
        str_params = {k: v for k, v in raw_kwargs.items() if isinstance(v, str)}
        non_str_params = {k: v for k, v in raw_kwargs.items() if not isinstance(v, str)}

        # Convert string parameters
        converted_params = convert_args(**str_params)

        # Merge with non-string parameters (non-string params take precedence)
        final_params = {**converted_params, **non_str_params}

        return func(**final_params)

    # Use functools.update_wrapper to preserve original function metadata
    return functools.update_wrapper(wrapper, func)


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
        s = orjson.dumps(iobj).decode("utf-8")
        return len(s) < max_json_object_length
    except TypeError:
        return False
    return True



class APIResource:
    """Class implementing request handling for our simple API."""

    def __init__(self: APIResource, *, import_guard: LockType = DEFAULT_IMPORT_GUARD) -> None:
        """Initialize an APIResource object, set up connection pool and action map.

        Sets up the database connection pool and action mapping for the API.
        """
        self._conn_pool: psycopg_pool.ConnectionPool = db_utils.make_pool()
        # Create action map with type-converting wrappers for all public methods
        self.action_map = {}
        for method_name in dir(self):
            if method_name.startswith("_"):
                continue
            method = getattr(self, method_name)
            if callable(method):
                self.action_map[method_name] = make_type_converting_wrapper(method)
        self.action_map["index"] = make_type_converting_wrapper(self.index_html)
        self._query_cache = LRUCache(maxsize=1_000)
        self._session = requests.Session()
        self._import_guard: LockType = import_guard

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
            error_monitoring.error_handler(req, oops)
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
        routes = {}

        for endpoint_name, wrapped_func in self.action_map.items():
            # Get the original function from the wrapper
            original_func = wrapped_func.__wrapped__ if hasattr(wrapped_func, "__wrapped__") else wrapped_func

            # Get function signature
            sig = inspect.signature(original_func)

            # Extract docstring
            doc = original_func.__doc__ or ""

            # Parse arguments
            args = []
            kwargs = {}

            for param_name, param in sig.parameters.items():
                if param_name.startswith("_"):
                    continue
                if param_name in ("self", "falcon_response"):
                    continue

                param_info = {
                    "name": param_name,
                    "type": self._get_type_name(param.annotation),
                }

                if param.default != inspect.Parameter.empty:
                    # It's a keyword argument with default
                    kwargs[param_name] = {
                        "type": self._get_type_name(param.annotation),
                        "default": param.default,
                    }
                else:
                    # It's a positional argument
                    args.append(param_info)

            routes[endpoint_name] = {
                "doc": doc,
                "args": args,
                "kwargs": kwargs,
            }

        raise falcon.HTTPNotFound(
            title="Not Found",
            description={
                "routes": routes,
            },
        )

    def _get_type_name(self: APIResource, annotation: Any) -> str:  # noqa: ANN401
        """Convert a type annotation to a readable string.

        Args:
            annotation: The type annotation to convert

        Returns:
            A string representation of the type
        """
        if annotation == inspect.Parameter.empty:
            return "Any"

        # Handle generic types and complex annotations
        if hasattr(annotation, "__name__"):
            return annotation.__name__
        if annotation is None:
            return "None"
        if hasattr(annotation, "__origin__"):
            # Handle generic types like List[str], Dict[str, int], etc.
            origin = annotation.__origin__
            if hasattr(origin, "__name__"):
                return origin.__name__
            return str(origin)

        # Fallback to string representation
        return str(annotation)

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
                    return orjson.dumps(v, option=orjson.OPT_SORT_KEYS).decode()
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

        params = {k: db_utils.maybe_json(v) for k, v in params.items()}
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
                response = orjson.loads(f.read())
        except FileNotFoundError:
            logger.info("Cache miss!")
            response = orjson.loads(self._session.get("https://api.scryfall.com/bulk-data", timeout=1).content)["data"]
            by_type = {r["type"]: r for r in response}
            oracle_cards_download_uri = by_type["oracle_cards"]["download_uri"]
            response = orjson.loads(self._session.get(oracle_cards_download_uri, timeout=30).content)
            with pathlib.Path(cache_file).open("w") as f:
                f.write(
                    orjson.dumps(
                        response,
                        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                    ).decode("utf-8"),
                )
        else:
            logger.info("Cache hit!")
        return response

    def setup_schema(self: APIResource, *_: object, **__: object) -> None:
        """Set up the database schema and apply migrations as needed."""
        filesystem_migrations = db_utils.get_migrations()

        with self._import_guard:
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
        card["card_subtypes"] = card_subtypes or []  # Use empty array instead of None
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

        # Extract frame data - combine frame version and frame effects into single JSONB object
        frame_data = {}
        # Add frame version if present (titlecased for consistency)
        frame_version = card.get("frame")
        if frame_version:
            frame_data[frame_version.title()] = True
        # Add frame effects if present (titlecased for consistency)
        frame_effects = card.get("frame_effects", [])
        for effect in frame_effects:
            frame_data[effect.title()] = True
        card["card_frame_data"] = frame_data

        # Extract pricing data if available
        prices = card.get("prices", {})
        card["price_usd"] = prices.get("usd")
        card["price_eur"] = prices.get("eur")
        card["price_tix"] = prices.get("tix")

        # Extract set code for dedicated column
        card["card_set_code"] = card.get("set")
        mana_cost_text = card.get("mana_cost", "")
        card["mana_cost_jsonb"] = mana_cost_str_to_dict(mana_cost_text)

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
            # check without taking the lock
            # so the majority of the time we will never have to take the lock
            return None

        with self._import_guard:
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
                description=f'Failed to parse query: "{query}"',
            ) from err
        sql_orderby = {
            "cmc": "cmc",
            "edhrec": "edhrec_rank",
            "power": "creature_power",
            "toughness": "creature_toughness",
            "usd": "usd",
            "rarity": "card_rarity_int",
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
            total_cards = self._get_total_cards_exact(
                where_clause=where_clause,
                params=params,
            )
        return {
            "cards": cards,
            "compiled": full_query,
            "params": params,
            "query": query,
            "result": result_bag,
            "total_cards": total_cards,
        }

    def _get_total_cards_exact(
        self: APIResource,
        *,
        where_clause: str,
        params: dict[str, Any],
    ) -> int:
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
        return count_result_bag["result"][0]["total_cards"]

    def _get_total_cards_from_estimate(
        self: APIResource,
        *,
        where_clause: str,
        params: dict[str, Any],
    ) -> int:
        """Get total cards count either from estimate or actual count.

        Args:
        ----
            where_clause: The WHERE clause for the query
            params: Query parameters
            use_estimate: Whether to use EXPLAIN estimate or actual COUNT

        Returns:
        -------
            Total number of cards matching the query
        """
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
        return ptr["Plan Rows"]


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
        return db_utils.get_migrations()

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
        card_names = self._scryfall_search(query=f"oracletag:{tag}")

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
                        "new_tag": orjson.dumps({tag: True}).decode("utf-8"),
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
        return self.import_cards_by_search(search_query=f'!"{card_name}"')

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
        filters = [
            "(f:m or f:l or f:c or f:v)",
            "game:paper",
            "unique:prints",
        ]
        full_query = f"({query}) {' '.join(filters)}"

        base_url = "https://api.scryfall.com/cards/search"
        params = {"q": full_query, "format": "json"}
        all_cards = []

        try:
            while True:
                time.sleep(1 / 10)  # Rate limiting - 10 requests per second max
                response = self._session.get(base_url, params=params, timeout=30)
                response.raise_for_status()
                data = orjson.loads(response.content)

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

        # TODO:
        # this is a little bit of a spray and pray method
        # what I want to do is implement a priority ordering
        # for cards, so that we import only one card of each name
        # but we use frame, printing time, etc. to get the best instance
        # of that card (likely the one with the highest quality artwork)
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
                    writer.writerows([orjson.dumps(card, option=orjson.OPT_SORT_KEYS).decode("utf-8")] for card in cards)

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
                        card_rarity_text,        -- 21
                        card_rarity_int,         -- 22
                        collector_number,        -- 23
                        collector_number_int,    -- 24
                        raw_card_blob,           -- 25
                        card_legalities,         -- 26
                        card_frame_data          -- 27
                    )
                    SELECT
                        card_blob->>'name' AS card_name, -- 1
                        (card_blob->>'cmc')::float::integer AS cmc, -- 2
                        card_blob->>'mana_cost' AS mana_cost_text, -- 3
                        card_blob->'mana_cost_jsonb' AS mana_cost_jsonb, -- 4
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
                        LOWER(card_blob->>'rarity') AS card_rarity_text, -- 21
                        magic.rarity_text_to_int(LOWER(card_blob->>'rarity')) AS card_rarity_int, -- 22
                        card_blob->>'collector_number' AS collector_number, -- 23
                        magic.extract_collector_number_int(card_blob->>'collector_number') AS collector_number_int, -- 24
                        card_blob AS raw_card_blob, -- 25
                        COALESCE(card_blob->'legalities', '{{}}'::jsonb) AS card_legalities, -- 26
                        COALESCE(card_blob->'card_frame_data', '{{}}'::jsonb) AS card_frame_data -- 27
                    FROM
                        {staging_table_name}
                    ON CONFLICT (card_name) DO NOTHING
                """

                cursor.execute(insert_query)
                cards_loaded = cursor.rowcount

                # Drop the staging table
                cursor.execute(f"DROP TABLE {staging_table_name}")

                conn.commit()

                result = {
                    "status": "success",
                    "cards_loaded": cards_loaded,
                    "sample_cards": sample_cards,
                    "message": f"Successfully loaded {cards_loaded} cards",
                }

                # Clear caches when cards are successfully loaded
                if cards_loaded > 0:
                    self._query_cache.clear()
                    # Clear the search cache by accessing its cache attribute
                    if hasattr(self._search, "cache"):
                        self._search.cache.clear()

                return result

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
