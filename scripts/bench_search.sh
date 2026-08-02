#!/usr/bin/env bash
# Benchmark engine vs SQL search paths.
#
# Finds a running sylvan apiservice container and runs the benchmark inside it
# so both the DB connection and card_engine module are available.
#
# Usage:
#   ./scripts/bench_search.sh              # auto-pick blue or green container
#   ./scripts/bench_search.sh sylvan_blue  # pick a specific project

set -euo pipefail

PROJECT="${1:-}"

if [[ -n "$PROJECT" ]]; then
    CONTAINER=$(docker ps --format '{{.Names}}' | grep "${PROJECT}-apiservice" | head -1)
else
    CONTAINER=$(docker ps --format '{{.Names}}' | grep "apiservice" | head -1)
fi

if [[ -z "$CONTAINER" ]]; then
    echo "No running apiservice container found." >&2
    exit 1
fi

echo "Running benchmark in: $CONTAINER"
echo ""

docker exec -i "$CONTAINER" python3 - << 'PYEOF'
"""Benchmark: engine vs SQL search paths."""
from __future__ import annotations

import math, multiprocessing, sys, time
sys.path.insert(0, "/app")

from api.api_resource import APIResource
from api.enums import CardOrdering, PreferOrder, SortDirection, UniqueOn
from api.parsing import generate_sql_query, parse_scryfall_query
from api.utils.timer import Timer

QUERIES = [
    ("name:soldier",                                    CardOrdering.EDHREC),
    ("t:merfolk and name:tide",                       CardOrdering.EDHREC),
    ("id:g",                                          CardOrdering.EDHREC),
    ("t:creature",                                    CardOrdering.EDHREC),
    ("cmc>3",                                         CardOrdering.CMC),
    ("cmc>6",                                         CardOrdering.CMC),
    ("format:legacy",                                 CardOrdering.EDHREC),
    ("(t:bird color:blue) or (t:beast color:green)",  CardOrdering.EDHREC),
    ("(name:forest) or (name:mountain)",              CardOrdering.EDHREC),
    ("power+toughness>8",                               CardOrdering.EDHREC),
    ("power>4",                                         CardOrdering.EDHREC),
]
UNIQUES = [UniqueOn.CARD, UniqueOn.PRINTING, UniqueOn.ARTWORK]

ENGINE_WARMUP = 20
ENGINE_WINDOW = 5.0  # seconds
SQL_RUNS      = 15
SQL_DISCARD   = 3

print("Connecting to DB and loading engine store…", flush=True)
api = APIResource(last_import_time=multiprocessing.Value("d", time.time(), lock=True))
api._import_recent = lambda: True
api._setup_complete = lambda: True
api._reload_engine()
print(f"Engine loaded: {api._engine.size():,} cards\n", flush=True)


def bench_engine(q_str, unique, orderby):
    q = parse_scryfall_query(q_str)
    kw = dict(unique=str(unique), prefer=str(PreferOrder.DEFAULT),
              orderby=str(orderby), direction=str(SortDirection.ASC), limit=100)
    for _ in range(ENGINE_WARMUP):
        api._engine.query(filters=q, **kw)
    n, t0 = 0, time.monotonic()
    while time.monotonic() < t0 + ENGINE_WINDOW:
        api._engine.query(filters=q, **kw)
        n += 1
    return (time.monotonic() - t0) / n * 1_000  # ms


def bench_sql(q_str, unique, orderby):
    parsed = parse_scryfall_query(q_str)
    wc, base_params = generate_sql_query(parsed)
    qe = parsed.to_human_explanation()
    kw = dict(where_clause=wc, query_explanation=qe, query=q_str, unique=unique,
              prefer=PreferOrder.DEFAULT, orderby=orderby,
              direction=SortDirection.ASC, limit=100)
    times = []
    for _ in range(SQL_RUNS):
        api._query_cache.clear()
        t0 = time.monotonic()
        api._search_sql(params=dict(base_params), timer=Timer(), **kw)
        times.append((time.monotonic() - t0) * 1_000)
    return sum(times[SQL_DISCARD:]) / len(times[SQL_DISCARD:])


def gmean(values):
    return math.exp(sum(math.log(v) for v in values) / len(values))


hdr = f"{'query':<28} {'unique':<10} {'engine ms':>10} {'sql ms':>9}  winner"
print(hdr)
print("-" * len(hdr))

prev = ""
all_eng, all_sql = [], []
for q_str, orderby in QUERIES:
    for unique in UNIQUES:
        eng = bench_engine(q_str, unique, orderby)
        sql = bench_sql(q_str, unique, orderby)
        all_eng.append(eng)
        all_sql.append(sql)
        winner = f"engine {sql/eng:.0f}x" if eng < sql else f"sql    {eng/sql:.0f}x"
        if q_str != prev and prev:
            print()
        print(f"{q_str:<28} {str(unique):<10} {eng:>10.2f} {sql:>9.1f}  {winner}")
        prev = q_str

ge, gs = gmean(all_eng), gmean(all_sql)
winner = f"engine {gs/ge:.1f}x" if ge < gs else f"sql    {ge/gs:.1f}x"
print(f"\n{'geometric mean':<22} {'':10} {ge:>10.2f} {gs:>9.1f}  {winner}")

print(f"\nEngine: {ENGINE_WARMUP} warmup + {ENGINE_WINDOW:.0f}s timed window")
print(f"SQL:    {SQL_RUNS} runs, first {SQL_DISCARD} discarded, _query_cache cleared each call")
PYEOF
