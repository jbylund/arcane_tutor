"""Selectivity sweep for the #743 arith-expression tuple postings.

Companion to `scripts/bench_arith_tuple_postings.py`. That script proves the #743 narrowing
helps the two survey-identified queries; this one asks how the win *scales with selectivity*.
The tuple scan's cost is fixed (evaluate the predicate against ~564 distinct
`(cmc,power,toughness,loyalty)` combinations, then union the matching combinations' card
postings), while the pre-#743 `GatheredScan` cost is proportional to the corpus, not to the
result. So the ladder below holds the *shape* roughly constant (arithmetic over card-level
integer fields) and varies only how many cards match, from ~17k down to 2.

    .venv/bin/python scripts/bench_arith_selectivity.py \
        --corpus benchmarks/bitplanes/corpus.jsonl \
        --rev 50eba3e --out benchmarks/arith-tuple-postings/sweep-before.csv

Run the same script against two builds (before = 50eba3e, after = fe30740) and diff the CSVs.
`total` doubles as the cross-build parity check: it must be identical on every row, or the
comparison is void. Report `min_ms` (converted to µs in prose) — the mode of the distribution's
floor is what the narrowing changes; `avg_ms` carries the machine's background noise.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.bench_bitplanes import bench_one, load_engine  # noqa: E402

# (group, query, unique, orderby, prefer) — direction=asc, limit=100, offset=0 throughout.
CONFIGS: list[tuple[str, str, str, str, str]] = [
    # ── repro: the configs scripts/bench_arith_tuple_postings.py measured in the PR, so this
    # run can be checked against the published table rather than trusted alongside it.
    ("repro", "power+toughness<4", "card", "rarity", "default"),
    ("repro", "cmc+1<power", "printing", "edhrec", "default"),
    ("repro", "power+toughness<4", "card", "edhrec", "default"),
    ("repro", "cmc+1<power", "card", "edhrec", "default"),
    ("repro-neg", "-power+toughness<4", "card", "rarity", "default"),
    ("repro-neg", "-cmc+1<power", "printing", "edhrec", "default"),
    ("repro-other", "power<toughness", "card", "edhrec", "default"),
    ("repro-other", "loyalty>=4", "card", "edhrec", "default"),
    ("repro-compound", "power+toughness<4 t:creature", "card", "edhrec", "default"),
    ("repro-compound", "cmc+1<power c:g", "card", "edhrec", "default"),
    # ── sweep: same predicate family (arithmetic over card-level ints only), ordered from
    # broadest to most selective. `total` on the after build is the matched-card count.
    ("sweep", "power+toughness<20", "card", "edhrec", "default"),
    ("sweep", "cmc>=power", "card", "edhrec", "default"),
    ("sweep", "cmc+1<power+toughness", "card", "edhrec", "default"),
    ("sweep", "power<toughness", "card", "edhrec", "default"),
    ("sweep", "power+toughness<4", "card", "edhrec", "default"),
    ("sweep", "cmc+cmc+2<power+toughness", "card", "edhrec", "default"),
    ("sweep", "power+toughness>13", "card", "edhrec", "default"),
    ("sweep", "cmc*3+1<power+toughness", "card", "edhrec", "default"),
    ("sweep", "power-cmc>4", "card", "edhrec", "default"),
    ("sweep", "power*2>cmc+20", "card", "edhrec", "default"),
    ("sweep", "power+toughness>30", "card", "edhrec", "default"),
    # Degenerate-broad: matches every card, so the narrowed set exceeds narrow_candidates_exact's
    # >75%-of-domain cap and is discarded — the tuple scan runs and its result is thrown away, so
    # this row shows the cost of a narrowing that doesn't pay.
    ("sweep-cap", "cmc>=cmc", "card", "edhrec", "default"),
    # ── page-size confound check. Every row runs at limit=100, so the three most selective sweep
    # rows (36/7/2 matches) are also the only ones whose whole result fits in one page and skips
    # top-k selection. These walk the 100-match boundary densely: if the multiplier jumps there, the
    # tail is a page-size artifact; if it rises smoothly through it, it is selectivity.
    ("boundary", "power+toughness>13", "card", "edhrec", "default"),
    ("boundary", "power+toughness>16", "card", "edhrec", "default"),
    ("boundary", "power+toughness>18", "card", "edhrec", "default"),
    ("boundary", "power-cmc>3", "card", "edhrec", "default"),
    ("boundary", "power-cmc>5", "card", "edhrec", "default"),
    ("boundary", "power*2>cmc+16", "card", "edhrec", "default"),
    ("boundary", "power+toughness>30", "card", "edhrec", "default"),
    # ── size- and sort-matched bare single-column comparisons (dedicated index arm, tuple route
    # declines). Same orderby as the arith rows, unlike the two controls below, and result sizes
    # chosen to match arith rows in the sweep: cmc>12 has the same 7 matches as power*2>cmc+20.
    ("matched-bare", "cmc>12", "card", "edhrec", "default"),
    ("matched-bare", "toughness>13", "card", "edhrec", "default"),
    ("matched-bare", "power>12", "card", "edhrec", "default"),
    ("matched-bare", "power>4", "card", "edhrec", "default"),
    ("matched-bare", "cmc>6", "card", "edhrec", "default"),
    # ── controls that must stay flat across builds:
    #   bare single-field numerics keep their dedicated index arms (the tuple route declines),
    ("control", "power>4", "card", "power", "default"),
    ("control", "cmc>6", "card", "cmc", "default"),
    #   an arith expr mixing a printing-level field (usd) with a card-level one must decline
    #   entirely and stay on the full scan,
    ("control", "usd+1<power", "card", "edhrec", "default"),
    #   and a non-numeric leaf, as a canary for machine-level drift between the two runs.
    ("control", "t:creature", "card", "edhrec", "default"),
]


def main() -> None:
    """Load the corpus, time every config, and write the results CSV."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="CSV output path")
    parser.add_argument("--window", type=float, default=5.0, help="timed seconds per config")
    parser.add_argument("--rev", default=None, help="revision label for the rev column (default: git HEAD)")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None, help="engine archive path (default: alongside --out)")
    args = parser.parse_args()

    rev = args.rev
    if rev is None:
        rev = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    shm_path = args.shm_path or args.out.with_suffix(".store")
    engine = load_engine(args.corpus, shm_path)

    hdr = f"{'group':<15} {'query':<28} {'unique':<9} {'orderby':<8} {'total':>7} {'avg ms':>8} {'min ms':>8}"
    print(f"\nrev {rev}, window {args.window:.0f}s per config\n{hdr}\n{'-' * len(hdr)}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rev", "group", "query", "unique", "orderby", "prefer", "total", "n", "avg_ms", "min_ms"])
        for group, query, unique, orderby, prefer in CONFIGS:
            total, n, avg_ms, min_ms = bench_one(engine, (query, unique, orderby, prefer), args.window)
            writer.writerow([rev, group, query, unique, orderby, prefer, total, n, f"{avg_ms:.4f}", f"{min_ms:.4f}"])
            fh.flush()
            print(f"{group:<15} {query:<28} {unique:<9} {orderby:<8} {total:>7} {avg_ms:>8.3f} {min_ms:>8.3f}", flush=True)

    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
