"""Where does the engine's prediction error actually come from, weighted by how often it happens?

Every other tool here reports error as a DISTRIBUTION -- a cell reads p50 1.47, a spread is 27x. That
answers "how wrong is this when it happens" and silently drops "how often, and on how much of the
work". Those rank differently, and this project has twice spent a round on a term that was badly
wrong on almost nothing: `stream_perm_steps` carries a 15.5x spread and 0.3% of its plan's predicted
cost, and StreamedSelect's `card_pass` count was exactly 2x wrong on a branch its plan is picked on
zero times.

So this ranks error sources by **error MASS** -- each source's share of the total log-error summed
across queries -- instead of by error rate. A source is worth work when its mass is large, which needs
both a big error and a population to happen on.

Three views, deliberately separate because they are different failure modes:

  ESTIMATES   `matches` against the realized `result_total`. A cardinality error the router acts on.
  COST        the picked plan's `predicted_ns` against its measured `plan_self_ns`. What actually
              becomes latency.
  PER-TERM    the money view. For each cost term whose feature has a realized counter, substitute the
              counter and recompute the arm. The drop in total log-error is that feature's error mass,
              in the model's own units. A term with no counter is reported as UNGRADED rather than as
              zero, because "we cannot see it" and "it is fine" are not the same answer.

The per-term substitution is exact rather than approximate: `fit_cost_model.design_row` returns
{term: value} and `CURRENT[plan][term]` holds the shipped coefficient, and that reconstruction is
checked against the engine's own `predicted_ns` per row -- rows where the mirror disagrees are
excluded and counted, so a drifted mirror shows up as a skipped-row count rather than as a finding.

    PYTHONPATH=<wheel> .venv/bin/python scripts/bench_error_attribution_weighted.py --n-queries 8000
"""

from __future__ import annotations

import argparse
import collections
import math
import pathlib
import random
import statistics
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine, plan_self_ns  # noqa: E402
from scripts.fit_cost_model import CURRENT, MIRROR_TOLERANCE, design_row  # noqa: E402

NUM_WARMUPS, NUM_TRIALS = costbench.NUM_WARMUPS, costbench.NUM_TRIALS
#: Below this a measured time is dominated by timer resolution and its log-ratio is noise.
MIN_MEASURED_NS = 500.0
#: Below this a realized counter cannot support a substitution -- a counter of 3 makes any feature
#: look 100x wrong. Same floor `bench_feature_accuracy` uses and justifies.
MIN_COUNTER = 100
#: Cells thinner than this are not reported; a share over a handful of rows says nothing.
MIN_ROWS = 30

#: term -> (acquire feature, realized counter). The substitution this tool performs, and the reason
#: only these terms get a mass rather than an UNGRADED marker. `SCAN_PER_ROW` is a different feature
#: per plan -- `stream_scan_units` for StreamedSelect, `scan_units` for GatheredScan -- so the key is
#: (plan, term), the same lesson `bench_term_contributions.MEASURED_SPREAD` records.
TERM_ORACLE: dict[tuple[str, str], tuple[str, str]] = {
    ("GatheredScan", "LOOP_PER_CARD"): ("eval_domain", "cards_visited"),
    ("GatheredScan", "SCAN_PER_ROW"): ("scan_units", "printings_examined"),
    ("GatheredScan", "CARD_PASS+FLOOR"): ("residual_card_pass", "card_pass_calls"),
    ("GatheredScan", "PUSH_PER_MATCH"): ("matches", "matches_pushed"),
    ("StreamedSelect", "LOOP_PER_CARD"): ("eval_domain", "cards_visited"),
    ("StreamedSelect", "SCAN_PER_ROW"): ("stream_scan_units", "printings_examined"),
    ("StreamedSelect", "CARD_PASS+FLOOR"): ("stream_residual_card_pass", "card_pass_calls"),
    ("StreamedSelect", "EMIT_PER_MATCH"): ("matches", "matches_pushed"),
    ("StreamedSelect", "PERM_STEP"): ("stream_perm_steps", "perm_steps"),
    ("PrintingCompose", "WALK_STEP"): ("printings_walked", "printings_examined"),
    ("PrintingCompose", "GATHER_BITTEST_PER_PRINTING"): ("compose_scan_printings", "printings_examined"),
    ("PrintingCompose", "BROADCAST_PER_PRINTING"): ("broadcast_printings", "broadcast_printings"),
    ("PrintingCompose", "PROJECT_PER_PRINTING"): ("project_printings", "set_printings"),
}


def log_err(pred: float, real: float) -> float:
    """Absolute log ratio -- symmetric in over- and under-prediction, which a percent error is not."""
    if pred <= 0 or real <= 0:
        return 0.0
    return abs(math.log(pred / real))


def collect(engine: object, sampler: QuerySampler, rng: random.Random, budget: costbench.Budget) -> list[dict]:
    """One row per query, carrying the picked plan's prediction, its measurement and its term vector."""
    rows: list[dict] = []
    for sample in costbench.iter_samples(engine, sampler, rng, budget, vary_prefer=True):
        acq = sample.acquire
        picked = next((p for p in sample.plans if p.get("picked")), None)
        if picked is None or not picked.get("trials_ns"):
            continue
        measured = plan_self_ns(picked, acq)
        predicted = picked.get("predicted_ns")
        if not measured or measured < MIN_MEASURED_NS or not predicted or predicted <= 0:
            continue
        built = design_row(picked["plan"], acq, sample.kw["limit"], sample.kw["offset"])
        rows.append(
            {
                "plan": picked["plan"],
                "acquire": acq["count_source"],
                "unique": sample.kw["unique"],
                "paging": picked.get("paging_taken") if picked["plan"] == "PrintingCompose" else None,
                "predicted": float(predicted),
                "measured": float(measured),
                "est_matches": acq["matches"],
                "true_total": picked.get("result_total") or 0,
                "acq": acq,
                "counters": picked,
                "built": built,
            }
        )
    return rows


def share_table(rows: list[dict], key: Callable[[dict], object], label: str, err: str) -> None:
    """Each slice's share of total error mass, beside its share of rows -- the two rank differently."""
    total = sum(r[err] for r in rows) or 1.0
    groups: dict[object, list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    print(f"\n{label}")
    print(f"  {'slice':<38} {'rows':>7} {'row%':>7} {'err mass%':>10} {'median |log|':>13} {'mass/row':>9}")
    for name, sub in sorted(groups.items(), key=lambda kv: -sum(r[err] for r in kv[1])):
        if len(sub) < MIN_ROWS:
            continue
        mass = sum(r[err] for r in sub)
        print(
            f"  {name!s:<38} {len(sub):>7,} {100 * len(sub) / len(rows):>6.1f}% {100 * mass / total:>9.1f}% "
            f"{statistics.median([r[err] for r in sub]):>13.3f} {mass / len(sub):>9.3f}"
        )


def substitute(usable: list[tuple], plan: str, term: str, feature: str, counter: str) -> tuple[float, int]:
    """Total cost log-error with one term's feature replaced by its realized counter, and the n."""
    after, n_sub = 0.0, 0
    for r, terms, excess, coeffs in usable:
        real = r["counters"].get(counter)
        eligible = (
            r["plan"] == plan and term in terms and real is not None and real >= MIN_COUNTER and r["acq"].get(feature) is not None
        )
        if not eligible:
            after += log_err(r["predicted"], r["measured"])
            continue
        n_sub += 1
        swapped = sum(coeffs[t] * (float(real) if t == term else v) for t, v in terms.items()) + excess
        after += log_err(swapped, r["measured"])
    return after, n_sub


def per_term(rows: list[dict]) -> None:
    """Substitute each term's realized counter and report the total cost log-error it removes."""
    usable, mirror_bad = [], 0
    for r in rows:
        if r["built"] is None:
            continue
        terms, excess = r["built"]
        coeffs = CURRENT[r["plan"]]
        mine = sum(coeffs[t] * v for t, v in terms.items()) + excess
        if abs(mine / r["predicted"] - 1.0) >= MIRROR_TOLERANCE:
            mirror_bad += 1
            continue
        usable.append((r, terms, excess, coeffs))
    if not usable:
        print("\nno rows with a reconstructable prediction")
        return

    base = sum(log_err(r["predicted"], r["measured"]) for r, _, _, _ in usable)
    print(f"\n{'=' * 92}\nPER-TERM ERROR MASS -- substitute the realized counter, see what error disappears")
    print(f"{'=' * 92}")
    print(f"{len(usable):,} picked rows with a mirror-exact reconstruction ({mirror_bad} excluded on mirror drift)")
    print(f"total cost log-error mass: {base:.1f}\n")
    print(f"  {'plan / term':<52} {'rows':>7} {'mass removed':>13} {'share':>8}")
    results = []
    for (plan, term), (feature, counter) in TERM_ORACLE.items():
        after, n_sub = substitute(usable, plan, term, feature, counter)
        if n_sub >= MIN_ROWS:
            results.append((base - after, plan, term, n_sub))
    for removed, plan, term, n_sub in sorted(results, reverse=True):
        print(f"  {plan + ' / ' + term:<52} {n_sub:>7,} {removed:>13.2f} {100 * removed / base:>7.1f}%")
    print("\n  Positive = substituting truth REMOVES error, so the feature is a real source.")
    print("  Negative = the feature's error was CANCELLING another term's; fixing it alone makes the")
    print("  arm worse, which is exactly what Round 76 measured and shipped anyway on correctness grounds.")

    ungraded = collections.Counter()
    for r, terms, _, coeffs in usable:
        for t, v in terms.items():
            if (r["plan"], t) not in TERM_ORACLE and coeffs[t] * v > 0:
                ungraded[f"{r['plan']} / {t}"] += 1
    print("\n  UNGRADED terms that carry nonzero cost (no counter exists -- unmeasured, not clean):")
    for name, n in ungraded.most_common(8):
        print(f"    {name:<50} {n:>7,} rows")


def main() -> None:
    """Rank estimate and cost error sources by mass rather than by rate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n-queries", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".errattr.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    budget = costbench.Budget(sample=args.n_queries, warmups=NUM_WARMUPS, trials=NUM_TRIALS)
    rows = collect(engine, sampler, random.Random(args.seed), budget)
    for r in rows:
        r["cost_err"] = log_err(r["predicted"], r["measured"])
        r["est_err"] = log_err(float(r["est_matches"]), float(r["true_total"])) if r["true_total"] else 0.0
    print(f"\n{len(rows):,} picked rows, mode={args.mode}, bound={args.n_queries:,} queries")

    est = [r for r in rows if r["true_total"] >= MIN_COUNTER]
    print(f"\n{'=' * 92}\nESTIMATE ERROR -- `matches` against realized `result_total` ({len(est):,} rows)\n{'=' * 92}")
    print(f"total estimate log-error mass: {sum(r['est_err'] for r in est):.1f}")
    share_table(est, lambda r: r["acquire"], "by acquire route", "est_err")
    share_table(est, lambda r: r["unique"], "by distinct-on", "est_err")

    print(f"\n{'=' * 92}\nCOST ERROR -- picked plan's predicted_ns against measured\n{'=' * 92}")
    print(f"total cost log-error mass: {sum(r['cost_err'] for r in rows):.1f}")
    share_table(rows, lambda r: r["plan"], "by plan", "cost_err")
    share_table(rows, lambda r: f"{r['plan']} [{r['acquire']}]", "by plan and acquire route", "cost_err")
    share_table(rows, lambda r: r["unique"], "by distinct-on", "cost_err")
    compose = [r for r in rows if r["plan"] == "PrintingCompose"]
    if len(compose) >= MIN_ROWS:
        share_table(compose, lambda r: f"compose {r['paging']}", "compose only, by paging branch taken", "cost_err")
    per_term(rows)


if __name__ == "__main__":
    main()
