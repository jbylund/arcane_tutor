"""A/B the streamed walk's match-span bound (`CARD_ENGINE_SPAN_TRACK_CANDIDATE_DIVISOR`).

The bound trades work in two phases at once, so a single number cannot judge it:

* `ns_finish` — the walk itself. Bounded to the match span it steps from the first matching card to
  the last instead of from permutation index 0 to wherever the page happens to fill.
* `ns_loop` — the match loop, which pays one `inv_perm` read plus a `min` and a `max` per MATCHING
  card to learn that span.

So the cells come in two groups. CLUSTERED pairs a predicate with an orderby it correlates with, which
is where a walk from index 0 grinds through a long non-matching prefix. BROAD has matches spread
through the permutation, so the walk fills its page in ~`limit` steps and the bound can only cost.
Both groups matter: the change is only worth taking if the first group's win is real AND the second
group's cost is not.

**Why a toggle rather than two builds.** Cross-build comparison cannot resolve this. Measured on
these cells, `ns_loop` wandered 43.00 / 45.38 / 47.17 us across three runs on the same two builds —
±9%, in both directions, on a phase neither build changed. That is the common-mode floor error
`bench_plan_execution_ab.py` documents, plus whatever the linker did to code layout, and the per-card
cost being hunted here is smaller than it. One binary with a runtime toggle removes both: identical
code, identical layout, and the only difference is a branch that was already there.

Both variants set the env var, to EQUAL-LENGTH values (`00001` / `99999`, both parsed as usize), because
an env var present in one branch only shifts process memory layout enough to move sub-100us queries a
consistent ~15% either way -- the same bias `bench_verify_order.py` documents and equalizes.

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
TOGGLE = "CARD_ENGINE_SPAN_TRACK_CANDIDATE_DIVISOR"
# Equal-length values, both valid usize: 1 tracks the span for every query (candidates <= n_cards),
# 99999 for none (n_cards / 99999 == 0 on any real corpus).
ON_ENV = {TOGGLE: "00001"}
OFF_ENV = {TOGGLE: "99999"}
# The cross-process regime's depth, for the reason `bench_plan_execution_ab.py` gives: `min` over
# trials is a floor estimator whose error is common-mode within a run, so pairing does not cancel it.
WARMUPS = 6
TRIALS = 30
LIMIT = 60

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
        for q, orderby, direction, offset in cells:
            res = engine.explain_analyze(
                filters=parse_scryfall_query(q),
                unique="card",
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
            out[f"{group}|{q}|{orderby}|{direction}|{offset}"] = {
                "exec_us": costbench.plan_self_ns(plan, res["acquire"]) / 1000.0,
                "loop_us": plan["ns_loop"] / 1000.0,
                "finish_us": plan["ns_finish"] / 1000.0,
                "perm_steps": plan["perm_steps"],
                "cards": plan["cards_visited"],
                "total": plan["result_total"],
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

    runs: dict[str, list[dict]] = {"on": [], "off": []}
    for rep in range(args.reps):
        # A/B/B/A within each pair, so a monotonic drift over the run cannot land on one arm.
        order = [("off", OFF_ENV), ("on", ON_ENV)] if rep % 2 else [("on", ON_ENV), ("off", OFF_ENV)]
        for label, overlay in order:
            runs[label].append(run_child(overlay))
            print(f"  rep {rep + 1}/{args.reps} {label} done", flush=True)

    cells = sorted(set(runs["on"][0]) & set(runs["off"][0]))
    print(
        f"\n{'cell':<44}{'steps off':>10}{'steps on':>9}"
        f"{'loop off':>10}{'loop on':>9}{'exec off':>10}{'exec on':>9}{'on/off':>8}"
    )
    ratios: dict[str, list[float]] = {"CLUSTERED": [], "BROAD": []}
    def med(label: str, cell: str, field: str) -> float:
        """Median of one field for one cell across that arm's subprocesses."""
        return statistics.median(r[cell][field] for r in runs[label])

    for cell in cells:
        group = cell.split("|", 1)[0]
        totals = {r[cell]["total"] for r in runs["on"] + runs["off"]}
        parity = "" if len(totals) == 1 else "  TOTALS DIFFER"
        ratio = med("on", cell, "exec_us") / med("off", cell, "exec_us")
        ratios[group].append(ratio)
        label = cell.split("|", 1)[1].replace("|", " ")
        print(
            f"{label:<44}{med('off', cell, 'perm_steps'):>10,.0f}{med('on', cell, 'perm_steps'):>9,.0f}"
            f"{med('off', cell, 'loop_us'):>10.2f}{med('on', cell, 'loop_us'):>9.2f}"
            f"{med('off', cell, 'exec_us'):>10.2f}{med('on', cell, 'exec_us'):>9.2f}{ratio:>8.3f}{parity}"
        )
    print()
    for group, rs in ratios.items():
        if rs:
            print(f"{group}: geometric mean exec on/off = {statistics.geometric_mean(rs):.3f} over {len(rs)} cells")
    print("\nCLUSTERED below 1.0 is the win; BROAD above 1.0 is what the bound costs where it cannot help.")


if __name__ == "__main__":
    main()
