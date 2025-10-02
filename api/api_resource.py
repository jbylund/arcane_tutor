"""Implementation of the routes of our simple api."""

from __future__ import annotations

import collections
import copy
import csv
import datetime
import enum
import functools
import hashlib
import inspect
import itertools
import logging
import os
import pathlib
import random
import re
import secrets
import time
import urllib.parse
import uuid
from typing import TYPE_CHECKING, Any
from typing import cast as typecast
from urllib.parse import urlparse

import falcon
import orjson
import psycopg
import requests
from cachetools import LRUCache, TTLCache, cached
from psycopg import Connection, Cursor

from .parsing import generate_sql_query, parse_scryfall_query
from .parsing.scryfall_nodes import extract_frame_data_from_raw_card, mana_cost_str_to_dict
from .tagger_client import TaggerClient
from .utils import db_utils, error_monitoring, multiprocessing_utils

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import RLock as LockType

    import psycopg_pool


class UniqueMode(enum.StrEnum):
    """Unique mode for partitioning."""
    CARDS = enum.auto()
    PRINTINGS = enum.auto()
    ILLUSTRATIONS = enum.auto()

logger = logging.getLogger(__name__)

# pylint: disable=c-extension-no-member
NOT_FOUND = 404

def parse_type_line(type_line: str) -> tuple[list[str], list[str]]:
    """Parse the type line of a card."""
    card_types, _, card_subtypes = (x.strip().split() for x in type_line.title().partition("\u2014"))
    return card_types, card_subtypes or []

def get_cards_and_faces(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Get cards and faces from cards."""
    cards_and_faces = []
    for icard in cards:
        faces = icard.get("card_faces", [])
        if faces:
            for face_index, face in enumerate(faces):
                face["face_index"] = face_index
                cards_and_faces.append(icard | face)
        else:
            icard["face_index"] = 0
            cards_and_faces.append(icard)
    return cards_and_faces

def extract_card_face_id(icard: dict[str, Any]) -> str:
    """Extract a card face id from a card."""
    return uuid_from_args(
        icard["face_index"],
        icard["oracle_id"],
    )

def extract_card_face_printing_id(icard: dict[str, Any]) -> str:
    """Extract a card face printing id from a card."""
    return uuid_from_args(
        icard["face_index"],
        icard["id"],
    )


def uuid_from_args(*args: object) -> str:
    """Generate a uuid from a list of arguments."""
    seperator = "🍵"
    signature_string = seperator.join(str(arg) for arg in args)
    signature_bytes = signature_string.encode()
    digest = hashlib.sha256(signature_bytes).hexdigest()
    return str(uuid.UUID(digest[:32]))

def extract_card_faces(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract card faces from cards."""
    """
    face_keywords jsonb,
    face_produced_mana jsonb,
    """
    card_face_id_to_card_face = {}
    for icard in get_cards_and_faces(cards):
        ocard = {}
        ocard["card_face_id"] = card_face_id = extract_card_face_id(icard)
        ocard["card_id"] = icard["oracle_id"]
        ocard["card_face_name"] = icard["name"]
        ocard["face_index"] = icard["face_index"]
        ocard["mana_cost_text"] = mana_cost_str = icard["mana_cost"]
        ocard["mana_cost_jsonb"] = mana_cost_str_to_dict(mana_cost_str)
        ocard["colors"] = color_array_to_object(icard["colors"])
        ocard["cmc"] = int(icard["cmc"])
        ocard["type_line"] = type_line = icard["type_line"]
        card_types, card_subtypes = parse_type_line(type_line)
        ocard["face_types"] = card_types
        ocard["face_subtypes"] = card_subtypes
        ocard["oracle_text"] = icard["oracle_text"]

        # integer like fields
        ocard["power_text"] = power = icard.get("power")
        ocard["toughness_text"] = toughness = icard.get("toughness")
        ocard["power_int"] = maybe_int(power)
        ocard["toughness_int"] = maybe_int(toughness)
        ocard["loyalty_text"] = loyalty = icard.get("loyalty")
        ocard["loyalty_int"] = maybe_int(loyalty)
        ocard["defense_text"] = defense = icard.get("defense")
        ocard["defense_int"] = maybe_int(defense)

        card_face_id_to_card_face[card_face_id] = ocard
    return list(card_face_id_to_card_face.values())

def extract_card_printings(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract card printings from cards."""
    card_printings = []
    for icard in cards:
        oracle_id = extract_oracle_id(icard)
        rarity = icard["rarity"]
        collector_number = icard["collector_number"]
        collector_number_int = extract_collector_number_int(collector_number)
        if collector_number_int is None:
            logger.warning("Collector number %s is not a valid integer: %s", collector_number, icard)
            continue
        if collector_number_int < 0:
            logger.warning("Collector number %s is negative: %s", collector_number, icard)
            continue
        as_dict = {
            "border_color": icard["border_color"].lower(),
            "card_id": oracle_id,
            "card_printing_id": icard["id"],
            "collector_number_int": collector_number_int,
            "collector_number_text": collector_number,
            "frame_bag": {},
            "rarity_int": rarity_text_to_int(rarity),
            "rarity_text": rarity,
            "set_code": icard["set"],
        }
        card_printings.append(as_dict)
    return card_printings


def extract_card_face_printings(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract card face printings from cards."""
    """
    card_face_id uuid NOT NULL REFERENCES magic.card_faces(card_face_id),
    illustration_id uuid NOT NULL REFERENCES magic.illustrations(illustration_id),
    card_printing_id uuid NOT NULL REFERENCES magic.card_printings(card_printing_id),
    layout text, -- Layout of this card face (for reversible cards)
    watermark text, -- Watermark on this particular card face
    flavor_text text,
    image_uris jsonb, -- Object providing URIs to imagery for this face
    """

    card_face_printings = {}
    for icard in get_cards_and_faces(cards):

        collector_number = icard["collector_number"]
        collector_number_int = extract_collector_number_int(collector_number)
        if collector_number_int is None or collector_number_int < 0:
            continue

        ocard = {}
        try:
            ocard["card_printing_id"] = icard["id"]
            ocard["card_face_id"] = extract_card_face_id(icard)
            ocard["card_face_printing_id"] = face_printing_id = extract_card_face_printing_id(icard)
            ocard["illustration_id"] = icard["illustration_id"]
            ocard["flavor_text"] = icard.get("flavor_text") # not all cards (or card faces) have flavor text
            ocard["image_uris"] = icard["image_uris"]
            for field in ["layout", "watermark"]:
                val = icard.get(field)
                if val is not None:
                    ocard[field] = val.lower()
        except KeyError as oops:
            logger.error("Card %s has no %s", icard, oops)
            continue

        card_face_printings[face_printing_id] = ocard

    return list(card_face_printings.values())

def include_card(card: dict[str, Any]) -> bool:
    """Should this card be included in the db."""
    return set(card["legalities"].values()) != {"not_legal"}

def dict_to_tuple(d: dict[str, Any]) -> tuple[str, Any]:
    """Convert a dictionary to a tuple."""
    try:
        res = tuple(sorted(d.items()))
        hash(res)
        return res
    except TypeError as oops:
        msg = f"Dictionary {d} is not hashable"
        raise TypeError(msg) from oops


def extract_card_sets(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract card sets from cards."""
    card_sets = set()
    for icard in cards:
        projected = {k: v for k, v in icard.items() if k.startswith("set")}
        projected = projected | {k[4:]: v for k, v in projected.items()}
        projected["set_code"] = icard["set"]
        card_sets.add(dict_to_tuple(projected))
    return [dict(r) for r in card_sets]


def extract_artists(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract artists from cards."""
    artist_id_to_name = {}
    for icard in get_cards_and_faces(cards):
        artist_ids = icard.get("artist_ids", [])
        if len(artist_ids) != 1:
            continue
        artist_name = icard.get("artist")
        artist_id = artist_ids[0]
        artist_id_to_name[artist_id] = artist_name
    return [
        {"artist_name": artist_name, "artist_id": artist_id}
        for artist_id, artist_name in artist_id_to_name.items()
    ]

def extract_illustrations(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract illustrations from cards."""
    illustrations = {
        icard.get("illustration_id")
        for icard in get_cards_and_faces(cards)
    }
    illustrations.discard(None)
    return [{"illustration_id": illustration_id} for illustration_id in illustrations]

def extract_illustration_artists(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract illustration artists from cards."""
    illustration_artists = set()
    for icard in get_cards_and_faces(cards):
        illustration_id = icard.get("illustration_id")
        if illustration_id is None:
            # this is true of the base card of a double faced card
            # each face has an illustration, but the parent card does not
            continue
        artist_ids = icard.get("artist_ids", [])
        if len(artist_ids) != 1:
            continue
        for artist_id in artist_ids:
            illustration_artists.add((illustration_id, artist_id))
    return [{"illustration_id": illustration_id, "artist_id": artist_id} for illustration_id, artist_id in illustration_artists]

def rarity_text_to_int(rarity_text: str) -> int:
    """Convert rarity text to integer."""
    rarity_map = {
        "common": 0,
        "uncommon": 1,
        "rare": 2,
        "mythic": 3,
        "special": 4,
        "bonus": 5,
    }
    return rarity_map.get(rarity_text, -1)


def color_array_to_object(color_array: list[str]) -> dict[str, bool]:
    """Convert a color array to an object."""
    return dict.fromkeys(color_array, True)

def extract_oracle_id(icard: dict[str, Any]) -> str:
    """Extract an oracle id from a card."""
    try:
        return icard["oracle_id"]
    except KeyError:
        pass
    oracle_ids = {
        face["oracle_id"]
        for face in icard["card_faces"]
    }
    if len(oracle_ids) != 1:
        msg = f"Card {icard} has multiple oracle ids: {oracle_ids}"
        raise ValueError(msg)
    return oracle_ids.pop()

def extract_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract cards from cards."""
    input_cards = cards
    del cards

    # keywords should belong to the card face, but scryfall actually
    # has them on the card object - so we're leaving them here
    output_cards = {}
    for icard in input_cards:
        output_cards[extract_oracle_id(icard)] = icard
    del icard
    return [
        {
            "card_color_identity": color_array_to_object(c.get("color_identity")),
            "card_id": oracle_id,
            "card_legalities": c["legalities"],
            "card_name": c["name"],
            "card_oracle_tags": c.get("oracle_tags", {}),
            "edhrec_rank": c.get("edhrec_rank"),
            "keywords": dict.fromkeys(c.get("keywords", []), True),
        }
        for oracle_id, c in output_cards.items()
    ]



def maybeify(func: callable) -> callable:
    """Convert value to int (via float first), returning None if conversion fails."""
    @functools.wraps(func)
    def wrapper(val: str | int | float | None) -> int | None:
        if val is None:
            return None
        try:
            return func(val)
        except (ValueError, TypeError):
            return None
    return wrapper

@maybeify
def maybe_float(val: str | int | float | None) -> float | None:
    """Convert value to float, returning None if conversion fails."""
    return float(val)

@maybeify
def maybe_int(val: str | int | float | None) -> int | None:
    """Convert value to int (via float first), returning None if conversion fails."""
    return int(float(val))


def extract_collector_number_int(collector_number: str | int | float | None) -> int | None:
    """Extract the integer part of a collector number."""
    if collector_number is None:
        return None
    # Implement magic.extract_collector_number_int in Python
    # Extract numeric characters using regex, similar to the database function
    numeric_part_match = re.search(r"[0-9]+", str(collector_number))
    if numeric_part_match:
        numeric_part = numeric_part_match.group(0)
        try:
            int_val = int(numeric_part)
            # PostgreSQL integer range is -2^31 to 2^31-1
            if -2**31 <= int_val <= 2**31-1:
                return int_val
        except (ValueError, OverflowError):
            pass
    return None  # Field will be null by default

def _convert_string_to_type(str_value: str, param_type: Any) -> Any:  # noqa: ANN401
    """Convert a string value to the specified type.

    Args:
        str_value: The string value to convert
        param_type: The target type annotation

    Returns:
        The converted value, or the original string if conversion fails/unsupported
    """
    if str_value is None:
        return None

    def identity(x: str) -> str:
        return x

    def convert_to_bool(x: str) -> bool:
        return x.lower() in ("true", "1", "yes", "on")

    converter_map = {
        "UniqueMode": UniqueMode,
        "bool": convert_to_bool,
        "float": float,
        "int": int,
        "str": identity,
    }
    possible_types = [x.strip() for x in param_type.split("|")]
    for ipossible_type in possible_types:
        try:
            converter = converter_map[ipossible_type]
        except KeyError:
            continue
        try:
            return converter(str_value)
        except (ValueError, TypeError):
            continue

    logger.warning("Was unable to convert parameter: [%s][%s]", type(param_type), param_type)
    return str_value


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

    def __init__(
        self: APIResource,
        *,
        import_guard: LockType = multiprocessing_utils.DEFAULT_LOCK,
        schema_setup_event: EventType = multiprocessing_utils.DEFAULT_EVENT,
    ) -> None:
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
        self._schema_setup_event: EventType = schema_setup_event

        version = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d")
        version = f"magic-api/{version}"
        self._session.headers.update({"User-Agent": version})
        # Initialize Tagger client for GraphQL API access
        self._tagger_client = TaggerClient()
        logger.info("Worker with pid %d has conn pool %s", os.getpid(), self._conn_pool)
        self.setup_schema()

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
            logger.info(
                "Request duration: %.5f seconds (%.2f ms) / %s",
                duration,
                duration * 1000,
                resp.status,
            )

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
        statement_timeout: int = 10_000,
    ) -> dict[str, Any]:
        """Run a SQL query with optional parameters and explanation.

        Args:
        ----
            query (str): The SQL query to run.
            params (Optional[Dict[str, Any]]): Query parameters.
            explain (bool): Whether to run EXPLAIN on the query.
            statement_timeout (int): The statement timeout in milliseconds.

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
            cursor.execute(f"set statement_timeout = {statement_timeout}")
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
        data_key = "oracle_cards"
        data_key = "default_cards"
        cache_dir_path = pathlib.Path("/data/api")
        if not cache_dir_path.exists():
            cache_dir_path = pathlib.Path("/tmp/api")  # noqa: S108
            cache_dir_path.mkdir(parents=True, exist_ok=True)
        cache_file_path = cache_dir_path / f"{data_key}.json"
        try:
            with cache_file_path.open() as f:
                response = orjson.loads(f.read())
        except FileNotFoundError:
            logger.info("Cache miss, %s not found!", cache_file_path)
        else:
            logger.info("Cache hit, %s found!", cache_file_path)
            return response
        response = orjson.loads(self._session.get("https://api.scryfall.com/bulk-data", timeout=1).content)["data"]
        by_type = {r["type"]: r for r in response}
        oracle_cards_download_uri = by_type[data_key]["download_uri"]
        logger.info("Downloading %s from %s", data_key, oracle_cards_download_uri)
        before = time.monotonic()
        response = orjson.loads(self._session.get(oracle_cards_download_uri, timeout=30).content)
        logger.info("Downloaded %s from %s in %.3f seconds", data_key, oracle_cards_download_uri, time.monotonic() - before)
        try:
            with cache_file_path.open("w") as f:
                f.write(
                    orjson.dumps(
                        response,
                        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                    ).decode("utf-8"),
                )
        except FileNotFoundError:
            logger.error("Failed to write cache file: %s", cache_file_path)
        return response

    def setup_schema(self: APIResource, *_: object, **__: object) -> None:
        """Set up the database schema and apply migrations as needed."""
        if self._schema_setup_event.is_set():
            logger.info("Schema already setup (fastpath) in pid %d", os.getpid())
            return

        filesystem_migrations = db_utils.get_migrations()

        with self._import_guard:
            if self._schema_setup_event.is_set():
                logger.info("Schema already setup (slowpath) in pid %d", os.getpid())
                return
            logger.info("Setting up schema in pid %d", os.getpid())
            # read migrations from the db dir...
            # if any already applied migrations differ from what we want
            # to apply then drop everything
            with self._conn_pool.connection() as conn, conn.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS migrations")
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS migrations.migrations (
                        file_name text not null,
                        file_sha256 text not null,
                        date_applied timestamp default now(),
                        file_contents text not null
                    )""",
                )
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_migrations_filename ON migrations.migrations (file_name)")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_migrations_file_sha256 ON migrations.migrations USING HASH (file_sha256)",
                )

                cursor.execute("SELECT file_name, file_sha256 FROM migrations.migrations ORDER BY date_applied")
                applied_migrations = [dict(r) for r in cursor]

                already_applied = set()
                for applied_migration, fs_migration in zip(applied_migrations, filesystem_migrations, strict=False):
                    if applied_migration.items() <= fs_migration.items():
                        already_applied.add(applied_migration["file_sha256"])
                    else:
                        already_applied.clear()
                        cursor.execute("DELETE FROM migrations.migrations")
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
                            INSERT INTO migrations.migrations
                                (  file_name  ,   file_sha256  ,   file_contents  ) VALUES
                                (%(file_name)s, %(file_sha256)s, %(file_contents)s)""",
                        imigration,
                    )
                    conn.commit()

            self._schema_setup_event.set()
            logger.info("Schema setup complete in pid %d", os.getpid())

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

    def _preprocess_card(self: APIResource, card: dict[str, Any]) -> None | dict[str, Any]:  # noqa: C901, PLR0915
        """Preprocess a card to remove invalid cards and add necessary fields."""
        if card.get("preprocessed"):
            return card
        if set(card["legalities"].values()) == {"not_legal"}:
            return None
        if "paper" not in card["games"]:
            return None
        if card.get("set_type") == "funny":
            return None

        # Store the original card data before modifications for raw_card_blob
        raw_card_data = copy.deepcopy(card)
        card["preprocessed"] = True

        card_types, _, card_subtypes = (x.strip().split() for x in card.get("type_line", "").title().partition("\u2014"))
        card["card_types"] = card_types
        card["card_subtypes"] = card_subtypes or []  # Use empty array instead of None

        card["creature_power"] = maybe_int(card.get("power"))
        card["creature_toughness"] = maybe_int(card.get("toughness"))

        # objects of keys to true
        card["card_colors"] = dict.fromkeys(card["colors"], True)
        card["card_color_identity"] = dict.fromkeys(card["color_identity"], True)
        card["card_keywords"] = dict.fromkeys(card.get("keywords", []), True)
        card["produced_mana"] = dict.fromkeys(card.get("produced_mana", []), True)

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

        # Extract pricing data if available - ensure they are floats for jsonb_populate_record
        prices = card.get("prices", {})
        card["price_usd"] = maybe_float(prices.get("usd"))
        card["price_eur"] = maybe_float(prices.get("eur"))
        card["price_tix"] = maybe_float(prices.get("tix"))

        # Extract set code for dedicated column
        card["card_set_code"] = card.get("set")

        # Extract layout and border for dedicated columns (lowercased for case-insensitive search)
        if "layout" in card:
            card["card_layout"] = card["layout"].lower()
        if "border_color" in card:
            card["card_border"] = card["border_color"].lower()
        if "watermark" in card:
            card["card_watermark"] = card["watermark"].lower()

        mana_cost_text = card.get("mana_cost", "")
        card["mana_cost_jsonb"] = mana_cost_str_to_dict(mana_cost_text)

        # Map field names to match database column names for jsonb_populate_record
        card["card_name"] = card.get("name")
        card["mana_cost_text"] = card.get("mana_cost")
        card["creature_power_text"] = card.get("power")
        card["creature_toughness_text"] = card.get("toughness")
        card["card_artist"] = card.get("artist")
        card["raw_card_blob"] = raw_card_data  # Store the original card data

        # Handle CMC and edhrec_rank conversion using helper function
        card["cmc"] = maybe_int(card.get("cmc"))

        # Handle rarity conversion - implement in Python to avoid SQL boilerplate
        if card.get("rarity"):
            card["card_rarity_text"] = card["rarity"].lower()
            # Implement magic.rarity_text_to_int in Python
            rarity_map = {
                "common": 0,
                "uncommon": 1,
                "rare": 2,
                "mythic": 3,
                "special": 4,
                "bonus": 5,
            }
            card["card_rarity_int"] = rarity_map.get(card["card_rarity_text"], -1)

        # Handle collector number - implement extraction in Python to avoid SQL boilerplate
        collector_number = card.get("collector_number")
        card["collector_number"] = collector_number
        card["collector_number_int"] = extract_collector_number_int(collector_number)

        # Handle legalities and produced_mana defaults
        card.setdefault("card_legalities", card.get("legalities", {}))
        card.setdefault("produced_mana", {})

        # Ensure all NOT NULL DEFAULT fields are set to avoid constraint violations
        card.setdefault("card_oracle_tags", {})
        card.setdefault("card_is_tags", {})
        if "raw_card_blob" in card["raw_card_blob"]:
            msg = "raw_card_blob is not a dictionary"
            raise AssertionError(msg)

        return card

    def get_stats(self: APIResource, **_: object) -> dict[str, Any]:
        """Get stats about the cards."""
        to_insert = self.get_data()
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

            to_insert = self.get_data()

            # Use the consolidated loading method
            return self._load_cards_with_staging(to_insert)

    def search(  # noqa: PLR0913
        self: APIResource,
        *,
        falcon_response: falcon.Response | None = None,
        q: str | None = None,
        query: str | None = None,
        orderby: str | None = None,
        direction: str | None = None,
        limit: int = 100,
        unique: UniqueMode = UniqueMode.CARDS,
    ) -> dict[str, Any]:
        """Run a search query and return results and metadata.

        Args:
            falcon_response: The Falcon response object (unused).
            q: Query string (alternative to query parameter).
            query: Query string (alternative to q parameter).
            direction: Sort direction ('asc' or 'desc').
            limit: Maximum number of results to return.
            orderby: Field to sort by.
            unique: Unique mode to use for partitioning.

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
            unique=unique,
        )

    def _get_partition_sql(self: APIResource, unique: UniqueMode) -> str:
        """Generate the partitioning SQL based on unique mode.

        Args:
            unique: The unique mode to use for partitioning.

        Returns:
            SQL expression for partitioning (window function).
        """
        if unique == UniqueMode.CARDS:
            # Partition by card_id - return only one row per unique card
            return "ROW_NUMBER() OVER (PARTITION BY magic.cards.card_id ORDER BY magic.card_printings.card_printing_id) AS row_num"
        if unique == UniqueMode.PRINTINGS:
            # No partitioning - return all printings
            return "1 AS row_num"
        if unique == UniqueMode.ILLUSTRATIONS:
            # Partition by illustration_id - return only one row per unique illustration
            return "ROW_NUMBER() OVER (PARTITION BY magic.card_face_printings.illustration_id ORDER BY magic.card_printings.card_printing_id) AS row_num"
        msg = f"Unknown unique mode: {unique}"
        raise ValueError(msg)

    def _get_outer_orderby_sql(self: APIResource, orderby: str | None) -> str:
        """Generate the outer query ORDER BY field based on orderby parameter.

        Args:
            orderby: The orderby parameter from the request.

        Returns:
            SQL field name for the outer query ORDER BY clause.
        """
        outer_orderby_map = {
            "cmc": "cmc",
            "edhrec": "edhrec_rank",
            "power": "power_int",
            "rarity": "rarity_int",
            "toughness": "toughness_int",
            "usd": "price_usd",
        }
        return outer_orderby_map.get(orderby, "edhrec_rank")

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
        unique: UniqueMode = UniqueMode.CARDS,
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

        sql_direction = {
            "asc": "ASC",
            "desc": "DESC",
        }.get(direction, "ASC")

        # Generate partitioning SQL based on unique mode
        partition_sql = self._get_partition_sql(unique)
        full_query = rf"""
        SELECT
            card_id,
            name,
            mana_cost,
            oracle_text,
            artist,
            cmc,
            set_name,
            type_line,
            illustration_id,
            image_location_uuid,
            edhrec_rank,
            power_int,
            toughness_int,
            rarity_int,
            price_usd
        FROM (
            SELECT
                magic.artists.artist_name AS artist,
                magic.card_face_printings.illustration_id AS illustration_id,
                magic.card_faces.cmc,
                magic.card_faces.mana_cost_text AS mana_cost,
                magic.card_faces.oracle_text AS oracle_text,
                magic.card_faces.power_int AS power_int,
                magic.card_faces.toughness_int AS toughness_int,
                magic.card_faces.type_line AS type_line,
                magic.card_printings.rarity_int AS rarity_int,
                magic.card_sets.set_name AS set_name,
                magic.cards.card_id AS card_id,
                magic.cards.card_name AS name,
                magic.cards.edhrec_rank AS edhrec_rank,
                magic.prices.price_usd AS price_usd,
                substring(
                    magic.card_face_printings.image_uris->>'small',
                    '[0-9a-f\-]{{36}}'
                ) AS image_location_uuid,
                {partition_sql}
            FROM
                magic.cards
            JOIN magic.card_printings ON magic.cards.card_id = magic.card_printings.card_id
            JOIN magic.card_faces ON magic.cards.card_id = magic.card_faces.card_id
            JOIN magic.card_face_printings ON magic.card_faces.card_face_id = magic.card_face_printings.card_face_id AND magic.card_printings.card_printing_id = magic.card_face_printings.card_printing_id
            JOIN magic.card_sets ON magic.card_printings.set_code = magic.card_sets.set_code
            LEFT JOIN magic.prices ON magic.card_printings.card_printing_id = magic.prices.card_printing_id
            LEFT JOIN magic.illustration_artists ON magic.card_face_printings.illustration_id = magic.illustration_artists.illustration_id
            LEFT JOIN magic.artists ON magic.illustration_artists.artist_id = magic.artists.artist_id
            WHERE
                {where_clause}
        ) partitioned_results
        WHERE
            row_num = 1
        ORDER BY
            {self._get_outer_orderby_sql(orderby)} {sql_direction} NULLS LAST,
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
            COUNT(DISTINCT magic.cards.card_id) AS total_cards
        FROM
            magic.cards
        JOIN magic.card_printings ON magic.cards.card_id = magic.card_printings.card_id
        JOIN magic.card_faces ON magic.cards.card_id = magic.card_faces.card_id
        JOIN magic.card_face_printings ON magic.card_faces.card_face_id = magic.card_face_printings.card_face_id AND magic.card_printings.card_printing_id = magic.card_face_printings.card_printing_id
        JOIN magic.card_sets ON magic.card_printings.set_code = magic.card_sets.set_code
        LEFT JOIN magic.prices ON magic.card_printings.card_printing_id = magic.prices.card_printing_id
        LEFT JOIN magic.illustration_artists ON magic.card_face_printings.illustration_id = magic.illustration_artists.illustration_id
        LEFT JOIN magic.artists ON magic.illustration_artists.artist_id = magic.artists.artist_id
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
        cards = self._scryfall_search(query=f"oracletag:{tag}")
        card_names = [c["name"] for c in cards]

        if not cards:
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

    def _add_is_tag_to_cards(self: APIResource, *, is_tag: str) -> dict[str, Any]:
        """Add a specific is: tag to all cards matching that tag using Scryfall search.

        Args:
        ----
            is_tag (str): The is: tag to fetch and apply to cards (e.g., 'creature', 'spell').

        Returns:
        -------
            Dict[str, Any]: Result summary with updated card count and tag info.

        """
        if not is_tag:
            msg = "is_tag parameter is required"
            raise ValueError(msg)

        # Fetch cards with this is: tag from Scryfall API (handles pagination)
        cards = self._scryfall_search(query=f"is:{is_tag}")
        card_names = [c["name"] for c in cards]

        if not cards:
            return {
                "is_tag": is_tag,
                "cards_updated": 0,
                "message": f"No cards found with is:{is_tag} in Scryfall API",
            }

        logger.info("Updating %d cards with is:%s", len(card_names), is_tag)
        # Update cards in database with the new is: tag
        updated_count = 0
        card_names.sort()
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            # Use SQL update with jsonb concatenation to add the is: tag
            for card_name_batch in itertools.batched(card_names, 200):  # noqa: B911
                cursor.execute(
                    """
                    UPDATE magic.cards
                    SET card_is_tags = card_is_tags || %(new_tag)s::jsonb
                    WHERE card_name = ANY(%(card_names)s)
                    """,
                    {
                        "card_names": list(card_name_batch),
                        "new_tag": orjson.dumps({is_tag: True}).decode("utf-8"),
                    },
                )
                updated_count += cursor.rowcount
                conn.commit()

        return {
            "is_tag": is_tag,
            "cards_updated": updated_count,
            "total_cards_found": len(card_names),
            "message": f"Successfully updated {updated_count} cards with is:{is_tag}",
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

    def discover_is_tags_from_syntax(self: APIResource, **_: object) -> list[str]:
        """Discover all available is: tags from Scryfall syntax documentation.

        Returns:
        -------
            List[str]: List of all available is: tag names.

        Raises:
        ------
            ValueError: If API request fails or returns invalid data.

        """
        try:
            response = self._session.get("https://scryfall.com/docs/syntax", timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            msg = f"Failed to fetch is: tags from Scryfall syntax: {e}"
            raise ValueError(msg) from e

        # Extract is: tag names from the documentation
        # Look for patterns like "is:permanent", "is:spell", etc.
        is_tag_pattern = r"is:([a-zA-Z_-]+)"
        matches = re.findall(is_tag_pattern, response.text)

        # Remove duplicates and sort
        unique_is_tags = sorted({match.lower() for match in matches})

        logger.info("Discovered %d unique is: tags from Scryfall syntax", len(unique_is_tags))
        return unique_is_tags

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

    def import_all_is_tags(self: APIResource, **_: object) -> dict[str, Any]:
        """Discover and import all is: tags from Scryfall syntax documentation.

        Returns:
        -------
            Dict[str, Any]: Summary of the bulk is: tag import operation.

        """
        result = {
            "success": True,
        }
        logger.info("Starting bulk is: tag discovery and import")

        try:
            all_is_tags = self.discover_is_tags_from_syntax()
        except ValueError as e:
            result.update({
                "success": False,
                "error": str(e),
                "message": "Failed to discover is: tags from Scryfall syntax",
            })
            return result

        if not all_is_tags:
            return {
                "success": False,
                "message": "No is: tags discovered from Scryfall syntax",
            }

        # Import card associations for each is: tag
        start_time = time.monotonic()
        imported_tags = []
        failed_tags = []
        total_cards_updated = 0

        for idx, is_tag in enumerate(all_is_tags):
            try:
                if idx > 0:
                    elapsed_time = time.monotonic() - start_time
                    fraction_complete = idx / len(all_is_tags)
                    estimated_time_remaining = (elapsed_time / fraction_complete) - elapsed_time
                    estimated_duration = datetime.timedelta(seconds=round(estimated_time_remaining, 1))
                    logger.info(
                        "Importing is: tag %d of %d: %20s (ETA: %s)",
                        idx + 1,
                        len(all_is_tags),
                        is_tag,
                        estimated_duration,
                    )

                tag_result = self._add_is_tag_to_cards(is_tag=is_tag)
                imported_tags.append({
                    "is_tag": is_tag,
                    "cards_updated": tag_result["cards_updated"],
                    "total_cards_found": tag_result["total_cards_found"],
                })
                total_cards_updated += tag_result["cards_updated"]

            except ValueError as e:
                logger.warning("Failed to import is: tag '%s': %s", is_tag, e)
                failed_tags.append({"is_tag": is_tag, "error": str(e)})

        result.update({
            "duration": time.monotonic() - start_time,
            "discovered_is_tags": len(all_is_tags),
            "imported_is_tags": len(imported_tags),
            "failed_is_tags": len(failed_tags),
            "total_cards_updated": total_cards_updated,
            "imported_tags": imported_tags,
            "failed_tags": failed_tags,
            "message": f"Successfully imported {len(imported_tags)} is: tags, {len(failed_tags)} failed",
        })

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

    def backfill_mana_cost_jsonb(self: APIResource, **_: object) -> None:
        """Backfill the mana_cost_jsonb column with the mana_cost_text column."""
        logger.info("Backfilling mana_cost_jsonb column with mana_cost_text column")
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            cursor = typecast("Cursor", cursor)
            cursor.execute("SELECT mana_cost_text FROM magic.cards GROUP BY mana_cost_text")
            for mana_cost_text in cursor.fetchall():
                mana_cost_jsonb = mana_cost_str_to_dict(mana_cost_text["mana_cost_text"])
                cursor.execute(
                    query="UPDATE magic.cards SET mana_cost_jsonb = %(mana_cost_jsonb)s WHERE mana_cost_text = %(mana_cost_text)s",
                    params={
                        "mana_cost_jsonb": db_utils.maybe_json(mana_cost_jsonb),
                        "mana_cost_text": mana_cost_text["mana_cost_text"],
                    },
                )
            conn.commit()
        return {
            "status": "success",
            "message": "Mana cost jsonb backfilled successfully",
        }

    def backfill_card_frame_data(self: APIResource, **_: object) -> dict[str, Any]:
        """Backfill the card_frame_data column from raw_card_blob frame data."""
        logger.info("Backfilling card_frame_data column from raw_card_blob")
        updated_count = 0
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            cursor = typecast("Cursor", cursor)
            # Select unique combinations of frame and frame_effects for efficient batch processing
            cursor.execute("""
                SELECT
                    raw_card_blob->>'frame' AS frame,
                    raw_card_blob->'frame_effects' AS frame_effects
                FROM
                    magic.cards
                WHERE
                    (raw_card_blob ? 'frame' OR raw_card_blob ? 'frame_effects')
                GROUP BY 1, 2
            """)

            for row in cursor.fetchall():
                # Build raw card data for frame extraction
                raw_card = {}
                if row["frame"] is not None:
                    raw_card["frame"] = row["frame"]
                if row["frame_effects"] is not None:
                    raw_card["frame_effects"] = row["frame_effects"]

                frame_data = extract_frame_data_from_raw_card(raw_card)
                if frame_data is None:
                    continue

                logger.info("Updating frame: frame_data=%s, row=%s", frame_data, row)
                cursor.execute(
                    query=rewrap("""
                        UPDATE
                            magic.cards
                        SET
                            card_frame_data = %(frame_data)s
                        WHERE
                            raw_card_blob->>'frame' IS NOT DISTINCT FROM %(frame)s AND
                            raw_card_blob->'frame_effects' IS NOT DISTINCT FROM %(frame_effects)s
                    """),
                    params={
                        "frame": db_utils.maybe_json(row["frame"]),
                        "frame_data": db_utils.maybe_json(frame_data),
                        "frame_effects": db_utils.maybe_json(row["frame_effects"]),
                    },
                )
                updated_count += cursor.rowcount

            conn.commit()

        return {
            "status": "success",
            "message": f"Card frame data backfilled successfully. Updated {updated_count} cards.",
            "updated_count": updated_count,
        }

    def export_card_data(self: APIResource, **_: object) -> dict[str, Any]:
        """Export card data tables to JSON files for backup/re-import.

        Exports the three main tables:
        - magic.cards
        - magic.tags
        - magic.tag_relationships

        Files are saved to /data/api/exports/{timestamp}/ directory.

        Returns:
        -------
            Dict[str, Any]: Export result with status, file paths, and counts.
        """
        logger.info("Starting card data export")

        # Create timestamped export directory
        timestamp = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d_%H%M%S")
        export_dir = pathlib.Path("/data/api/exports") / timestamp
        export_dir.mkdir(parents=True, exist_ok=True)

        try:
            with self._conn_pool.connection() as conn, conn.cursor() as cursor:
                cursor = typecast("Cursor", cursor)

                export_results = {
                    "cards": self._export_cards_table(cursor, export_dir),
                    "tags": self._export_tags_table(cursor, export_dir),
                    "tag_relationships": self._export_tag_relationships_table(cursor, export_dir),
                }

                logger.info("Export completed successfully to %s", export_dir)
                return {
                    "status": "success",
                    "export_directory": str(export_dir),
                    "timestamp": timestamp,
                    "results": export_results,
                    "message": "Successfully exported cards, tags, and tag relationships",
                }

        except (OSError, psycopg.Error, ValueError) as e:
            logger.error("Failed to export card data: %s", e)
            return {
                "status": "error",
                "message": f"Export failed: {e}",
            }

    def _export_cards_table(self: APIResource, cursor: Cursor, export_dir: pathlib.Path) -> dict[str, Any]:
        """Export magic.cards table to JSON file."""
        cards_file = export_dir / "cards.json"
        logger.info("Exporting magic.cards table to %s file", cards_file)
        cursor.execute("SELECT * FROM magic.cards ORDER BY card_name")

        cards_data = [dict(row) for row in cursor.fetchall()]
        cards_count = len(cards_data)

        # Write JSON file
        with cards_file.open("w", encoding="utf-8") as f:
            f.write(orjson.dumps(cards_data, option=orjson.OPT_INDENT_2).decode("utf-8"))

        logger.info("Exported magic.cards table to %s file", cards_file)
        return {"file": str(cards_file), "count": cards_count}

    def _export_tags_table(self: APIResource, cursor: Cursor, export_dir: pathlib.Path) -> dict[str, Any]:
        """Export magic.tags table to JSON file."""
        tags_file = export_dir / "tags.json"
        logger.info("Exporting tags table to %s file", tags_file)
        cursor.execute("SELECT tag FROM magic.tags ORDER BY tag")

        tags_data = [dict(row) for row in cursor.fetchall()]
        tags_count = len(tags_data)

        # Write JSON file
        with tags_file.open("w", encoding="utf-8") as f:
            f.write(orjson.dumps(tags_data, option=orjson.OPT_INDENT_2).decode("utf-8"))

        logger.info("Exported tags table to %s file", tags_file)
        return {"file": str(tags_file), "count": tags_count}

    def _export_tag_relationships_table(self: APIResource, cursor: Cursor, export_dir: pathlib.Path) -> dict[str, Any]:
        """Export magic.tag_relationships table to JSON file."""
        relationships_file = export_dir / "tag_relationships.json"
        logger.info("Exporting tag_relationships table to %s file", relationships_file)
        cursor.execute("""
            SELECT child_tag, parent_tag
            FROM magic.tag_relationships
            ORDER BY child_tag, parent_tag
        """)

        relationships_data = [dict(row) for row in cursor.fetchall()]
        relationships_count = len(relationships_data)

        # Write JSON file
        with relationships_file.open("w", encoding="utf-8") as f:
            f.write(orjson.dumps(relationships_data, option=orjson.OPT_INDENT_2).decode("utf-8"))

        logger.info("Exported tag_relationships table to %s file", relationships_file)
        return {"file": str(relationships_file), "count": relationships_count}

    def import_card_data(self: APIResource, *, timestamp: str | None = None, **_: object) -> dict[str, Any]:
        """Import card data from JSON files, truncating existing data.

        Imports data from /data/api/exports/{timestamp}/ directory.
        If timestamp is not provided, uses the most recent export.

        Args:
        ----
            timestamp (str, optional): Timestamp of export to import. If None, uses latest.

        Returns:
        -------
            Dict[str, Any]: Import result with status and counts.
        """
        logger.info("Starting card data import")

        try:
            import_dir, timestamp = self._find_import_directory(timestamp)
            self._validate_import_files(import_dir)

            logger.info("Importing from directory: %s", import_dir)

            with self._conn_pool.connection() as conn, conn.cursor() as cursor:
                cursor = typecast("Cursor", cursor)
                conn.autocommit = False

                try:
                    import_results = self._perform_import(cursor, import_dir)
                    conn.commit()

                    logger.info("Import completed successfully")
                    return {
                        "status": "success",
                        "timestamp": timestamp,
                        "import_directory": str(import_dir),
                        "results": import_results,
                        "message": f"Successfully imported {import_results['cards']} cards, {import_results['tags']} tags, and {import_results['tag_relationships']} tag relationships",
                    }

                except (OSError, psycopg.Error, ValueError) as e:
                    conn.rollback()
                    raise e
                finally:
                    conn.autocommit = True

        except (OSError, psycopg.Error, ValueError) as e:
            logger.error("Failed to import card data: %s", e)
            return {
                "status": "error",
                "message": f"Import failed: {e}",
            }

    def _find_import_directory(self: APIResource, timestamp: str | None) -> tuple[pathlib.Path, str]:
        """Find and validate the import directory."""
        exports_dir = pathlib.Path("/data/api/exports")
        if not exports_dir.exists():
            msg = "No exports directory found at /data/api/exports"
            raise ValueError(msg)

        if timestamp:
            import_dir = exports_dir / timestamp
            if not import_dir.exists():
                msg = f"Export directory for timestamp {timestamp} not found"
                raise ValueError(msg)
        else:
            # Find most recent export
            try:
                import_dir = max(
                    (d for d in exports_dir.iterdir() if d.is_dir()),
                    key=lambda d: d.name,
                )
            except ValueError:
                msg = "No export directories found"
                raise ValueError(msg) from None
            timestamp = import_dir.name

        return import_dir, timestamp

    def _validate_import_files(self: APIResource, import_dir: pathlib.Path) -> None:
        """Validate that all required import files exist."""
        required_files = [
            ("cards.json", import_dir / "cards.json"),
            ("tags.json", import_dir / "tags.json"),
            ("tag_relationships.json", import_dir / "tag_relationships.json"),
        ]

        missing_files = [name for name, file_path in required_files if not file_path.exists()]

        if missing_files:
            msg = f"Missing required files: {', '.join(missing_files)}"
            raise ValueError(msg)

    def _perform_import(self: APIResource, cursor: Cursor, import_dir: pathlib.Path) -> dict[str, int]:
        """Perform the actual import operation."""
        # Delete data from tables in correct order (respecting foreign keys)
        logger.info("Deleting existing data")
        cursor.execute("DELETE FROM magic.tag_relationships")
        cursor.execute("DELETE FROM magic.tags")
        cursor.execute("DELETE FROM magic.cards")

        import_results = {}

        # Import tags first (no dependencies)
        logger.info("Importing tags")
        tags_file = import_dir / "tags.json"
        with tags_file.open("r", encoding="utf-8") as f:
            tags_data = orjson.loads(f.read())

        for tag_record in tags_data:
            cursor.execute("INSERT INTO magic.tags (tag) VALUES (%(tag)s)", tag_record)

        cursor.execute("SELECT COUNT(*) FROM magic.tags")
        import_results["tags"] = cursor.fetchone()["count"]

        # Import tag relationships (depends on tags)
        logger.info("Importing tag relationships")
        relationships_file = import_dir / "tag_relationships.json"
        with relationships_file.open("r", encoding="utf-8") as f:
            relationships_data = orjson.loads(f.read())

        for relationship_record in relationships_data:
            cursor.execute(
                "INSERT INTO magic.tag_relationships (child_tag, parent_tag) VALUES (%(child_tag)s, %(parent_tag)s)",
                relationship_record,
            )

        cursor.execute("SELECT COUNT(*) FROM magic.tag_relationships")
        import_results["tag_relationships"] = cursor.fetchone()["count"]

        # Import cards last (largest table)
        logger.info("Importing cards")
        cards_file = import_dir / "cards.json"
        with cards_file.open("r", encoding="utf-8") as f:
            cards_data = orjson.loads(f.read())

        num_cards = len(cards_data)
        page_size = 750
        num_imported = 0
        # Import cards in batches using jsonb_populate_record
        for card_batch in itertools.batched(cards_data, page_size):  # noqa: B911
            batch_json = orjson.dumps(card_batch).decode("utf-8")
            cursor.execute("""
                INSERT INTO magic.cards
                SELECT
                    (jsonb_populate_record(null::magic.cards, value)).*
                FROM
                    jsonb_array_elements(%s::jsonb)
            """, (batch_json,))
            num_imported += cursor.rowcount
            logger.info(
                "Imported %s of %s cards (%.1f%%)",
                f"{num_imported:,}",
                f"{num_cards:,}",
                num_imported / num_cards * 100,
            )

        cursor.execute("SELECT COUNT(*) FROM magic.cards")
        import_results["cards"] = cursor.fetchone()["count"]

        return import_results

    def _load_cards_with_staging(  # noqa: PLR0915
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

        self.setup_schema()

        cards = [
            c
            for c in cards
            if include_card(c)
        ]

        if not cards:
            return {
                "status": "no_cards_after_preprocessing",
                "cards_loaded": 0,
                "sample_cards": [],
                "message": "No cards remaining after preprocessing",
            }

        # from cards... extract...
        data = [
            ("artists", extract_artists(cards)),
            ("illustrations", extract_illustrations(cards)),
            ("illustration_artists", extract_illustration_artists(cards)),
            ("cards", extract_cards(cards)),
            ("card_sets", extract_card_sets(cards)),
            ("card_printings", extract_card_printings(cards)),
            ("card_faces", extract_card_faces(cards)),
            ("card_face_printings", extract_card_face_printings(cards)),
        ]
        del cards

        # Generate random staging table name
        staging_suffix = secrets.token_hex(8)
        staging_table_name = f"import_staging_{staging_suffix}"

        sample_data = {}
        items_loaded_obj = {}
        target_table = "undefined"

        try:
            with self._conn_pool.connection() as conn, conn.cursor() as cursor:
                statement_timeout = 30_000
                cursor.execute(f"set statement_timeout = {statement_timeout}")
                # Create staging table with unique name

                for target_table, table_data in data:
                    cursor.execute(f"CREATE TEMPORARY TABLE {staging_table_name} (item_blob jsonb)")
                    items_loaded = 0
                    page_size = 200_000 // 9
                    for item_batch in itertools.batched(table_data, page_size):  # noqa: B911
                        # Load cards into staging table using COPY for efficiency
                        with cursor.copy(f"COPY {staging_table_name} (item_blob) FROM STDIN WITH (FORMAT csv, HEADER false)") as copy_filehandle:
                            writer = csv.writer(copy_filehandle, quoting=csv.QUOTE_ALL)
                            writer.writerows([orjson.dumps(item, option=orjson.OPT_SORT_KEYS).decode("utf-8")] for item in item_batch)
                        items_loaded += len(item_batch)
                        logger.info(
                            "Loaded %s of %s items into magic.%s (%.1f%%)...",
                            f"{items_loaded:8,}",
                            f"{len(table_data):8,}",
                            target_table,
                            items_loaded / len(table_data) * 100,
                        )

                    # Get random sample before transfer (up to 10 cards)
                    target_sample_size = 10
                    sample_data[target_table] = []
                    random_threshold = 2 * target_sample_size / len(table_data)
                    cursor.execute(f"""
                        SELECT
                            (jsonb_populate_record(null::magic.{target_table}, item_blob)).*
                        FROM
                            {staging_table_name}
                        WHERE
                            RANDOM() < {random_threshold}
                        ORDER BY RANDOM()
                        LIMIT {target_sample_size}""",
                    )
                    sample_data[target_table] = [dict(r) for r in cursor.fetchall()]

                    # Transfer from staging to main table using direct jsonb_populate_record
                    transfer_query = f"""
                        INSERT INTO magic.{target_table}
                        SELECT
                            (jsonb_populate_record(null::magic.{target_table}, item_blob)).*
                        FROM
                            {staging_table_name}
                        ON CONFLICT DO NOTHING
                    """

                    cursor.execute(transfer_query)
                    items_loaded_obj[target_table] = cursor.rowcount

                    # Drop the staging table
                    cursor.execute(f"DROP TABLE {staging_table_name}")
                    cursor.execute(f"SELECT SUM(1) AS total_items FROM magic.{target_table}")
                    total_items = cursor.fetchone()["total_items"]
                    logger.info("Total items in magic.%s: %s", target_table, total_items)

                conn.commit()

                total_items_loaded = sum(items_loaded_obj.values())
                result = {
                    "status": "success",
                    "items_loaded": items_loaded_obj,
                    "sample_items": sample_data,
                    "message": f"Successfully loaded {total_items_loaded} items",
                }

                # Clear caches when items are successfully loaded
                if total_items_loaded > 0:
                    self._query_cache.clear()
                    # Clear the search cache by accessing its cache attribute
                    if hasattr(self._search, "cache"):
                        self._search.cache.clear()

                return result

        except (psycopg.Error, ValueError, KeyError) as oops:
            logger.error(
                "Error loading %s with staging table %s: %s",
                target_table,
                staging_table_name,
                oops,
            )
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
                "message": f"Error loading cards: {oops}",
            }
