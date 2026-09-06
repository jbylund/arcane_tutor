"""End-to-end HTTP/WSGI latency A/B for a build, through the REAL Falcon app + middleware stack.

Companion to `bench_query_latency_ab.py`, which calls `engine.query()` directly and skips Falcon,
JSON encoding, compression, and `CachingMiddleware` entirely. This one goes through the real WSGI
app -- middleware order exactly as `ApiWorker.get_api` wires it, real gzip/br/zstd compression
(`--accept-encoding`), and (with `--enable-cache`) the real `CachingMiddleware` backed by
`shared_cache.SharedCache` -- to answer "what does a client actually wait for," not just "how fast
is the engine call underneath it."

No live Postgres: `AppContext.setup_complete` is monkeypatched to `True` (the same pattern
`api/tests/conftest.py`'s `stub_api_resource_fixture` uses for tests that don't touch the DB --
`/search` never reads `reader_pool` once past that check). Fake `PG*` env vars keep
`psycopg_pool.ConnectionPool`'s background connection attempts pointed at a dead port instead of
spinning up a real testcontainers Postgres for a pool this script never uses.

Cache semantics: with `--enable-cache`, warmups already populate the cache before the timed trials
run, so the timed trials measure CACHE-HIT latency (repeat request for the same rendered page), not
a fresh engine pass. That is deliberate -- it is what caching is for -- but it means a MISS-path-only
fix (e.g. the double hash on a cache write) fires once, during warmup, and is invisible in the
timed trials regardless of its real cost. Nanosecond-scale fixes are invisible at HTTP altitude
either way (a WSGI round trip costs high-microseconds to low-milliseconds); this harness answers
"did anything regress, and is the cumulative effect of the larger fixes visible at realistic
scale," not "can I re-detect a kernel-benchmark delta."

Same paired discipline as `bench_query_latency_ab.py`: measure one build to a file with `--out`,
then `--compare` two files written with the SAME `--seed`/`--mode`/`--enable-cache` so the query
streams and cache regime line up. That script's interleaving warning applies here too -- alternate
which build you run rather than measuring all of A then all of B -- but true interleaving needs two
live builds in one process, which two different compiled `card_engine`/`shared_cache` .so files
rule out; running short alternating invocations (A, B, A, B) and keeping only the queries common to
all runs (`--compare` already pairs on the query tuple) is the practical substitute.

**Import isolation, because this repo has bitten sessions before**: `maturin develop` rewrites the
SHARED venv's `card_engine.pth`, so a bare `import card_engine` after building a second worktree can
silently resolve to whichever build ran `make engine` most recently -- see
docs/issues (or ask about "shared checkout concurrency"). This script never relies on that: point
`--engine-dir` at a directory produced by `maturin build --release -o <dir>` (then unzipped) for
EACH build under test, and it prepends that exact directory to `sys.path` before importing anything
from it. The run's own output prints `card_engine.__file__` and `shared_cache.__file__` so a result
self-documents which .so files actually produced it.

    # once per build, once per cache setting -- run from that build's checked-out repo root:
    .venv/bin/python scripts/bench_http_latency_ab.py --engine-dir /tmp/wheel-main --sample 300 \\
        --out /tmp/main_nocache.jsonl
    .venv/bin/python scripts/bench_http_latency_ab.py --engine-dir /tmp/wheel-main --sample 300 \\
        --enable-cache --out /tmp/main_cache.jsonl
    # repeat with --engine-dir /tmp/wheel-pr for the PR build
    .venv/bin/python scripts/bench_http_latency_ab.py --compare /tmp/main_nocache.jsonl /tmp/pr_nocache.jsonl
    .venv/bin/python scripts/bench_http_latency_ab.py --compare /tmp/main_cache.jsonl /tmp/pr_cache.jsonl
"""

from __future__ import annotations

import argparse
import io
import json
import math
import multiprocessing
import os
import pathlib
import random
import statistics
import sys
import time
import urllib.parse

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Must happen before anything under `api.` is imported: Settings() and db_utils read these at
# import time, not per-call. A dead port keeps psycopg_pool's background connect attempts from
# spinning up a real testcontainers Postgres for a pool `/search` (with setup_complete stubbed)
# never actually uses.
os.environ.setdefault("PGHOST", "127.0.0.1")
os.environ.setdefault("PGPORT", "1")
os.environ.setdefault("PGDATABASE", "nonexistent")
os.environ.setdefault("PGUSER", "nobody")
os.environ.setdefault("PGPASSWORD", "nobody")

NUM_WARMUPS = 6
NUM_TRIALS = 30
LIMITS = (10, 100, 175)
OFFSETS = (0, 0, 0, 100)
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_CI = 0.95
NOISE_FLOOR_US = 1.0
# From api/middlewares/compression/compression_mod.py's own doc example of a real header shape.
DEFAULT_ACCEPT_ENCODING = "br;q=1.0, gzip;q=0.8, *;q=0.1"


def build_app(*, enable_cache: bool, shared_cache_path: pathlib.Path, engine: object) -> object:
    """Construct the real Falcon app: same middleware stack/order as `ApiWorker.get_api`, but with
    the engine injected (already loaded, no reload needed) and DB readiness stubbed out."""
    os.environ["SHARED_CACHE_PATH"] = str(shared_cache_path)
    from api.settings import settings

    settings.enable_cache = enable_cache

    import falcon
    import falcon.media
    import orjson

    from api.admin_resource import AdminContext
    from api.api_resource import APIResource
    from api.api_worker import json_error_serializer
    from api.app_context import AppContext
    from api.middlewares import (
        AdminAuthMiddleware,
        CachingMiddleware,
        CompressionMiddleware,
        CORSMiddleware,
        QueryLogMiddleware,
        SearchBudgetMiddleware,
        SecurityHeadersMiddleware,
        TimingMiddleware,
    )

    shared_cache_obj = None
    if enable_cache:
        from shared_cache import SharedCache

        shared_cache_obj = SharedCache(path=str(shared_cache_path), maxsize=10_000, n_pages=3)

    api = falcon.App(
        middleware=[
            TimingMiddleware(),
            AdminAuthMiddleware(),
            SearchBudgetMiddleware(),
            QueryLogMiddleware(),
            CachingMiddleware(cache=shared_cache_obj),
            CompressionMiddleware(),
            SecurityHeadersMiddleware(),
            CORSMiddleware(),
        ],
    )
    api.set_error_serializer(json_error_serializer)

    # last_import_time="now" keeps AdminResource.import_data() on its fast path in __init__ (no
    # network fetch). AdminContext()'s default schema_setup_event (MockEvent) starts UNSET, so
    # __init__'s unconditional self.admin.setup_schema() call would hit the real migration path
    # and block on a DB that doesn't exist here -- pre-set the event so it takes its fast path
    # (`if schema_setup_event.is_set(): return`) instead, same effect api/tests/conftest.py's
    # api_resource fixture gets by actually running migrations against a live container once.
    schema_setup_event = multiprocessing.Event()
    schema_setup_event.set()
    app_context = AppContext(last_import_time=multiprocessing.Value("d", time.time(), lock=True), engine=engine)
    app_context.setup_complete = lambda: True  # the other DB touch, on the /search path itself
    sink = APIResource(app_context=app_context, admin_context=AdminContext(schema_setup_event=schema_setup_event))
    api.add_sink(sink._handle, prefix="/")  # noqa: SLF001 - same call ApiWorker.get_api makes

    json_handler = falcon.media.JSONHandler(dumps=orjson.dumps, loads=orjson.loads)
    api.req_options.media_handlers.update({"application/json": json_handler})
    api.resp_options.media_handlers.update({"application/json": json_handler})
    return api


def wsgi_call(app: object, query_string: str, accept_encoding: str) -> tuple[str, bytes]:
    """One real WSGI request/response round trip through `app`. Returns (status, body)."""
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/search",
        "QUERY_STRING": query_string,
        "SERVER_NAME": "bench",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "bench",
        "HTTP_USER_AGENT": "sylvan-http-ab-bench/1.0",
        "HTTP_ACCEPT_ENCODING": accept_encoding,
        "CONTENT_LENGTH": "0",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    captured: dict[str, str] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: object = None) -> None:
        del headers, exc_info
        captured["status"] = status

    body = b"".join(app(environ, start_response))
    return captured["status"], body


def measure(
    app: object,
    sampler: object,
    rng: random.Random,
    sample: int,
    warmups: int,
    trials: int,
    accept_encoding: str,
    fields: str | None = None,
) -> list[dict]:
    """Min-of-trials wall time for a real HTTP round trip on each sampled query.

    `fields`, when given, is passed through verbatim as the `fields` query param (comma-separated
    field names) -- the default (None/omitted) resolves server-side to DEFAULT_FIELDS, which does
    NOT include `legalities`, so a run against the defaults never exercises that fix at all.
    """
    from client.query_sampler import ANY_SHAPE

    rows: list[dict] = []
    for _ in range(sample):
        limit, offset = rng.choice(LIMITS), rng.choice(OFFSETS)
        unique, orderby = sampler.unique(rng), sampler.orderby(rng)
        direction, prefer = rng.choice(("asc", "desc")), "default"
        q = sampler.query(rng, ANY_SHAPE)
        params = {
            "q": q,
            "unique": unique,
            "orderby": orderby,
            "direction": direction,
            "prefer": prefer,
            "limit": str(limit),
            "offset": str(offset),
        }
        if fields:
            params["fields"] = fields
        qs = urllib.parse.urlencode(params)
        try:
            for _ in range(warmups):
                status, _ = wsgi_call(app, qs, accept_encoding)
            if not status.startswith("200"):
                continue
            best = math.inf
            for _ in range(trials):
                t0 = time.perf_counter_ns()
                status, _ = wsgi_call(app, qs, accept_encoding)
                best = min(best, time.perf_counter_ns() - t0)
            if not status.startswith("200"):
                continue
        except Exception:  # noqa: BLE001, S112 - a rejected/erroring query is a skipped sample
            continue
        rows.append({"q": q, "unique": unique, "orderby": orderby, "direction": direction, "prefer": prefer, "limit": limit, "offset": offset, "fields": fields or "", "us": best / 1000.0})
    return rows


def paired_bootstrap(deltas: list[float]) -> tuple[float, float]:
    rng = random.Random(0)
    n = len(deltas)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_RESAMPLES))
    tail = (1.0 - BOOTSTRAP_CI) / 2.0
    return means[int(tail * BOOTSTRAP_RESAMPLES)], means[int((1.0 - tail) * BOOTSTRAP_RESAMPLES) - 1]


def compare(path_a: pathlib.Path, path_b: pathlib.Path) -> None:
    def rows(path: pathlib.Path) -> dict[tuple, float]:
        out = {}
        for line in path.open():
            r = json.loads(line)
            out[(r["q"], r["unique"], r["orderby"], r["direction"], r["prefer"], r["limit"], r["offset"], r.get("fields", ""))] = r["us"]
        return out

    a, b = rows(path_a), rows(path_b)
    shared = sorted(set(a) & set(b))
    if not shared:
        print("no queries in common -- both runs need the same --mode/--sample/--seed/--enable-cache")
        return
    deltas = [b[k] - a[k] for k in shared]
    lo, hi = paired_bootstrap(deltas)
    mean_a, mean_b = sum(a[k] for k in shared) / len(shared), sum(b[k] for k in shared) / len(shared)
    ratios = sorted(b[k] / a[k] for k in shared if a[k] > 0)
    worse = sum(1 for d in deltas if d > NOISE_FLOOR_US)
    better = sum(1 for d in deltas if d < -NOISE_FLOOR_US)

    print(f"\npaired over {len(shared):,} queries in common ({len(a):,} / {len(b):,} recorded)")
    print(f"  A mean latency  {mean_a:>9.1f} µs   median {statistics.median(a[k] for k in shared):>8.1f} µs   ({path_a.name})")
    print(f"  B mean latency  {mean_b:>9.1f} µs   median {statistics.median(b[k] for k in shared):>8.1f} µs   ({path_b.name})")
    print(f"  B - A           {mean_b - mean_a:>9.1f} µs   {BOOTSTRAP_CI:.0%} CI [{lo:+.1f}, {hi:+.1f}]")
    print(f"  per-query ratio B/A: median {statistics.median(ratios):.3f}   p10 {ratios[len(ratios) // 10]:.3f}   p90 {ratios[len(ratios) * 9 // 10]:.3f}")
    print(f"  slower on {worse:,}, faster on {better:,}, within ±{NOISE_FLOOR_US:g}µs on {len(shared) - worse - better:,}")
    verdict = "NO DETECTABLE DIFFERENCE (interval spans zero)" if lo <= 0.0 <= hi else ("B is SLOWER" if lo > 0 else "B is FASTER")
    print(f"  verdict: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine-dir", type=pathlib.Path, help="dir from `maturin build --release -o <dir>` + unzip, for import isolation")
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--warmups", type=int, default=NUM_WARMUPS)
    parser.add_argument("--trials", type=int, default=NUM_TRIALS)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--mode", default="realistic")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None, help="card_engine store path (NOT the http cache -- see --cache-path)")
    parser.add_argument("--cache-path", type=pathlib.Path, default=None, help="shared_cache mmap path; a fresh default per invocation, always deleted first for a cold start")
    parser.add_argument("--enable-cache", action="store_true")
    parser.add_argument("--accept-encoding", default=DEFAULT_ACCEPT_ENCODING)
    parser.add_argument("--fields", default=None, help="comma-separated fields param; default omits it (server resolves to the 9-field DEFAULT_FIELDS, which excludes legalities)")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--compare", nargs=2, type=pathlib.Path, metavar=("A.jsonl", "B.jsonl"))
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return

    if args.engine_dir:
        sys.path.insert(0, str(args.engine_dir.resolve()))
    sys.path.insert(0, str(REPO_ROOT))

    from scripts.costbench import load_engine

    shm_path = args.shm_path or args.corpus.with_suffix(".http_ab.store")
    cache_path = args.cache_path or pathlib.Path(f"/tmp/sylvan_http_ab_{'cache' if args.enable_cache else 'nocache'}.cache")  # noqa: S108
    cache_path.unlink(missing_ok=True)  # cold start every run -- no carryover between builds/arms

    engine = load_engine(args.corpus, shm_path)
    app = build_app(enable_cache=args.enable_cache, shared_cache_path=cache_path, engine=engine)

    import card_engine as _ce

    print(f"card_engine: {_ce.__file__}")
    if args.enable_cache:
        import shared_cache as _sc

        print(f"shared_cache: {_sc.__file__}")

    from client.query_sampler import QuerySampler

    sampler = QuerySampler(args.corpus, args.mode)
    rows = measure(app, sampler, random.Random(args.seed), args.sample, args.warmups, args.trials, args.accept_encoding, args.fields)
    print(f"measured {len(rows):,} queries, mode={args.mode}, enable_cache={args.enable_cache}")
    if args.out:
        with args.out.open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
