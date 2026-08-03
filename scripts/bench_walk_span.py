"""A/B the streamed walk's sort-column bound (`CARD_ENGINE_WALK_SORT_BOUND`).

`StreamedSelect`'s emission walk steps the sort permutation until the page fills. When the filter bounds
the SORT COLUMN -- `cmc>=6` under `order=cmc`, the correlation that puts every match at one end of the
walked order -- two binary searches turn that into a walk over only the segment the bound admits. This
prices that, per cell, against the same binary walking everything.

Cells come in two groups. CLUSTERED pairs a predicate with an orderby it correlates with, which is where
a walk from index 0 grinds through a long non-matching prefix. BROAD is the control: matches spread
through the permutation, so the walk already fills its page in ~`limit` steps and the bound has nothing
to skip. Both matter -- the change is only worth taking if the first group's win is real and the second
group is untouched.

**Why a toggle rather than two builds.** Cross-build comparison cannot resolve this. Measured on these
cells, `ns_loop` wandered 43.00 / 45.38 / 47.17 us across three runs of two builds -- +-9%, in both
directions, on a phase neither build changed -- and a cross-build `bench_plan_execution_ab.py` run
reported the wrong SIGN for this change while its own acquire control moved 1.9% the same way. That is
the common-mode floor error the toolkit documents, plus whatever the linker did to code layout. One
binary with a runtime toggle removes both: identical code, identical layout.

Both arms set the env var, to EQUAL-LENGTH values, because an env var present in one arm only shifts
process memory layout enough to move sub-100us queries a consistent ~15% either way -- the same bias
`bench_verify_order.py` documents and equalizes.

    .venv/bin/python scripts/bench_walk_span.py --reps 5
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CORPUS = REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl"
TOGGLE = "CARD_ENGINE_WALK_SORT_BOUND"
# Two arms, equal-length values so neither carries a differently-sized environment: `0` walks the whole
# permutation as the executor did before the bound existed, `1` narrows it to what the filter's interval
# on the sort column admits.
ARMS = {"off": "0", "on": "1"}
BASELINE_ARM = "off"
# The cross-process regime's depth, for the reason `bench_plan_execution_ab.py` gives: `min` over
# trials is a floor estimator whose error is common-mode within a run, so pairing does not cancel it.
WARMUPS = 6
TRIALS = 30
LIMIT = 60

# Swept, because WHICH mode is measured decides whether the result is a routed win or a latent one.
# In `unique=card` a plane-consumable predicate leaves `FilterExpr::True`, which makes
# `PlanePopcountOrder` applicable, and the router picks it over `StreamedSelect` on exactly the
# clustered cells here (`cmc>=6 order=cmc`: 2.46 us against P3's 12.38). Printing and artwork mode have
# no popcount plan -- a popcount counts cards, not printings -- so P3 is picked and the same improvement
# is what a user waits for. The `routed` column says which happened, per cell.
UNIQUES = ("card", "printing", "artwork")

# (query, orderby, direction, offset). The clustered cells put the match band at the far end of the
# walked order (`asc` over a `>=` predicate on the sort column); the `desc` twin puts the same band at
# the near end, where there is no prefix to skip and only the last-match bound can help. The broad
# cells are the control: nothing to skip at either end.
CLUSTERED = [
    ("cmc>=6", "cmc", "asc", 0),
    ("cmc>=6", "cmc", "asc", 900),
    ("cmc>=6", "cmc", "desc", 900),
    ("cmc>=5", "cmc", "asc", 0),
    ("power>=6", "power", "asc", 0),
    ("toughness>=6", "toughness", "asc", 0),
    ("t:creature cmc>=5", "cmc", "asc", 0),
    ("o:flying cmc>=4", "cmc", "asc", 0),
]
BROAD = [
    ("t:creature", "edhrec", "asc", 0),
    ("cmc>=1", "edhrec", "asc", 0),
    ("o:the", "edhrec", "asc", 0),
    ("c:r", "edhrec", "asc", 0),
]
GROUPS = {"CLUSTERED": CLUSTERED, "BROAD": BROAD}


def measure_one_process() -> None:
    """Child entry point: measure every cell on this process's toggle setting and print JSON."""
    from api.parsing import parse_scryfall_query  # noqa: PLC0415
    from scripts import costbench  # noqa: PLC0415

    engine = costbench.load_engine(CORPUS, CORPUS.with_suffix(".walkspan.store"))
    out = {}
    for group, cells in GROUPS.items():
        for unique in UNIQUES:
            for q, orderby, direction, offset in cells:
                res = engine.explain_analyze(
                    filters=parse_scryfall_query(q),
                    unique=unique,
                    prefer="default",
                    orderby=orderby,
                    direction=direction,
                    limit=LIMIT,
                    offset=offset,
                    num_warmups=WARMUPS,
                    num_trials=TRIALS,
                )
                plan = next((p for p in res["plans"] if p["plan"] == "StreamedSelect"), None)
                if plan is None:
                    continue
                picked = next((p["plan"] for p in res["plans"] if p["picked"]), "?")
                out[f"{group}|{unique}|{q}|{orderby}|{direction}|{offset}"] = {
                    "exec_us": costbench.plan_self_ns(plan, res["acquire"]) / 1000.0,
                    "loop_us": plan["ns_loop"] / 1000.0,
                    "finish_us": plan["ns_finish"] / 1000.0,
                    "perm_steps": plan["perm_steps"],
                    "cards": plan["cards_visited"],
                    "total": plan["result_total"],
                    "picked": picked,
                }
    print("BENCHJSON " + json.dumps(out))


def run_child(env_overlay: dict[str, str]) -> dict:
    """One fresh subprocess: the toggle is a once-per-process LazyLock, so it cannot be re-read."""
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve()), "--child"],
        env={**os.environ, **env_overlay},
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("BENCHJSON "))
    return json.loads(line[len("BENCHJSON ") :])


def main() -> None:
    """Interleave ON/OFF subprocesses, then report per-cell medians and the paired ratio."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reps", type=int, default=5, help="ON/OFF subprocess pairs, interleaved")
    args = parser.parse_args()
    if args.child:
        measure_one_process()
        return

    runs: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    for rep in range(args.reps):
        # Arm order reversed on alternate reps, so a monotonic drift over the run cannot land on one arm.
        arms = list(ARMS.items())
        for label, value in arms if rep % 2 == 0 else reversed(arms):
            runs[label].append(run_child({TOGGLE: value}))
            print(f"  rep {rep + 1}/{args.reps} {label} done", flush=True)

    cells = sorted(set.intersection(*(set(runs[arm][0]) for arm in ARMS)))
    print(f"\n{'cell':<50}{'steps off':>10}{'steps on':>11}{'exec off':>10}{'exec on':>10}{'on/off':>9}{'routed':>20}")
    # Keyed by group AND by whether P3 is the routed plan, because a ratio on a plan the router does not
    # pick is a latent win and must not be averaged in with the realized ones.
    ratios: dict[str, list[float]] = {}

    def med(label: str, cell: str, field: str) -> float:
        """Median of one field for one cell across that arm's subprocesses."""
        return statistics.median(r[cell][field] for r in runs[label])

    for cell in cells:
        group = cell.split("|", 1)[0]
        picked = runs["on"][0][cell]["picked"]
        routed = picked == "StreamedSelect"
        totals = {r[cell]["total"] for arm in ARMS for r in runs[arm]}
        parity = "" if len(totals) == 1 else "  TOTALS DIFFER"
        base = med(BASELINE_ARM, cell, "exec_us")
        ship_ratio = med("on", cell, "exec_us") / base
        ratios.setdefault(f"{group} {'routed' if routed else 'LATENT'}", []).append(ship_ratio)
        label = cell.split("|", 1)[1].replace("|", " ")
        print(
            f"{label:<50}{med('off', cell, 'perm_steps'):>10,.0f}{med('on', cell, 'perm_steps'):>11,.0f}"
            f"{base:>10.2f}{med('on', cell, 'exec_us'):>10.2f}{ship_ratio:>9.3f}"
            f"{picked:>20}{parity}"
        )
    print()
    for group, rs in sorted(ratios.items()):
        print(f"{group:<20} geometric mean exec on/off = {statistics.geometric_mean(rs):.3f} over {len(rs)} cells")
    print(
        "\nCLUSTERED below 1.0 is the win; BROAD at 1.0 is the control staying put -- the bound costs\n"
        "nothing per card, so a query it cannot help should be unchanged rather than taxed.\n"
        "LATENT rows are cells where the router picks another plan, so their ratio is not what a user sees."
    )


if __name__ == "__main__":
    main()
