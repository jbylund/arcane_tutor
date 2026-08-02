"""Acceptance test for the range-cardinality estimate: scan each dimension, compare estimate to truth.

`docs/issues/local-engine-range-cardinality-estimate.md` proposes replacing `card_est = k.min(n_cards)`
with a three-column boundary table (prefix / suffix / per-value distinct-card counts), which should be
**exact**. This is the test that says whether it worked.

It sweeps thresholds across each range dimension in both directions and reports
`acquire.matches / true total` at every point. Run it before and after the change:

- **before** — `unique=card` shows the proxy's error, a median around 1.49x
- **after** — every `unique=card` row should read 1.000

Scoped to the dimensions that actually reach this acquire. `eur` and `tix` have no range index, so
they route elsewhere and are excluded -- see SCANS.

`unique=printing` is included as a control that should *already* be exact: that acquire branch
(`printing_range_scan`) sets `matches = k`, the in-range printing count, and for printing mode the
result cardinality IS the printing count. If those rows are not already 1.000, that assumption is
wrong and the branch needs wiring too.

    .venv/bin/python scripts/bench_range_estimate_scan.py

Scans rather than a pooled median on purpose: every pooled figure in this investigation hid structure
that only showed up per-cell.
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

# Thresholds per dimension, spanning each field's real distribution from a handful of cards to
# nearly the whole corpus. Deliberately the same shapes the investigation swept.
SCANS: dict[str, list[str]] = {
    "usd": ["0.25", "1", "5", "20", "50", "200"],
    "cn": ["20", "100", "300"],
    "year": ["1999", "2009", "2018", "2023"],
    "date": ["2005-06-01", "2015-03-01", "2022-01-01"],
    # `eur` and `tix` are deliberately absent: only `price_usd` has a PrintingRangeIndex, and
    # `resolve_numeric_range_leaf` maps only PriceUsd, so those predicates never reach
    # `bare_range_bounds`. They fall through to the general candidates path, where `matches` is the
    # unnarrowed card count -- measured at up to 106x off, which is a missing index rather than a
    # bad estimate and is out of scope here. Add them if either ever gets an index.
}
DIRECTIONS = ("<", ">")
# An estimate this close to truth counts as exact; the target for the boundary table is 1.000.
EXACT_TOL = 0.01


def main() -> None:
    """Sweep every dimension and direction, printing estimate/true per point and a per-cell summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    parser.add_argument("--unique", default="card,printing", help="comma-separated modes to scan")
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".estimate-scan.store"))

    for unique in args.unique.split(","):
        print(f"\n=== unique={unique} ===")
        print(f"{'query':<16}{'acquire':>20}{'estimate':>10}{'true':>9}{'est/true':>10}")
        cells: dict[str, list[float]] = {}
        for dim, thresholds in SCANS.items():
            for direction in DIRECTIONS:
                ratios = []
                for t in thresholds:
                    q = f"{dim}{direction}{t}"
                    try:
                        filters = parse_scryfall_query(q)
                        kw = {"filters": filters, "unique": unique, "orderby": "edhrec", "direction": "asc", "offset": 0}
                        acq = engine.explain(limit=100, **kw)["acquire"]
                        true = engine.query(prefer="default", limit=1, **kw)[0]
                    except Exception as exc:  # noqa: BLE001 - a rejected query is a skipped scan point
                        print(f"{q:<16}{'skipped':>20}  {exc}")
                        continue
                    if true == 0:
                        continue
                    ratio = acq["matches"] / true
                    ratios.append(ratio)
                    flag = "" if abs(ratio - 1.0) <= EXACT_TOL else "  <-- off"
                    print(f"{q:<16}{acq['count_source']:>20}{acq['matches']:>10,}{true:>9,}{ratio:>10.3f}{flag}")
                if ratios:
                    cells[f"{dim}{direction}x"] = ratios

        print(f"\n{'cell':<12}{'n':>4}{'median':>9}{'worst':>9}{'all exact?':>12}")
        worst_overall = 0.0
        for cell, ratios in cells.items():
            worst = max(abs(r - 1.0) for r in ratios)
            worst_overall = max(worst_overall, worst)
            verdict = "yes" if worst <= EXACT_TOL else "NO"
            print(f"{cell:<12}{len(ratios):>4}{statistics.median(ratios):>9.3f}{max(ratios):>9.3f}{verdict:>12}")
        status = "PASS" if worst_overall <= EXACT_TOL else "FAIL"
        print(f"\n{unique}: worst deviation {worst_overall:.3f} — {status} (target: every cell within {EXACT_TOL:.0%})")


if __name__ == "__main__":
    main()
