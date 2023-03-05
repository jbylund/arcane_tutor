"""Implementation of the routes of our simple api"""
import collections
import csv
import io
import json
import logging
import os
import time
from urllib.parse import urlparse

import requests
import falcon
import psycopg2
import psycopg2.extras
import psycopg2.pool
from psycopg2.extensions import register_adapter

register_adapter(dict, psycopg2.extras.Json)

logger = logging.getLogger("apiresource")

# pylint: disable=c-extension-no-member


class APIResource:
    """Class implementing request handling for our simple api"""

    def __init__(self):
        """Initialize an APIResource object"""
        self._tablename = "staging_table"

        # make a connection pool
        self._conn_pool = self._make_pool()

        # set up routing
        self.action_map = {
            "db_ready": self.db_ready,
            "get_cards_to_csv": self.get_cards_to_csv,
            "get_cards": self.get_cards,
            "import": self.import_data,
            "pid": self.get_pid,
        }

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

    @staticmethod
    def _get_pg_creds():
        return {k[2:].lower(): v for k, v in os.environ.items() if k.startswith("PG")}

    def _make_pool(self):
        creds = self._get_pg_creds()
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
                logger.info("Got postgres connection in pid %d...", os.getpid())
                return pool
            except psycopg2.OperationalError:
                if deadline < time.time():
                    raise
                time.sleep(3)

    def raise_not_found(self, **_):
        raise falcon.HTTPNotFound(description={"routes": {k: v.__doc__ for k, v in self.action_map.items()}})

    def _run_query(self, query, params=None, explain=True):
        params = params or {}
        query = " ".join(query.strip().split())
        explain_query = f"EXPLAIN (FORMAT JSON) {query}"

        conn = self._conn_pool.getconn()
        conn.cursor_factory = psycopg2.extras.DictCursor
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("set statement_timeout = 10000")
        result = {}
        try:
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
        finally:
            self._conn_pool.putconn(conn)
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
        if os.path.exists(cache_file):
            with open(cache_file) as f:
                response = json.load(f)
        else:
            session = requests.Session()
            response = session.get("https://api.scryfall.com/bulk-data").json()["data"]
            by_type = {r["type"]: r for r in response}
            oracle_cards_download_uri = by_type["oracle_cards"]["download_uri"]
            response = requests.get(oracle_cards_download_uri).json()
            with open(cache_file, "w") as f:
                json.dump(response, f, indent=4, sort_keys=True)

        return response

    def import_data(self, **_):
        """Import data from scryfall"""
        response = self.get_data()

        conn = self._conn_pool.getconn()
        conn.cursor_factory = psycopg2.extras.DictCursor
        conn.autocommit = True
        cursor = conn.cursor()

        try:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS magic")
            cursor.execute("DROP TABLE IF EXISTS magic.cards")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS magic.cards (
                    cmc integer,
                    mana_cost text,
                    name text,
                    other jsonb,
                    power integer,
                    power_txt text,
                    prices jsonb,
                    toughness integer,
                    toughness_txt text,
                    type_line text
                )
                """
            )
            cursor.execute("TRUNCATE TABLE magic.cards")
            cursor.execute("CREATE UNIQUE INDEX ON magic.cards (name)")
            for idx, row in enumerate(response):
                if row["legalities"]["vintage"] == "not_legal":
                    continue
                if not idx % 10:
                    logger.info("Importing row %d...", idx)
                for intkey in ["power", "toughness"]:
                    val = row.pop(intkey, None)
                    if val:
                        try:
                            row[intkey] = int(val)
                            row[f"{intkey}_txt"] = None
                        except ValueError:
                            row[f"{intkey}_txt"] = val
                            row[intkey] = None
                for key in [
                    "mana_cost",
                    "power_txt", 
                    "power", 
                    "toughness_txt",
                    "toughness", 
                ]:
                    row.setdefault(key, None)

                cursor.execute(
                    f"""
                    INSERT INTO magic.cards (
                        cmc,
                        mana_cost,
                        name,
                        power,
                        power_txt,
                        prices,
                        toughness,
                        toughness_txt,
                        type_line
                    )
                    VALUES (
                        %(cmc)s,
                        %(mana_cost)s,
                        %(name)s,
                        %(power)s,
                        %(power_txt)s,
                        %(prices)s,
                        %(toughness)s,
                        %(toughness_txt)s,
                        %(type_line)s
                    )
                    """,
                    row,
                )
        finally:
            self._conn_pool.putconn(conn)

        key_frequency = collections.Counter()
        for card in response:
            key_frequency.update(k for k, v in card.items() if v)
        print(json.dumps(dict(key_frequency.most_common(100)), indent=4))

    def get_cards(self, min_name=None, max_name=None, limit=2500, **_):
        """Get cards by name"""
        return self._run_query(
            """
            SELECT
                cmc,
                mana_cost,
                name,
                power,
                (prices->>'usd')::double precision AS price,
                toughness,
                type_line
            FROM 
                magic.cards
            WHERE
                (%(min_name)s IS NULL OR %(min_name)s < name) AND
                (%(max_name)s IS NULL OR name < %(max_name)s)
            ORDER BY
                name
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
        raw_cards = self.get_cards(min_name=min_name, max_name=max_name, limit=limit)
        falcon_response.content_type = "text/csv"

        str_buffer = io.StringIO()
        writer = csv.DictWriter(str_buffer, fieldnames=raw_cards[0].keys())
        writer.writeheader()
        writer.writerows(raw_cards)
        str_buffer.seek(0)
        val = str_buffer.getvalue()
        falcon_response.body = val.encode("utf-8")