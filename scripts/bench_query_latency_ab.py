"""Paired end-to-end query latency between two engine builds. The A/B that works against ANY build.

`bench_plan_misselection.py` measures routing regret, but it reads `explain_analyze` fields and a
response shape that this branch introduced — main returns a bare list where the branch returns
`{"acquire": ..., "plans": [...]}`, and the per-plan `picked` / `ns_prepare` / `ns_round_total`
fields do not exist there at all. So it cannot be pointed at main.

`query()` is present and identically shaped in both, and it measures the thing users actually wait
for. Same discipline as the regret A/B: write per-query rows with `--out`, then `--compare` two files
PAIRED over the same query list, with a bootstrap CI on the difference. Latency is heavy-tailed
(broad scans cost thousands of times a name lookup), so comparing two headline means is hopeless —
pairing removes query-sampling variance and the interval says whether what remains is real.

For the executor-level question underneath this one — did a specific PLAN get faster, whether or not
the router picks it — see `bench_plan_execution_ab.py`.

**Pairing does not remove the other variance, and breadth does not substitute for depth.** `min` over
the trials is a floor estimator, and how far above the floor it lands depends on the interference
that run saw. That error is common-mode across every query in a run, so averaging it over more
queries does NOT cancel it. Measured on a same-build, same-seed pair at the old (2, 7) defaults, 388
queries paired: `B - A = -1.0 µs, 95% CI [-1.6, -0.5]`, verdict "B is FASTER", faster on 110 and
slower on 35 — with nothing changed. The same pair at (6, 30): `+0.5 µs, CI [-1.0, +2.9]`, no
detectable difference. Hence the defaults below; lower them only if you can show the canary is clean.

    # once per build:
    .venv/bin/python scripts/bench_query_latency_ab.py --sample 2000 --out A.jsonl
    .venv/bin/python scripts/bench_query_latency_ab.py --compare A.jsonl B.jsonl

INTERLEAVE THE BUILDS. Run A, then B, then A, then B, and compare like against like. Measuring all
of A and then all of B maps any drift in machine state -- thermal, background load, page cache --
straight onto the comparison. Measured: a sequential main-then-branch run showed a ~3% median
slowdown spread EVENLY across acquire branches the change never touched, while the branch it did
touch was absolutely faster. A uniform effect on untouched code paths is drift, not signal, and the
mean's confidence interval spanned zero throughout.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import pathlib
import random
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import ANY_SHAPE, MODES, QuerySampler, Shape  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

# Well above the shared costbench (2, 7), which is calibrated for comparisons INSIDE one
# `explain_analyze` call where every participant shares the same conditions. This is a
# cross-process comparison, where each run's floor estimate carries its own error -- see the module
# docstring for the same-build canary that fixes these numbers.
NUM_WARMUPS = 6
NUM_TRIALS = 30
LIMITS = (10, 100, 175)
OFFSETS = (0, 0, 0, 100)
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_CI = 0.95
# Below this, per-query differences are timer noise rather than a real change.
NOISE_FLOOR_US = 1.0


@dataclasses.dataclass(frozen=True)
class Budget:
    """How many queries to measure, and how hard.

    Breadth and depth defend against DIFFERENT variance, and only one of them is optional. Pairing
    plus a wide sample removes query-to-query variance. Neither touches the floor-estimation error,
    which is common-mode within a run and therefore survives any amount of averaging over queries.
    Buy depth first, then spend what is left on breadth.
    """

    sample: int
    warmups: int = NUM_WARMUPS
    trials: int = NUM_TRIALS


def measure(  # noqa: PLR0913 - a measurement function's arguments ARE the conditions it measures under
    engine: object,
    sampler: QuerySampler,
    rng: random.Random,
    budget: Budget,
    shape: Shape = ANY_SHAPE,
    *,
    vary_prefer: bool = False,
) -> list[dict]:
    """Min-of-trials wall time for `query()` on each sampled query.

    `vary_prefer` draws the printing preference instead of pinning it to `default`. Off by default
    because drawing it consumes the rng and shifts the whole query stream, which would orphan every
    baseline on disk -- but a run pinned to `default` cannot see a change to the custom-prefer path at
    all. `Prefer::Default` is the only value that lets the card- and artwork-mode match kernels stop
    at the first qualifying printing; the other four must score every printing of the card. `query()`
    has taken `prefer` on every build this harness can target, so varying it stays A/B-safe.
    """
    rows: list[dict] = []
    for _ in range(budget.sample):
        limit, offset = rng.choice(LIMITS), rng.choice(OFFSETS)
        kw = {
            "unique": sampler.unique(rng),
            "orderby": sampler.orderby(rng),
            "direction": rng.choice(("asc", "desc")),
            "limit": limit,
            "offset": offset,
        }
        q = sampler.query(rng, shape)
        prefer = sampler.prefer(rng) if vary_prefer else "default"
        try:
            filters = parse_scryfall_query(q)
            for _ in range(budget.warmups):
                engine.query(filters=filters, prefer=prefer, **kw)
            best = math.inf
            for _ in range(budget.trials):
                t0 = time.perf_counter_ns()
                engine.query(filters=filters, prefer=prefer, **kw)
                best = min(best, time.perf_counter_ns() - t0)
        except Exception:  # noqa: BLE001, S112 - a rejected query is a skipped sample
            continue
        rows.append(
            {
                "q": q,
                **{k: kw[k] for k in ("unique", "orderby", "direction")},
                "prefer": prefer,
                "limit": limit,
                "offset": offset,
                "us": best / 1000.0,
            }
        )
    return rows


def paired_bootstrap(deltas: list[float]) -> tuple[float, float]:
    """Central `BOOTSTRAP_CI` interval for the mean of `deltas`, by resampling with replacement."""
    rng = random.Random(0)  # fixed: the interval should not wobble between reads of the same data
    n = len(deltas)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_RESAMPLES))
    tail = (1.0 - BOOTSTRAP_CI) / 2.0
    return means[int(tail * BOOTSTRAP_RESAMPLES)], means[int((1.0 - tail) * BOOTSTRAP_RESAMPLES) - 1]


def compare(path_a: pathlib.Path, path_b: pathlib.Path) -> None:
    """Paired latency comparison over the queries both runs recorded."""

    def rows(path: pathlib.Path) -> dict[tuple, float]:
        out = {}
        for line in path.open():
            r = json.loads(line)
            # `.get` for `prefer`: files written before it was recorded pin it to default anyway, so
            # they still pair with each other rather than failing to key.
            out[(r["q"], r["unique"], r["orderby"], r["direction"], r.get("prefer", "default"), r["limit"], r["offset"])] = r["us"]
        return out

    a, b = rows(path_a), rows(path_b)
    shared = sorted(set(a) & set(b))
    if not shared:
        print("no queries in common -- both runs need the same --mode/--sample/--seed")
        return
    deltas = [b[k] - a[k] for k in shared]
    lo, hi = paired_bootstrap(deltas)
    mean_a = sum(a[k] for k in shared) / len(shared)
    mean_b = sum(b[k] for k in shared) / len(shared)
    # The mean is dominated by the slowest queries; the median ratio says what a typical query sees.
    ratios = sorted(b[k] / a[k] for k in shared if a[k] > 0)
    worse = sum(1 for d in deltas if d > NOISE_FLOOR_US)
    better = sum(1 for d in deltas if d < -NOISE_FLOOR_US)

    print(f"\npaired over {len(shared):,} queries in common ({len(a):,} / {len(b):,} recorded)")
    print(f"  A mean latency  {mean_a:>9.1f} µs   median {statistics.median(a[k] for k in shared):>8.1f} µs   ({path_a.name})")
    print(f"  B mean latency  {mean_b:>9.1f} µs   median {statistics.median(b[k] for k in shared):>8.1f} µs   ({path_b.name})")
    print(f"  B - A           {mean_b - mean_a:>9.1f} µs   {BOOTSTRAP_CI:.0%} CI [{lo:+.1f}, {hi:+.1f}]")
    print(
        f"  per-query ratio B/A: median {statistics.median(ratios):.3f}   p10 {ratios[len(ratios) // 10]:.3f}   p90 {ratios[len(ratios) * 9 // 10]:.3f}"
    )
    print(f"  slower on {worse:,}, faster on {better:,}, within ±{NOISE_FLOOR_US:g}µs on {len(shared) - worse - better:,}")
    verdict = "NO DETECTABLE DIFFERENCE (interval spans zero)" if lo <= 0.0 <= hi else ("B is SLOWER" if lo > 0 else "B is FASTER")
    print(f"  verdict: {verdict}")


def main() -> None:
    """Either measure one build to a file, or compare two such files."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Was 2000 when a run was 9 rounds deep; at 36 that would be 4x the wall time. Depth comes first
    # (breadth cannot cancel a common-mode error), and the same-build canary was already clean at 400.
    parser.add_argument("--sample", type=int, default=800)
    # Lowering these to buy more distinct queries is the trade this harness used to make, and it is
    # the wrong one: breadth cannot cancel an error that is common-mode within a run.
    parser.add_argument("--warmups", type=int, default=NUM_WARMUPS)
    parser.add_argument(
        "--trials", type=int, default=NUM_TRIALS, help="a cross-process A/B needs a converged floor; see the module docstring"
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--vary-prefer",
        action="store_true",
        help="sample the printing preference instead of pinning it to default; the custom-prefer path is the only one where the match kernels cannot early-break",
    )
    parser.add_argument("--mode", choices=MODES, default="realistic", help="latency asks what users wait for, so traffic-weighted")
    parser.add_argument(
        "--predicates",
        type=int,
        default=None,
        help=(
            "pin the conjunction width instead of drawing it from PREDICATE_COUNT_WEIGHTS (1/2/3 at "
            "45/40/15). 4+ is reachable only this way, and on purpose: the sampler caps the natural "
            "draw at 3 because deeper conjunctions narrow to nothing and stop exercising plan choice, "
            "which is the thing being measured. Pin it to compare like against like at one width."
        ),
    )
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    parser.add_argument("--out", type=pathlib.Path, help="write per-query latencies as JSONL")
    parser.add_argument("--compare", nargs=2, type=pathlib.Path, metavar=("A.jsonl", "B.jsonl"))
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".latency.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    shape = Shape(predicates=args.predicates) if args.predicates else ANY_SHAPE
    rows = measure(
        engine,
        sampler,
        random.Random(args.seed),
        Budget(args.sample, args.warmups, args.trials),
        shape,
        vary_prefer=args.vary_prefer,
    )
    width = f", predicates={args.predicates}" if args.predicates else ""
    print(f"measured {len(rows):,} queries, mode={args.mode}{width}")
    if args.out:
        with args.out.open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
