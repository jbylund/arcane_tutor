"""On the queries that lose the most time to a mis-pick, is it the FEATURES or the rates?

The costliest mis-picks are near-ties the model lost: predicted ratios of 0.63-1.00 where the truth
was 1.6-2.9x. That is a PAIRWISE failure -- the two arms' errors having different signs on the same
query -- and no single-arm accuracy metric can see it, which is why twenty rounds of feature grading
did not surface these.

So ask the question directly. For each high-loss mis-pick, rebuild BOTH arms' predictions with every
oracle-backed feature replaced by its realized counter, and re-run the two-way comparison:

  would perfect features have picked the right plan?

That splits the tail cleanly. A mis-pick the oracle fixes is a FEATURE problem on that query, however
well the feature grades in aggregate. One the oracle does not fix is a rate or model-form problem, and
no amount of estimator work will reach it.

Also reports each feature's estimate/realized ratio on the tail against the same feature on everything
else, so a feature that is fine in aggregate and wrong exactly here shows up as itself.

Both plans are timed inside ONE `explain_analyze` call, so their measurements are common-mode and the
comparison is readable at microsecond scale.

    PYTHONPATH=<wheel> .venv/bin/python scripts/bench_high_loss_features.py --n-queries 8000 --top 40
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import random
import statistics
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from client.query_sampler import MODES, QuerySampler  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.bench_error_attribution_weighted import TERM_ORACLE, substitutable  # noqa: E402
from scripts.costbench import Budget, iter_samples, load_engine, plan_self_ns  # noqa: E402
from scripts.fit_cost_model import CURRENT, MIRROR_TOLERANCE, design_row  # noqa: E402

#: Below this a plan's measured time is timer resolution. Values quantize to ~41.67 ns (24 MHz).
MIN_MEASURED_NS = 500.0
#: Feature/counter pairs to grade on the tail. Keyed (plan, term) exactly as the oracle is, because
#: `SCAN_PER_ROW` is a different feature per plan.
GRADE_PAIRS = TERM_ORACLE
#: How many of the costliest mis-picks form the "tail" when `--top` is not given.
DEFAULT_TOP = 40
DEFAULT_N_QUERIES = 8000


def rebuilt(row: dict, plan: str, *, oracle: bool) -> float | None:
    """That plan's predicted ns, either as shipped or with every gradeable feature set to truth."""
    built = design_row(plan, row["acq"], row["limit"], row["offset"])
    if built is None:
        return None
    terms, excess = built
    coeffs = CURRENT[plan]
    counters = row["counters"].get(plan)
    total = excess
    for term, value in terms.items():
        swapped = value
        pair = GRADE_PAIRS.get((plan, term))
        if oracle and pair and counters is not None:
            probe = {"plan": plan, "counters": counters, "acq": row["acq"], "unique": row["unique"], "paging": row["paging"]}
            if substitutable(probe, terms, plan, term, pair):
                swapped = float(counters[pair[1]])
        total += coeffs[term] * swapped
    return total


def collect(engine: object, sampler: QuerySampler, rng: random.Random, budget: Budget) -> list[dict]:
    """One row per query where at least two plans ran, carrying every plan's counters and prediction."""
    rows: list[dict] = []
    for sample in iter_samples(engine, sampler, rng, budget, vary_prefer=True):
        acq = sample.acquire
        timed, counters = {}, {}
        for p in sample.plans:
            if not p.get("trials_ns"):
                continue
            self_ns = plan_self_ns(p, acq)
            if self_ns and self_ns >= MIN_MEASURED_NS:
                timed[p["plan"]] = self_ns
                counters[p["plan"]] = p
        picked = next((p["plan"] for p in sample.plans if p.get("picked")), None)
        if picked is None or picked not in timed or len(timed) < 2:  # noqa: PLR2004
            continue
        best = min(timed, key=lambda k: timed[k])
        rows.append(
            {
                "q": sample.q,
                "unique": sample.kw["unique"],
                "limit": sample.kw["limit"],
                "offset": sample.kw["offset"],
                "acq": acq,
                "acquire": acq["count_source"],
                "paging": counters[picked].get("paging_taken") if picked == "PrintingCompose" else acq["compose_paging"],
                "counters": counters,
                "timed": timed,
                "picked": picked,
                "best": best,
                "loss": timed[picked] - timed[best],
            }
        )
    return rows


def oracle_verdict(tail: list[dict]) -> None:
    """Would perfect features have picked the right plan on each high-loss miss?"""
    fixed, unfixed, unusable, moved_wrong = 0, 0, 0, 0
    for r in tail:
        preds = {}
        for plan in r["timed"]:
            shipped = rebuilt(r, plan, oracle=False)
            engine_pred = r["counters"][plan].get("predicted_ns")
            # Only trust a rebuild the mirror reproduces; otherwise this row's arms are not ours.
            if shipped is None or not engine_pred or abs(shipped / engine_pred - 1.0) >= MIRROR_TOLERANCE:
                preds = {}
                break
            preds[plan] = rebuilt(r, plan, oracle=True)
        if not preds or any(v is None for v in preds.values()):
            unusable += 1
            continue
        new_pick = min(preds, key=lambda k: preds[k])
        if new_pick == r["best"]:
            fixed += 1
        elif new_pick == r["picked"]:
            unfixed += 1
        else:
            moved_wrong += 1
    total = fixed + unfixed + moved_wrong
    print(f"\n{'=' * 92}\nWOULD PERFECT FEATURES FIX THESE MIS-PICKS?\n{'=' * 92}")
    print(f"  {len(tail):,} high-loss mis-picks, {total:,} with a mirror-exact rebuild ({unusable} excluded)")
    if total:
        print(f"    fixed by the oracle          {fixed:>5}  ({100 * fixed / total:.0f}%)  <- FEATURE problem on this query")
        print(f"    still picks the same plan    {unfixed:>5}  ({100 * unfixed / total:.0f}%)  <- rate or model-form problem")
        print(f"    moves to a third, still wrong{moved_wrong:>5}  ({100 * moved_wrong / total:.0f}%)")


def feature_table(tail: list[dict], rest: list[dict]) -> None:
    """Each feature's estimate/realized ratio on the tail against the same feature elsewhere."""

    def ratios(rows: list[dict]) -> dict[tuple[str, str], list[float]]:
        out: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
        for r in rows:
            for plan in r["timed"]:
                built = design_row(plan, r["acq"], r["limit"], r["offset"])
                if built is None:
                    continue
                terms, _ = built
                for (p, term), pair in GRADE_PAIRS.items():
                    if p != plan or term not in terms:
                        continue
                    counters = r["counters"][plan]
                    probe = {"plan": plan, "counters": counters, "acq": r["acq"], "unique": r["unique"], "paging": r["paging"]}
                    if not substitutable(probe, terms, plan, term, pair):
                        continue
                    real = float(counters[pair[1]])
                    est = float(r["acq"][pair[0]])
                    if real > 0:
                        out[(plan, term)].append(est / real)
        return out

    a, b = ratios(tail), ratios(rest)
    print(f"\n{'=' * 92}\nFEATURE ACCURACY ON THE TAIL vs EVERYWHERE ELSE (estimate / realized)\n{'=' * 92}")
    print(f"  {'plan / term':<48} {'n tail':>7} {'tail p50':>9} {'rest p50':>9} {'shift':>7}")
    for key in sorted(a, key=lambda k: -len(a[k])):
        if len(a[key]) < 5 or key not in b or len(b[key]) < 20:  # noqa: PLR2004
            continue
        ta, tb = statistics.median(a[key]), statistics.median(b[key])
        print(f"  {key[0] + ' / ' + key[1]:<48} {len(a[key]):>7,} {ta:>9.3f} {tb:>9.3f} {ta / tb if tb else float('nan'):>7.2f}x")
    print("\n  `shift` far from 1.00 means the feature behaves DIFFERENTLY on the costly queries than")
    print("  in aggregate -- which is exactly what a pooled grading cannot show.")


def main() -> None:
    """Split the high-loss mis-picks into feature problems and rate problems."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n-queries", type=int, default=DEFAULT_N_QUERIES)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="how many costliest mis-picks form the tail")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="uniform")
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".hiloss.store"))
    sampler = QuerySampler(args.corpus, args.mode)
    budget = Budget(sample=args.n_queries, warmups=costbench.NUM_WARMUPS, trials=costbench.NUM_TRIALS)
    rows = collect(engine, sampler, random.Random(args.seed), budget)
    miss = sorted((r for r in rows if r["picked"] != r["best"]), key=lambda r: -r["loss"])
    if not miss:
        print("no mis-picks")
        return
    tail = miss[: args.top]
    rest = [r for r in rows if r["picked"] == r["best"]]
    tot_lost = sum(r["loss"] for r in miss) or 1.0
    print(f"\n{len(rows):,} queries, {len(miss):,} mis-picks, mode={args.mode}")
    print(f"  the top {len(tail)} by loss carry {100 * sum(r['loss'] for r in tail) / tot_lost:.1f}% of all lost time")
    oracle_verdict(tail)
    feature_table(tail, rest)


if __name__ == "__main__":
    main()
