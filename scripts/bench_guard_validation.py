"""Old-vs-new cost-guard constants on real data and real (wild) queries.

Validation step for the guard calibration (bench_cost_guards.py): samples a
deduplicated, weight-proportional set of wild queries (the Common Crawl
harvest of real scryfall.com searches, benchmarks/wild-queries/), runs them
against the *real* corpus export, and compares the OLD constants (forced via
CARD_ENGINE_* env overrides) against the NEW baked-in defaults in interleaved
fresh subprocesses. Totals must be identical old-vs-new for every query — the guards
are pure speed dials.

Reports per-query median ratios, the geomean speedup, and the per-rep geomean
spread.

    .venv/bin/python scripts/bench_guard_validation.py run \
        --corpus <real corpus.jsonl> --reps 5
    .venv/bin/python scripts/bench_guard_validation.py analyze

`--query-source uniform` swaps the wild corpus for `QuerySampler("uniform")`-generated queries
(offset/limit drawn from a wide spread, biased toward `Mode::Card`). Every row in the wild corpus is
offset=0 -- real users almost never page deep -- so a feature gated on deep pages (the sigma decision
rule, docs/issues/local-engine-compose-perm-sigma-decision-rule.md) needs this to be exercised at all,
not just checked for safety on the common case:

    .venv/bin/python scripts/bench_guard_validation.py run --query-source uniform \
        --corpus <real corpus.jsonl> --env-old '{}' \
        --env-new '{"CARD_ENGINE_COMPOSE_SIGMA_ENABLED": "1", "CARD_ENGINE_COMPOSE_SIGMA_KNOB": "3.0"}'

Note `--env-old` defaults to `OLD_ENV` (the pre-calibration constants this tool was originally built
to validate) when omitted -- pass `--env-old '{}'` explicitly for a clean "today's shipped defaults"
baseline, or the old branch will also carry those unrelated overrides.

The crossover the sigma rule guards is rare even under `--query-source uniform`: only ~1% of sampled
queries land sparse+deep enough to divert. An unfiltered run's aggregate is ~99% queries the decision
never touches, which buries the real effect in noise from the untouched majority. Add `--divert-only`
to sample a larger candidate pool, probe each one under `--env-new`, and time only the ones that
actually took `PermThreePhase`:

    .venv/bin/python scripts/bench_guard_validation.py run --query-source uniform --divert-only \
        --corpus <real corpus.jsonl> --count 5000 --env-old '{}' \
        --env-new '{"CARD_ENGINE_COMPOSE_SIGMA_ENABLED": "1", "CARD_ENGINE_COMPOSE_SIGMA_KNOB": "3.0"}'
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import pathlib
import random
import re
import statistics
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import card_engine  # noqa: E402
from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import ENGINE_ORDERBYS  # noqa: E402

OUTDIR = REPO_ROOT / "benchmarks/cost-guards"

# The pre-calibration constants, forced via env for the "old" branch. The
# "new" branch runs with no overrides, i.e. the defaults baked into the build.
OLD_ENV = {
    "CARD_ENGINE_MAX_NARROW_FRACTION": "0.25",
    "CARD_ENGINE_NARROW_FLOOR": "1000",
    "CARD_ENGINE_AND_SKIP_THRESHOLD": "2048",
    "CARD_ENGINE_BITS_PROMOTE": "4096",
    "CARD_ENGINE_STREAM_MIN_MATCHES": "1024",
}

WARMUP = 3
MAX_ITERS = 400

# Wild params → engine params: unique names differ, and orders the engine has
# no sort column for fall back to edhrec (mirroring orderby_to_col).
_WILD_UNIQUE = {"card": "card", "prints": "printing", "art": "artwork"}
# Bare name lookups dominate the wild corpus by weight but are one engine code
# path; cap them so operator queries (many distinct paths) keep most slots.
_NAME_LOOKUP_FRACTION = 1 / 6
_OP_RE = re.compile(r"[a-z]+[:<>=]", re.IGNORECASE)


def sample_wild(rng: random.Random, wild_corpus: pathlib.Path, count: int) -> list[dict]:
    """Sample wild queries weight-proportionally without replacement (Efraimidis-Spirakis keys)."""
    ops: list[dict] = []
    names: list[dict] = []
    with wild_corpus.open() as fh:
        for line in fh:
            row = json.loads(line)
            (ops if _OP_RE.search(row["q"]) else names).append(row)
    n_names = int(count * _NAME_LOOKUP_FRACTION)
    picked: list[dict] = []
    for pool, k in ((ops, count - n_names), (names, n_names)):
        keyed = sorted(pool, key=lambda r: rng.random() ** (1 / r["weight"]), reverse=True)
        picked.extend(keyed[:k])
    return [
        {
            "query": r["q"],
            "unique": _WILD_UNIQUE[r["unique"]],
            "orderby": r["order"] if r["order"] in ENGINE_ORDERBYS else "edhrec",
        }
        for r in picked
    ]


# Permutation-backed orderbys only (`SortCol`'s six minus `usd`/`rarity`, which have no permutation)
# -- the sigma decision rule this validation exists for only ever fires on `Perm`, so a query set
# built from the other two never exercises it at all, regardless of offset/limit coverage.
_PERM_ORDERBYS = ("edhrec", "cubecobra", "cmc", "power", "toughness", "name")
# Mirrors OFFSET_SWEEP in bench_compose_card_visited_safety_bound.py / sigma_knob_sensitivity_sweep:
# real traffic is offset~0-heavy by design (confirmed by the wild corpus itself: every row is
# offset=0), which cannot exercise the tail the sigma decision rule is FOR. Drawing from this spread
# instead of a fixed 0 is the whole point of using the sampler here rather than the wild corpus.
_OFFSET_CHOICES = (0, 0, 0, 50, 200, 500, 1_000, 2_000, 4_000, 8_000, 15_000, 25_000)
# Weighted toward smaller pages (real UI page sizes cluster low) but with enough spread that a
# handful of large-limit queries exercise `k = offset + limit` reaching further into the permutation.
_LIMIT_CHOICES = (20, 20, 20, 50, 100)


def sample_uniform(rng: random.Random, corpus: pathlib.Path, count: int) -> list[dict]:
    """Sample `count` query specs from `QuerySampler("uniform")`.

    Biased toward the population the sigma decision rule actually decides for: `Mode::Card` (the only
    mode `walk_card_page_via_popcount_skip` supports) on a permutation-backed orderby, with offset AND
    limit drawn from a wide spread rather than pinned to the shallow-page default every real wild
    query uses.

    Query TEXT is synthetic (structured, not harvested), unlike `sample_wild` -- the trade this
    validation makes deliberately: real user query text almost never exercises deep pages at all, so
    a harness restricted to it can only validate SAFETY on the common case, never the population this
    feature exists for. `bench_guard_validation.py`'s own wild-corpus run covers that safety case
    already; this covers the coverage gap it structurally cannot.
    """
    from client.query_sampler import QuerySampler  # noqa: PLC0415 - only this path needs it

    sampler = QuerySampler(corpus, "uniform")
    specs: list[dict] = []
    seen: set[str] = set()
    while len(specs) < count:
        query = sampler.structured_query(rng)["query"]
        if query in seen:
            continue
        seen.add(query)
        specs.append(
            {
                "query": query,
                "unique": "card",
                "orderby": rng.choice(_PERM_ORDERBYS),
                "direction": rng.choice(("asc", "desc")),
                "offset": rng.choice(_OFFSET_CHOICES),
                "limit": rng.choice(_LIMIT_CHOICES),
            }
        )
    return specs


def bench_one(engine: card_engine.QueryEngine, spec: dict, window: float) -> tuple[int, int, float, float]:
    """Return (total, n, median_ms, min_ms) for one query spec over a timed window."""
    kw = {
        "filters": parse_scryfall_query(spec["query"]),
        "unique": spec["unique"],
        "prefer": spec.get("prefer", "default"),
        "orderby": spec["orderby"],
        "direction": spec.get("direction", "asc"),
        "limit": spec.get("limit", 100),
        "offset": spec.get("offset", 0),
    }
    total = engine.query(**kw)[0]
    for _ in range(WARMUP):
        engine.query(**kw)
    samples: list[float] = []
    deadline = time.monotonic() + window
    while not samples or (time.monotonic() < deadline and len(samples) < MAX_ITERS):
        t0 = time.perf_counter_ns()
        engine.query(**kw)
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    return total, len(samples), statistics.median(samples), min(samples)


def build_query_set(store: pathlib.Path, sample_fn, count: int, seed: int, path: pathlib.Path) -> None:  # noqa: ANN001 - sample_fn is sample_wild or sample_uniform, both (rng, count) -> list[dict]
    """Sample, dedupe, and pre-filter query specs from `sample_fn`; write the frozen set to JSON."""
    rng = random.Random(seed)
    specs, seen = [], set()
    for spec in sample_fn(rng, count * 2):  # oversample: dedupe + parse failures shrink the pool
        if spec["query"] in seen:
            continue
        seen.add(spec["query"])
        specs.append(spec)
    engine = card_engine.QueryEngine(str(store))
    kept = []
    for spec in specs:
        try:
            bench_one(engine, spec, 0.0)
        except Exception as oops:  # noqa: BLE001 — wild strings include unsupported syntax
            print(f"SKIP {spec['query']!r}: {oops}")
            continue
        kept.append(spec)
        if len(kept) == count:
            break
    path.write_text(json.dumps(kept, indent=1) + "\n")
    print(f"query set: {len(kept)} specs -> {path}")


_PROBE_SCRIPT = """
import json, sys
sys.path.insert(0, {repo_root!r})
import card_engine
from api.parsing import parse_scryfall_query
specs = json.loads(open({queries!r}).read())
engine = card_engine.QueryEngine({store!r})
diverted = []
for qid, spec in enumerate(specs):
    kw = dict(
        filters=parse_scryfall_query(spec["query"]), unique=spec["unique"], prefer=spec.get("prefer", "default"),
        orderby=spec["orderby"], direction=spec.get("direction", "asc"), limit=spec.get("limit", 100),
        offset=spec.get("offset", 0),
    )
    res = engine.explain_analyze(num_warmups=0, num_trials=1, **kw)
    for plan in res["plans"]:
        if plan["plan"] == "PrintingCompose" and plan["paging_taken"] == "PermThreePhase":
            diverted.append(qid)
            break
print(json.dumps(diverted))
"""


def probe_diverted(store: pathlib.Path, queries: pathlib.Path, env: dict) -> set[int]:
    """Return the qids (indices into `queries`) whose `PrintingCompose` plan took `PermThreePhase` under `env`.

    Runs in a fresh subprocess with `env` applied, since `CARD_ENGINE_COMPOSE_SIGMA_ENABLED`'s
    `LazyLock` caches on first read -- this process's own import of `card_engine` may already have
    read a different value.
    """
    script = _PROBE_SCRIPT.format(repo_root=str(REPO_ROOT), queries=str(queries), store=str(store))
    out = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True, env={**os.environ, **env}, cwd=REPO_ROOT
    )
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def cmd_worker(args: argparse.Namespace) -> None:
    """Time every query in the frozen set in this process; append rows to the CSV."""
    engine = card_engine.QueryEngine(str(args.store))
    specs = json.loads(args.queries.read_text())
    with args.out.open("a", newline="") as fh:
        writer = csv.writer(fh)
        for qid, spec in enumerate(specs):
            total, n, med_ms, min_ms = bench_one(engine, spec, args.window)
            writer.writerow(
                [
                    args.branch,
                    args.rep,
                    qid,
                    spec["query"],
                    spec["unique"],
                    spec["orderby"],
                    total,
                    n,
                    f"{med_ms:.5f}",
                    f"{min_ms:.5f}",
                ]
            )
    print(f"  {args.branch:<4} rep{args.rep}: {len(specs)} queries", flush=True)


def cmd_run(args: argparse.Namespace) -> None:
    """Build the store + frozen query set, then run interleaved old/new reps."""
    from scripts.costbench import load_engine  # noqa: PLC0415 — heavy loader, workers don't need it

    store = OUTDIR / "real.store"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    if not store.exists():
        load_engine(args.corpus, store)
    env_old = json.loads(args.env_old) if args.env_old else OLD_ENV
    env_new = json.loads(args.env_new) if args.env_new else {}
    if args.query_source == "wild":
        sample_fn = lambda rng, count: sample_wild(rng, args.wild_corpus, count)  # noqa: E731
    else:
        sample_fn = lambda rng, count: sample_uniform(rng, args.corpus, count)  # noqa: E731
    # Separate frozen sets per source -- a stale wild-sourced file must never silently serve a
    # uniform-sourced run or vice versa; the two populations are not comparable to each other.
    queries = OUTDIR / f"validation-queries-{args.query_source}{'-diverted' if args.divert_only else ''}.json"
    if not queries.exists():
        if args.divert_only:
            # `--count` candidates almost all take bare `Perm` (the crossover is rare by design --
            # it only fires on a sparse, deep page); filter down to the ones that actually diverted
            # to `PermThreePhase` under env-new, so the timed A/B measures the population the sigma
            # decision rule exists for, not a benchmark diluted 99% by queries it left untouched.
            candidates = OUTDIR / f"validation-queries-{args.query_source}-candidates.json"
            if not candidates.exists():
                build_query_set(store, sample_fn, args.count, args.seed, candidates)
            diverted = probe_diverted(store, candidates, env_new)
            if not diverted:
                msg = f"no candidate queries took PermThreePhase under env-new ({len(json.loads(candidates.read_text()))} candidates) -- raise --count"
                raise SystemExit(msg)
            specs = json.loads(candidates.read_text())
            subset = [specs[i] for i in sorted(diverted)]
            queries.write_text(json.dumps(subset, indent=1) + "\n")
            print(f"divert-only: {len(subset)}/{len(specs)} candidates took PermThreePhase -> {queries}")
        else:
            build_query_set(store, sample_fn, args.count, args.seed, queries)
    if not args.out.exists():
        args.out.write_text("branch,rep,qid,query,unique,orderby,total,n,med_ms,min_ms\n")
    for rep in range(1, args.reps + 1):
        branches = [("old", env_old), ("new", env_new)]
        if rep % 2 == 0:
            branches.reverse()
        for branch, env in branches:
            cmd = [
                sys.executable,
                str(pathlib.Path(__file__)),
                "worker",
                "--branch",
                branch,
                "--rep",
                str(rep),
                "--store",
                str(store),
                "--queries",
                str(queries),
                "--out",
                str(args.out),
                "--window",
                str(args.window),
            ]
            subprocess.run(cmd, check=True, env={**os.environ, **env}, cwd=REPO_ROOT)
    cmd_analyze(args)


def cmd_analyze(args: argparse.Namespace) -> None:
    """Parity-check totals, then print per-query ratios, geomean, and per-rep spread."""
    rows: list[dict] = []
    with args.out.open() as fh:
        rows = list(csv.DictReader(fh))
    totals: dict[str, set] = {}
    for r in rows:
        totals.setdefault(r["qid"], set()).add(r["total"])
    bad = {k: v for k, v in totals.items() if len(v) > 1}
    if bad:
        for k, v in sorted(bad.items()):
            print(f"PARITY FAILURE qid={k}: totals {sorted(v)}", file=sys.stderr)
        sys.exit(1)
    print(f"parity OK: {len(totals)} queries, totals identical old vs new")

    med: dict[tuple[str, str], list[float]] = {}  # (qid, branch) -> per-rep medians
    meta: dict[str, dict] = {}
    for r in rows:
        med.setdefault((r["qid"], r["branch"]), []).append(float(r["med_ms"]))
        meta[r["qid"]] = r
    qids = sorted({q for q, _ in med}, key=int)
    ratios: dict[str, float] = {}
    for q in qids:
        old, new = statistics.median(med[(q, "old")]), statistics.median(med[(q, "new")])
        ratios[q] = old / new
    geomean = math.exp(statistics.fmean(math.log(r) for r in ratios.values()))
    reps = sorted({int(r["rep"]) for r in rows})
    per_rep = []
    for rep in reps:
        logs = []
        for q in qids:
            o = [float(r["med_ms"]) for r in rows if r["qid"] == q and r["branch"] == "old" and int(r["rep"]) == rep]
            n = [float(r["med_ms"]) for r in rows if r["qid"] == q and r["branch"] == "new" and int(r["rep"]) == rep]
            if o and n:
                logs.append(math.log(o[0] / n[0]))
        per_rep.append(math.exp(statistics.fmean(logs)))
    print(f"geomean speedup (old/new): {geomean:.4f}x  per-rep: {', '.join(f'{g:.4f}' for g in per_rep)}")
    print("\nbiggest wins (old/new ratio):")
    for q in sorted(qids, key=lambda q: -ratios[q])[:12]:
        r = meta[q]
        print(
            f"  {ratios[q]:6.2f}x  {statistics.median(med[(q, 'old')]):8.3f} -> {statistics.median(med[(q, 'new')]):8.3f} ms  total={r['total']:>6}  {r['query']!r}"
        )
    print("\nbiggest regressions:")
    for q in sorted(qids, key=lambda q: ratios[q])[:12]:
        r = meta[q]
        print(
            f"  {ratios[q]:6.2f}x  {statistics.median(med[(q, 'old')]):8.3f} -> {statistics.median(med[(q, 'new')]):8.3f} ms  total={r['total']:>6}  {r['query']!r}"
        )


def main() -> None:
    """Dispatch the run / worker / analyze subcommands."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "worker", "analyze"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=pathlib.Path, default=OUTDIR / "validation.csv")
        p.add_argument("--window", type=float, default=0.12)
        if name == "run":
            p.add_argument("--corpus", type=pathlib.Path, required=True)
            p.add_argument("--wild-corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/wild-queries/wild-corpus.jsonl")
            p.add_argument(
                "--query-source",
                choices=("wild", "uniform"),
                default="wild",
                help="'wild': real harvested query text, always offset=0 (safety on the common case). "
                "'uniform': QuerySampler-generated Mode::Card queries with offset/limit drawn from a wide "
                "spread (coverage of the population a feature gated on deep pages actually needs to exercise)",
            )
            p.add_argument("--reps", type=int, default=5)
            p.add_argument("--count", type=int, default=180)
            p.add_argument("--seed", type=int, default=20260708)
            p.add_argument("--env-old", default="", help="JSON env dict for the 'old' branch (default: pre-calibration constants)")
            p.add_argument("--env-new", default="", help="JSON env dict for the 'new' branch (default: baked-in defaults)")
            p.add_argument(
                "--divert-only",
                action="store_true",
                help="Sample --count candidates, probe each under --env-new, and time only the ones whose "
                "PrintingCompose plan actually took PermThreePhase. The crossover is rare (~1% of uniform "
                "queries), so an unfiltered run is ~99% queries the decision left untouched -- this isolates "
                "the population the sigma rule exists for.",
            )
        if name == "worker":
            p.add_argument("--branch", required=True)
            p.add_argument("--rep", type=int, required=True)
            p.add_argument("--store", type=pathlib.Path, required=True)
            p.add_argument("--queries", type=pathlib.Path, required=True)
    args = parser.parse_args()
    {"run": cmd_run, "worker": cmd_worker, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    main()
