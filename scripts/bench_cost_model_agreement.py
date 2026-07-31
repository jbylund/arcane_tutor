"""How well does `cost::plan_cost` agree with measured plan time, across the whole query space?

Not a mis-selection check — this asks whether each plan's cost ARM is right, per acquire branch, per
`unique`, per `orderby`. A plan can be picked correctly for a long time while its arm is badly wrong,
and only diverge once something competes closely with it.

Sampling is deliberately unbiased where the existing generator is not:

- `unique` is drawn evenly across card / printing / artwork, not 75/20/5. Distinct-on changes which
  plans are even applicable and what `scan_units` means, so under-sampling two thirds of it hides
  exactly the cells most likely to be wrong.
- `orderby` is drawn across every column the engine supports, since the permutation's existence is
  what gates `StreamedSelect` and `PlanePopcountOrder`.
- range thresholds are sampled from each field's real value distribution rather than a hand-picked
  list, so selectivity is spread instead of clustered at round numbers.

Reports measured/predicted per (plan, acquire branch), plus what fraction of the time is
`prepare_candidates` — which no cost term currently describes and which is 21-33% of a range-acquired
query against 7-10% of a plane-acquired one.

    .venv/bin/python scripts/bench_cost_model_agreement.py --seconds 120
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import math
import pathlib
import random
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from scripts.bench_bitplanes import load_engine  # noqa: E402

NUM_WARMUPS = 2
NUM_TRIALS = 7
# Every distinct-on the engine supports, evenly weighted — see the module docstring.
UNIQUES = ("card", "printing", "artwork")
# Every orderby `orderby_to_col` maps. Which ones have a sort permutation decides plan applicability.
ORDERBYS = ("edhrec", "cubecobra", "cmc", "power", "toughness", "rarity", "usd", "name")
LIMITS = (10, 100, 175)
OFFSETS = (0, 0, 0, 100)  # mostly first-page, which is what real traffic asks for
MIN_FOR_QUARTILES = 8
# The agreement bar this work is aiming at: every (plan, acquire) cell's median inside it.
AGREE_LO, AGREE_HI = 0.8, 1.25

# Predicate templates, one per engine path, so no single family dominates the sample the way a
# weighted-dimension generator does. A query is one or two of these joined.
PREDICATES: dict[str, list[str]] = {
    "plane_color": ["c:w", "c:u", "c:b", "c:r", "c:g", "c:wu", "c:br", "id:g", "id:wu"],
    "plane_type": ["t:creature", "t:instant", "t:artifact", "t:land", "t:enchantment", "t:sorcery"],
    "legality": ["f:modern", "f:commander", "f:legacy", "f:pauper", "f:standard", "f:vintage"],
    "text": ["o:flying", "o:draw", "o:destroy", "o:trample", "o:counter", "o:sacrifice"],
    "collection": ["is:reprint", "border:black", "border:borderless", "frame:showcase", "watermark:set"],
    "arith": ["pow>2", "tou<4", "power+toughness<6", "cmc>=4", "loyalty>=3"],
}
# Range fields with a real index, and the value ranges to sample thresholds from.
RANGE_FIELDS: dict[str, tuple[float, float, bool]] = {
    "usd": (0.05, 400.0, True),  # log-sampled: prices span four orders of magnitude
    "cn": (1, 500, False),
    "year": (1993, 2026, False),
}
RANGE_OPS = (">", ">=", "<", "<=", ":")


def sample_range(rng: random.Random) -> str:
    """One range predicate with the threshold drawn from the field's own distribution."""
    field, (lo, hi, log_scale) = rng.choice(list(RANGE_FIELDS.items()))
    value = f"{math.exp(rng.uniform(math.log(lo), math.log(hi))):.2f}" if log_scale else str(rng.randint(int(lo), int(hi)))
    op = rng.choice(RANGE_OPS)
    return f"{field}{op}{value}"


def sample_query(rng: random.Random) -> str:
    """One or two predicates, each family equally likely — ranges included as their own family."""
    families = [*PREDICATES.keys(), "range"]
    n = rng.choice((1, 1, 2))
    parts, used = [], set()
    for _ in range(n):
        fam = rng.choice(families)
        if fam in used:
            continue
        used.add(fam)
        parts.append(sample_range(rng) if fam == "range" else rng.choice(PREDICATES[fam]))
    return " ".join(parts)


def main() -> None:
    """Sample until the budget runs out, then report agreement per plan and acquire branch."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".agreement.store"))
    rng = random.Random(args.seed)

    agr = Agreement()

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        q, unique = sample_query(rng), rng.choice(UNIQUES)
        kw = {
            "filters": None,
            "unique": unique,
            "orderby": rng.choice(ORDERBYS),
            "direction": rng.choice(("asc", "desc")),
            "limit": rng.choice(LIMITS),
            "offset": rng.choice(OFFSETS),
        }
        try:
            kw["filters"] = parse_scryfall_query(q)
            acq = engine.explain(**kw)["acquire"]
            res = engine.explain_analyze(prefer="default", num_warmups=NUM_WARMUPS, num_trials=NUM_TRIALS, **kw)
        except Exception:  # noqa: BLE001 - a rejected query is a skipped sample
            agr.skipped += 1
            continue
        agr.sampled += 1
        for p in res["plans"]:
            if not p["trials_ns"] or p["predicted_ns"] <= 0:
                continue
            measured = min(p["trials_ns"])
            agr.ratios[p["plan"], acq["count_source"]].append(measured / p["predicted_ns"])
            agr.by_unique[p["plan"], unique].append(measured / p["predicted_ns"])
            if p["ns_round_total"]:
                agr.prep_frac[p["plan"]].append(p["ns_prepare"] / p["ns_round_total"])

    report(agr, args.seconds)


def summarise(label: str, groups: dict[tuple[str, str], list[float]], col: str) -> None:
    """One table: median measured/predicted per cell, and how many cells clear the agreement bar."""
    cells: list[tuple[str, float]] = []
    print(f"\n{'plan':<20}{col:<22}{'n':>5}{'median':>9}{'p10':>8}{'p90':>8}{'within 25%':>12}")
    for (plan, key), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        if len(rs) < MIN_FOR_QUARTILES:
            continue
        ds = statistics.quantiles(rs, n=10)
        near = sum(1 for r in rs if AGREE_LO <= r <= AGREE_HI) / len(rs)
        med = statistics.median(rs)
        verdict = "" if AGREE_LO <= med <= AGREE_HI else "  FAIL"
        print(f"{plan:<20}{key:<22}{len(rs):>5}{med:>9.2f}{ds[0]:>8.2f}{ds[8]:>8.2f}{near:>11.0%}{verdict}")
        cells.append((f"{plan}/{key}", med))
    passing = sum(1 for _, m in cells if AGREE_LO <= m <= AGREE_HI)
    print(f"  {label}")
    print(f"  {passing}/{len(cells)} cells inside [{AGREE_LO}, {AGREE_HI}]")


@dataclasses.dataclass
class Agreement:
    """Everything the sweep accumulates, keyed for the three tables the report prints."""

    ratios: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    by_unique: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    prep_frac: dict[str, list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    sampled: int = 0
    skipped: int = 0


def report(agr: Agreement, seconds: float) -> None:
    """Agreement per acquire branch and per distinct-on, then the unpriced prepare share."""
    print(f"\n{agr.sampled:,} queries sampled ({agr.skipped:,} skipped) in {seconds:.0f}s")
    summarise("measured/predicted by acquire branch. 1.00 is agreement; >1 under-costed.", agr.ratios, "acquire")
    summarise("the same, split by distinct-on rather than acquire.", agr.by_unique, "unique")

    print(f"\n{'plan':<20}{'n':>6}{'median prep share':>20}{'p90':>8}")
    for plan, fracs in sorted(agr.prep_frac.items()):
        if len(fracs) < MIN_FOR_QUARTILES:
            continue
        print(f"{plan:<20}{len(fracs):>6}{statistics.median(fracs):>19.0%}{statistics.quantiles(fracs, n=10)[8]:>8.0%}")
    print("  prepare_candidates as a share of the plan's run.")


if __name__ == "__main__":
    main()
