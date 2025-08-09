"""Implementation of the routes of our simple api"""

import collections
import csv
import hashlib
import inspect
import io
import json
import logging
import os
import pathlib
import time
import traceback
from sys import exception
from urllib.parse import urlparse

import falcon
import psycopg2
import psycopg2.extras
import psycopg2.pool
import requests
from parsing import generate_sql_query, parse_search_query
from psycopg2.extensions import register_adapter

register_adapter(dict, psycopg2.extras.Json)
register_adapter(list, psycopg2.extras.Json)

logger = logging.getLogger("apiresource")

# pylint: disable=c-extension-no-member


class DBCtx:
    def __init__(self, conn_pool):
        self._conn_pool = conn_pool

    def __enter__(self):
        self.conn = conn = self._conn_pool.getconn()
        conn.cursor_factory = psycopg2.extras.DictCursor
        conn.autocommit = True
        return conn.cursor()

    def __exit__(self, err_type, err_val, err_tb):
        if err_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self._conn_pool.putconn(self.conn)


class BorrowingPool:
    def __init__(self, *, pool):
        self.pool = pool

    def borrow(self):
        # return a thing that has an enter and an exit...
        # enter checks out of the pool
        # exit puts it back
        return DBCtx(self.pool)

    def __repr__(self):
        return f"<BorrowingPool({self.pool})>"


def _make_pool() -> BorrowingPool:
    def _get_pg_creds():
        return {k[2:].lower(): v for k, v in os.environ.items() if k.startswith("PG")}

    def make_pool():
        creds = _get_pg_creds()
        max_connections = 1
        standby_connections = 1

        deadline = time.time() + 30
        while True:
            try:
                pool = psycopg2.pool.ThreadedConnectionPool(
                    standby_connections,
                    max_connections,
                    "application_name=api",
                    **creds,
                )
                logger.info("Created pool in pid %d...", os.getpid())
                return BorrowingPool(pool=pool)
            except psycopg2.OperationalError as oops:
                logger.warning("Failed to get connection %s", oops)
                if deadline < time.time():
                    raise
                time.sleep(3)

    return make_pool()


def get_migrations():
    # generate migrations + their hashes
    here = pathlib.Path(__file__).parent
    migrations_dir = here / "db"
    migrations = []
    for dirname, child_dirs, child_files in migrations_dir.walk():
        for ichild in sorted(child_files):
            if not ichild.lower().endswith(".sql"):
                continue
            fullpath = dirname / ichild
            with open(fullpath) as filehandle:
                contents = filehandle.read().strip()
            migrations.append(
                {
                    "file_contents": contents,
                    "file_md5": hashlib.md5(contents.encode()).hexdigest(),
                    "file_name": ichild,
                }
            )
    return migrations


def can_serialize(iobj):
    try:
        s = json.dumps(iobj)
        return len(s) < 16000
    except:
        return False
    return True


class APIResource:
    """Class implementing request handling for our simple api"""

    def __init__(self):
        """Initialize an APIResource object"""
        self._tablename = "staging_table"

        # make a connection pool
        self._conn_pool = _make_pool()

        # set up routing
        # self.action_map = {
        #     "db_ready": self.db_ready,
        #     "get_cards_to_csv": self.get_cards_to_csv,
        #     "get_cards": self.get_cards,
        #     "import": self.import_data,
        #     "pid": self.get_pid,
        #     "search": self.search,
        # }
        self.action_map = {x: getattr(self, x) for x in dir(self) if not x.startswith("_")}
        logger.info("Worker with pid has conn pool %s", self._conn_pool)

    def handle(self, req: falcon.Request, resp: falcon.Response, **_kwargs):
        """Handle a request"""
        parsed = urlparse(req.uri)
        path = parsed.path.strip("/")

        if path not in ("db_ready", "pid"):
            logger.info("Handling request for %s", req.uri)

        action = self.action_map.get(path, self.raise_not_found)
        try:
            res = action(falcon_response=resp, **req.params)
            resp.media = res
        except TypeError as oops:
            logger.error("Error handling request: %s", oops, exc_info=True)
            raise falcon.HTTPBadRequest(description=str(oops))
        except falcon.HTTPError as oops:
            raise
        except Exception as oops:
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
                    }
                )

            raise falcon.HTTPInternalServerError(
                title="Server Error",
                description={
                    "exception": str(oops),
                    "stack_info": stack_info,
                },
            )

    def raise_not_found(self, **_):
        raise falcon.HTTPNotFound(title="Not Found", description={"routes": {k: v.__doc__ for k, v in self.action_map.items()}})

    def _run_query(self, query, params=None, explain=True):
        params = params or {}
        query = " ".join(query.strip().split())
        explain_query = f"EXPLAIN (FORMAT JSON) {query}"

        result = {}
        with self._conn_pool.borrow() as cursor:
            cursor.execute("set statement_timeout = 10000")

            if explain:
                cursor.execute(explain_query, params)
                for row in cursor:
                    result["plan"] = row
            before = time.time()
            cursor.execute(query, params)
            duration = time.time() - before
            result["duration"] = duration
            result["frequency"] = 1 / duration
            result["result"] = [dict(r) for r in cursor]
        return result

    def get_pid(self, **_):
        """Just return the pid of the process which served this request"""
        return os.getpid()

    def db_ready(self, **_):
        """Return true if the db is ready"""
        records = self._run_query("SELECT relname FROM pg_stat_user_tables")["result"]
        existing_tables = set(r["relname"] for r in records)
        return "migrations" in existing_tables

    def get_data(self):
        cache_file = "/data/api/foo.json"
        try:
            with open(cache_file) as f:
                response = json.load(f)
        except FileNotFoundError as oops:
            logger.info("Cache miss!")
            session = requests.Session()
            response = session.get("https://api.scryfall.com/bulk-data").json()["data"]
            by_type = {r["type"]: r for r in response}
            oracle_cards_download_uri = by_type["oracle_cards"]["download_uri"]
            response = requests.get(oracle_cards_download_uri).json()
            with open(cache_file, "w") as f:
                json.dump(response, f, indent=4, sort_keys=True)
        else:
            logger.info("Cache hit!")

        return response

    def _setup_schema(self):
        # read migrations from the db dir...
        # if any already applied migrations differ from what we want
        # to apply then drop everything
        with self._conn_pool.borrow() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS migrations (
                file_name text not null, 
                file_md5 text not null, 
                date_applied timestamp default now(),
                file_contents text not null
            )"""
            )
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_migrations_filename ON migrations (file_name)")
            cursor.execute("CREATE        INDEX IF NOT EXISTS idx_migrations_filemd5 ON migrations USING HASH (file_md5)")

            cursor.execute("SELECT file_name, file_md5 FROM migrations ORDER BY date_applied")
            applied_migrations = [dict(r) for r in cursor]
            filesystem_migrations = get_migrations()

            already_applied = set()
            for applied_migration, fs_migration in zip(applied_migrations, filesystem_migrations):
                if applied_migration.items() <= fs_migration.items():
                    already_applied.add(applied_migration["file_md5"])
                else:
                    already_applied.clear()
                    cursor.execute("DELETE FROM migrations")
                    cursor.execute("DROP SCHEMA IF EXISTS magic CASCADE")

            for imigration in filesystem_migrations:
                file_md5 = imigration["file_md5"]
                if file_md5 in already_applied:
                    logger.info("%s was already applied...", imigration["file_name"])
                    continue
                logger.info("Applying %s ...", imigration["file_name"])
                cursor.execute(imigration["file_contents"])
                cursor.execute(
                    """
                    INSERT INTO migrations 
                        (  file_name  ,   file_md5  ,   file_contents  ) VALUES 
                        (%(file_name)s, %(file_md5)s, %(file_contents)s)""",
                    imigration,
                )

    def import_data(self, **_):
        """Import data from scryfall"""
        self._setup_schema()
        response = self.get_data()

        # card_name | cmc | mana_cost_text | mana_cost_jsonb | raw_card_blob | card_types | card_subtypes | card_colors | creature_power | creature_power_text | creature_toughness | creature_toughness_text
        # type_line	: "Legendary Creature — Elf Druid"
        last_log = 0
        key_frequency = collections.Counter()
        with self._conn_pool.borrow() as cursor:
            for idx, card in enumerate(response):
                if set(card["legalities"].values()) == {"not_legal"}:
                    continue
                if "card_faces" in card:
                    continue
                if card.get("set_type") == "funny":
                    continue
                if 1 < time.monotonic() - last_log:
                    logger.info("Imported %d cards...", idx)
                    last_log = time.monotonic()
                key_frequency.update(k for k, v in card.items() if v)
                card_types, _, card_subtypes = [x.strip().split() for x in card.get("type_line", "").title().partition("\u2014")]
                card["card_types"] = card_types
                card["card_subtypes"] = card_subtypes or None
                for creature_field in ["power", "toughness"]:
                    val = card.setdefault(creature_field, None)
                    try:
                        numeric_val = int(val)
                    except:
                        card[f"{creature_field}_numeric"] = None
                    else:
                        card[f"{creature_field}_numeric"] = numeric_val

                cursor.execute(
                    """
                    INSERT INTO magic.cards 
                    ( card_name,   cmc  , mana_cost_text, mana_cost_jsonb, raw_card_blob,     card_types  ,   card_subtypes  , card_colors, creature_power, creature_power_text, creature_toughness, creature_toughness_text ) VALUES
                    ( %(name)s , %(cmc)s, %(mana_cost)s ,          null  ,    %(blob)s  ,    %(card_types)s, %(card_subtypes)s,     %(colors)s,     %(power_numeric)s,             %(power)s,             %(toughness_numeric)s   ,   %(toughness)s)
                    ON CONFLICT (card_name) DO NOTHING
                    """,
                    card | {"blob": card},
                )
        print(json.dumps(dict(key_frequency.most_common(100)), indent=4))

    def get_cards(self, min_name=None, max_name=None, limit=2500, **_):
        """Get cards by name"""
        return self._run_query(
            """
            SELECT
                *
            FROM 
                magic.cards
            WHERE
                (%(min_name)s IS NULL OR %(min_name)s < card_name) AND
                (%(max_name)s IS NULL OR card_name < %(max_name)s)
            ORDER BY
                card_name
            LIMIT
                %(limit)s
            """,
            {
                "min_name": min_name,
                "max_name": max_name,
                "limit": limit,
            },
        )["result"]

    def get_cards_to_csv(self, min_name=None, max_name=None, limit=2500, falcon_response=None, **_):
        if falcon_response is None:
            raise ValueError("falcon_response is required")
        raw_cards = self.get_cards(min_name=min_name, max_name=max_name, limit=limit)
        falcon_response.content_type = "text/csv"

        str_buffer = io.StringIO()
        writer = csv.DictWriter(str_buffer, fieldnames=raw_cards[0].keys())
        writer.writeheader()
        writer.writerows(raw_cards)
        str_buffer.seek(0)
        val = str_buffer.getvalue()
        falcon_response.body = val.encode("utf-8")

    def search(self, *, query=None, **_):
        """Run a query"""
        parsed_query = parse_search_query(query)
        compiled_query = generate_sql_query(parsed_query)
        return {
            "query": query,
            "parsed": parsed_query,
            "compiled": compiled_query,
        }
