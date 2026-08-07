"""Targeted end-to-end A/B for #849 — bitmap materialization at the CSR narrowing arms.

`expand_csr` turns the posting rows of an oracle-text, artist, or flavor predicate into the
ascending candidate vector the driver requires. It used to concatenate the rows and
`sort_unstable`; it now routes through `sorted_ids`, which scatters into a bitmap and reads
the set bits back once the domain:count ratio says that is cheaper (`MATERIALIZE_BITMAP_RATIO`).

`card_engine/src/bench_expand_materialize.rs` measures the kernel; this measures what the
change is worth from the outside. Same binary both sides — `CARD_ENGINE_RANGE_MATERIALIZE_BITMAP`
picks the route, so nothing but the route differs:

    for r in 1 2 3; do
      for v in 0 1; do
        CARD_ENGINE_RANGE_MATERIALIZE_BITMAP=$v .venv/bin/python scripts/bench_expand_materialize.py \
            --out benchmarks/expand-materialize/r$r-flag$v.csv
      done
    done
    .venv/bin/python scripts/bench_expand_materialize.py --compare benchmarks/expand-materialize

The flag also governs the range arms (#845), so no target or control here is a range predicate:
whatever moves is `expand_csr`. `total` is the parity check — identical across both sides for
every config, or the comparison is void.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import statistics
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.bench_bitplanes import bench_one  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

# (group, query, unique, orderby, prefer) — direction=asc, limit=100, offset=0 throughout.
#
# Which queries reach `expand_csr` was PROBED against the real corpus, not assumed — an
# earlier version of this list guessed wrong. Single-word `o:` needles reach it too: the
# dictionary's dense tier is only ~56 words, so `o:flying` skips the CSR and `o:trample`,
# `o:sacrifice`, `o:landwalk` do not. `control` rows reach no CSR arm at all (probed: zero
# calls) and must not move. `ft:the` is a control in disguise and is labelled as one —
# `range_too_broad_to_narrow` declines it before the CSR, so it pins the flavor arm's ceiling.
CONFIGS: list[tuple[str, str, str, str, str]] = [
    ("oracle", "o:trample", "card", "edhrec", "default"),
    ("oracle", "o:sacrifice", "card", "edhrec", "default"),
    ("oracle", "o:landwalk", "card", "edhrec", "default"),
    ("oracle", "o:the", "card", "edhrec", "default"),
    ("oracle", 'o:"draw a card"', "card", "edhrec", "default"),
    ("oracle", 'o:"you control"', "card", "edhrec", "default"),
    ("oracle", 'o:"whenever you cast"', "card", "edhrec", "default"),
    ("oracle", 'o:"draw a card" t:creature', "card", "edhrec", "default"),
    ("regex", "o:/counters? on/", "card", "edhrec", "default"),
    ("artist", "a:john", "printing", "edhrec", "default"),
    ("artist", "a:a", "printing", "edhrec", "default"),
    ("artist", "a:e", "card", "edhrec", "default"),
    ("artist", "a:rebecca", "printing", "edhrec", "default"),
    ("flavor", "ft:dragon", "printing", "edhrec", "default"),
    ("flavor", "ft:death", "card", "edhrec", "default"),
    ("flavor", "ft:war t:creature", "printing", "edhrec", "default"),
    ("control", "ft:the", "printing", "edhrec", "default"),
    ("control", "o:flying", "card", "edhrec", "default"),
    ("control", "t:creature", "card", "edhrec", "default"),
    ("control", "f:modern", "card", "edhrec", "default"),
    ("control", "c:g", "card", "edhrec", "default"),
    ("control", "name:bolt", "card", "edhrec", "default"),
]

#: Timed seconds per config. Long enough that `min_ms` converges to a floor rather than to
#: whatever interference the first few iterations saw.
DEFAULT_WINDOW = 6.0


def measure(out: pathlib.Path, corpus: pathlib.Path, shm_path: pathlib.Path | None, window: float) -> None:
    """Time every config once and write the results CSV."""
    rev = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    engine = load_engine(corpus, shm_path or out.with_suffix(".store"))

    hdr = f"{'group':<9} {'query':<30} {'unique':<9} {'total':>7} {'avg ms':>8} {'min ms':>8}"
    print(f"\nrev {rev}, window {window:.0f}s per config\n{hdr}\n{'-' * len(hdr)}", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rev", "group", "query", "unique", "orderby", "prefer", "total", "n", "avg_ms", "min_ms"])
        for group, query, unique, orderby, prefer in CONFIGS:
            total, n, avg_ms, min_ms = bench_one(engine, (query, unique, orderby, prefer), window)
            writer.writerow([rev, group, query, unique, orderby, prefer, total, n, f"{avg_ms:.4f}", f"{min_ms:.4f}"])
            fh.flush()
            print(f"{group:<9} {query:<30} {unique:<9} {total:>7} {avg_ms:>8.3f} {min_ms:>8.3f}", flush=True)

    print(f"\nWrote {out}")


def compare(directory: pathlib.Path) -> None:
    """Pair `*-flag0.csv` against `*-flag1.csv` per query and report the median ratio per group."""

    def load(pattern: str) -> dict[str, list[tuple[int, float]]]:
        rows: dict[str, list[tuple[int, float]]] = {}
        for path in sorted(directory.glob(pattern)):
            with path.open() as fh:
                for row in csv.DictReader(fh):
                    rows.setdefault(row["query"], []).append((int(row["total"]), float(row["min_ms"])))
        return rows

    sort_side, bitmap_side = load("*-flag0.csv"), load("*-flag1.csv")
    if not sort_side or not bitmap_side:
        print(f"No paired CSVs under {directory} (expected *-flag0.csv and *-flag1.csv)")
        return

    group_of = {q: g for g, q, *_ in CONFIGS}
    by_group: dict[str, list[float]] = {}
    hdr = f"{'group':<9} {'query':<30} {'sort ms':>9} {'bitmap ms':>10} {'ratio':>7}"
    print(f"\n{hdr}\n{'-' * len(hdr)}")
    for query in [q for _, q, *_ in CONFIGS]:
        a, b = sort_side.get(query), bitmap_side.get(query)
        if not a or not b:
            continue
        totals = {t for t, _ in a} | {t for t, _ in b}
        if len(totals) != 1:
            # Both sides must return the same rows or the timing comparison means nothing.
            print(f"{group_of[query]:<9} {query:<30}   PARITY FAILED: totals {sorted(totals)}")
            continue
        a_ms, b_ms = min(ms for _, ms in a), min(ms for _, ms in b)
        ratio = b_ms / a_ms
        by_group.setdefault(group_of[query], []).append(ratio)
        print(f"{group_of[query]:<9} {query:<30} {a_ms:>9.3f} {b_ms:>10.3f} {ratio:>7.3f}")

    print(f"\n{'group':<9} {'n':>3} {'median ratio':>13}")
    for group, ratios in by_group.items():
        print(f"{group:<9} {len(ratios):>3} {statistics.median(ratios):>13.3f}")


def main() -> None:
    """Measure one side, or compare a directory of paired runs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--out", type=pathlib.Path, help="CSV output path")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW, help="timed seconds per config")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None, help="engine archive path (default: alongside --out)")
    parser.add_argument("--compare", type=pathlib.Path, help="directory of *-flag0.csv / *-flag1.csv runs to pair")
    args = parser.parse_args()

    if args.compare:
        compare(args.compare)
        return
    if not args.out:
        parser.error("--out is required unless --compare is given")
    measure(args.out, args.corpus, args.shm_path, args.window)


if __name__ == "__main__":
    main()
