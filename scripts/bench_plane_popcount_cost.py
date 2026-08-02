"""Why is `PlanePopcountOrder` under-costed? Residual against every term the model could be missing.

Its cost arm is `matches·0.65 + (n_cards/64)·1.0 + limit·2.0 + 200` ns — no term for evaluating the
plane itself, and no dependence on how many plane leaves the expression has. A corpus sweep put it
at a median 1.54-1.61x under-costed with a p90 of 6.16, so something real looked unpriced.

That turned out to be the harness, not the engine: `run_query_with_plan` re-evaluates the plane,
which the routed path does once in acquire and reuses. Netting acquire for every plan clears it
(2.10 raw -> 0.69 net). Kept because the leaf-count breakdown below is what diagnosed it.

Sampling is time-budgeted rather than count-budgeted (`--seconds`), because the plan only fires on
plane-acquired queries — roughly one generated query in seven. Cheap `explain` calls filter for
those first; only hits pay for `explain_analyze`.

    .venv/bin/python scripts/bench_plane_popcount_cost.py --seconds 120
"""

from __future__ import annotations

import argparse
import pathlib
import random
import re
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import QuerySampler  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

TARGET_PLAN = "PlanePopcountOrder"
NUM_WARMUPS = 3
NUM_TRIALS = 11
# Leaf-ish tokens in the query text, as a cheap proxy for how many planes get ANDed/ORed together —
# the model has no term for this, so it is the first candidate explanation for the residual.
LEAF_RE = re.compile(r"[a-z]+[:<>=]", re.I)
# Model constants mirrored from cost.rs, so the residual can be attributed to a term rather than
# just observed. Any drift here shows up as a constant offset in the residual column.
SCATTER_PER_MATCH_NS = 0.65
PER_WORD_NS = 1.0
EMIT_PER_CARD_NS = 2.0
FIXED_NS = 200.0
LIMIT = 100


def predicted_ns(matches: int, n_cards: int) -> float:
    """The model's own arithmetic, re-derived so its terms can be inspected separately."""
    return matches * SCATTER_PER_MATCH_NS + (n_cards / 64.0) * PER_WORD_NS + LIMIT * EMIT_PER_CARD_NS + FIXED_NS


def main() -> None:
    """Sample plane-acquired queries until the time budget is spent, then attribute the residual."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=120.0, help="wall-clock budget for sampling")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    parser.add_argument("--seed", type=int, default=42, help="seed the sampled query stream")
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".misselect.store"))
    sampler = QuerySampler(args.corpus, "realistic")
    rng = random.Random(args.seed)

    rows: list[tuple[float, int, int, float, float, str]] = []
    seen: set[str] = set()
    generated = filtered = 0
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        q = sampler.query(rng)
        generated += 1
        if q in seen:
            continue
        seen.add(q)
        try:
            filters = parse_scryfall_query(q)
            # Cheap filter: one acquire, no plan executions.
            quick = engine.explain(filters=filters, unique="card", orderby="edhrec", direction="asc", limit=LIMIT, offset=0)
        except Exception:  # noqa: BLE001, S112 - a query bind rejects is a sample skip
            continue
        if not any(p["plan"] == TARGET_PLAN for p in quick["plans"]):
            continue
        filtered += 1
        try:
            res = engine.explain_analyze(
                filters=filters,
                unique="card",
                orderby="edhrec",
                direction="asc",
                limit=LIMIT,
                offset=0,
                num_warmups=NUM_WARMUPS,
                num_trials=NUM_TRIALS,
            )
        except Exception:  # noqa: BLE001, S112
            continue
        plan = next((p for p in res["plans"] if p["plan"] == TARGET_PLAN and p["trials_ns"]), None)
        if plan is None:
            continue
        a = res["acquire"]
        meas = min(plan["trials_ns"])
        rows.append((meas / plan["predicted_ns"], a["matches"], len(LEAF_RE.findall(q)), meas, plan["predicted_ns"], q))

    if not rows:
        print("no plane-acquired samples in the budget")
        return

    report(rows, generated, filtered, args.seconds)


def report(rows: list[tuple[float, int, int, float, float, str]], generated: int, filtered: int, seconds: float) -> None:
    """Ratio summary, then the residual broken down by plane-leaf count and by match count."""
    ratios = [r[0] for r in rows]
    print(f"\n{len(rows):,} {TARGET_PLAN} samples ({generated:,} generated, {filtered:,} plane-acquired) in {seconds:.0f}s")
    qs = statistics.quantiles(ratios, n=10)
    print(
        f"ratio measured/predicted: median {statistics.median(ratios):.2f}  p10 {qs[0]:.2f}  p90 {qs[8]:.2f}  max {max(ratios):.2f}"
    )

    # Is the residual a fixed underestimate, or does it scale with match count or leaf count? A
    # constant residual means the fixed term is too low; one that scales means a missing rate.
    print(f"\n{'leaves':>7}{'n':>6}{'median ratio':>14}{'median resid ns':>17}{'resid/match ns':>16}")
    for leaves in sorted({r[2] for r in rows}):
        grp = [r for r in rows if r[2] == leaves]
        resid = [r[3] - r[4] for r in grp]
        per_match = [(r[3] - r[4]) / max(r[1], 1) for r in grp]
        print(
            f"{leaves:>7}{len(grp):>6}{statistics.median(r[0] for r in grp):>14.2f}"
            f"{statistics.median(resid):>17.0f}{statistics.median(per_match):>16.2f}"
        )

    print(f"\n{'matches':>9}{'n':>6}{'median ratio':>14}{'median resid ns':>17}")
    buckets = ((0, 1, "0"), (1, 100, "1-99"), (100, 1_000, "100-999"), (1_000, 10_000, "1k-10k"), (10_000, 1 << 30, "10k+"))
    for lo, hi, label in buckets:
        grp = [r for r in rows if lo <= r[1] < hi]
        if not grp:
            continue
        resid = [r[3] - r[4] for r in grp]
        print(f"{label:>9}{len(grp):>6}{statistics.median(r[0] for r in grp):>14.2f}{statistics.median(resid):>17.0f}")

    rows.sort(reverse=True)
    print(f"\n{'ratio':>7}{'matches':>9}{'leaves':>7}{'meas ns':>10}{'pred ns':>10}  query")
    for ratio, matches, leaves, meas, pred, q in rows[:12]:
        print(f"{ratio:>7.2f}{matches:>9}{leaves:>7}{meas:>10.0f}{pred:>10.0f}  {q[:42]}")


if __name__ == "__main__":
    main()
