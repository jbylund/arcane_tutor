#!/usr/bin/env python3
"""Measure decklist-style search query sizes from the card-name corpus.

Pulls distinct ``card_name`` values from blue Postgres (or a cached export),
builds simulated commander decklist queries in the shape:

    (!"Card One" OR !"Card Two" OR …) f:commander

and reports UTF-8 byte-length percentiles over many random 100-card decks.

Usage:
    python scripts/measure_decklist_query_budget.py
    python scripts/measure_decklist_query_budget.py --samples 100000 --seed 0
    python scripts/measure_decklist_query_budget.py --names /tmp/names.txt
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing.hand_parser import _is_word_cont, _is_word_start  # noqa: E402

DB_CONTAINER = "sylvan_blue-postgres-1"
DB_USER = "foouser"
DB_NAME = "magic"
DEFAULT_SAMPLES = 100_000
DEFAULT_DECK_SIZE = 100
DEFAULT_FORMAT = "commander"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "decklist_query_budget.json"
HISTOGRAM_COLUMN_COUNT = 3

HISTOGRAM_SQL = """
WITH distinct_card_names AS (
    SELECT card_name
    FROM magic.cards
    GROUP BY card_name
)
SELECT
    char_length(card_name) AS name_chars,
    octet_length(card_name) AS name_bytes,
    count(*) AS name_frequency
FROM distinct_card_names
GROUP BY 1, 2
ORDER BY 1, 2;
"""

NAMES_SQL = """
SELECT card_name
FROM magic.cards
GROUP BY card_name
ORDER BY card_name;
"""

FRAGMENT_STATS_SQL = """
WITH distinct_card_names AS (
    SELECT card_name FROM magic.cards GROUP BY card_name
),
encoded AS (
    SELECT
        card_name,
        octet_length(card_name) AS name_bytes,
        octet_length(
            CASE
                WHEN card_name ~ '^[^ \\t",()]+$' THEN '!' || card_name
                ELSE '!"' || replace(card_name, '"', E'\\\\"') || '"'
            END
        ) AS fragment_bytes
    FROM distinct_card_names
)
SELECT
    percentile_cont(ARRAY[0.5, 0.95, 0.99, 0.999])
        WITHIN GROUP (ORDER BY fragment_bytes) AS fragment_byte_percentiles,
    max(fragment_bytes) AS max_fragment_bytes,
    count(*) AS distinct_names
FROM encoded;
"""


def _is_single_word_token(name: str) -> bool:
    """True when ``!name`` lexes as one exact-name word token (hand_parser rules)."""
    if not name:
        return False
    if not _is_word_start(name[0]):
        return False
    return all(_is_word_cont(ch) for ch in name[1:])


def exact_name_fragment(name: str) -> str:
    """Return the Scryfall-style exact-name fragment for *name*."""
    if _is_single_word_token(name):
        return f"!{name}"
    return f'!"{name}"'


def decklist_query(names: list[str], *, fmt: str = DEFAULT_FORMAT) -> str:
    """Build a parenthesized OR chain with a trailing format filter."""
    body = " OR ".join(exact_name_fragment(name) for name in names)
    return f"({body}) f:{fmt}"


def _run_psql(sql: str, *, csv: bool = False) -> str:
    cmd = [
        "docker",
        "exec",
        "-i",
        DB_CONTAINER,
        "psql",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-v",
        "ON_ERROR_STOP=1",
        "-X",
    ]
    if csv:
        cmd.append("--csv")
    else:
        cmd.extend(["-At", "-F", "\t"])
    cmd.extend(["-c", sql])
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        msg = f"psql failed:\n{proc.stderr}"
        raise SystemExit(msg)
    return proc.stdout


def fetch_names_from_db() -> list[str]:
    """Load distinct card names from blue Postgres."""
    raw = _run_psql(NAMES_SQL)
    names = [line.strip() for line in raw.splitlines() if line.strip()]
    if not names:
        msg = "No card names returned from database."
        raise SystemExit(msg)
    return names


def load_names(path: Path) -> list[str]:
    """Load one card name per line from *path*."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def percentile(sorted_values: list[int], p: float) -> float:
    """Linear-interpolation percentile on a sorted list."""
    if not sorted_values:
        return 0.0
    if p <= 0:
        return float(sorted_values[0])
    if p >= 1:
        return float(sorted_values[-1])
    idx = (len(sorted_values) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    weight = idx - lo
    return sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight


def simulate_deck_queries(
    names: list[str],
    *,
    samples: int,
    deck_size: int,
    fmt: str,
    seed: int,
) -> list[int]:
    """Return UTF-8 byte lengths for *samples* random decklist queries."""
    if deck_size > len(names):
        msg = f"deck_size={deck_size} exceeds distinct name count={len(names)}"
        raise SystemExit(msg)
    rng = random.Random(seed)
    out: list[int] = []
    for _ in range(samples):
        deck = rng.sample(names, deck_size)
        query = decklist_query(deck, fmt=fmt)
        out.append(len(query.encode("utf-8")))
    return out


def parse_histogram(raw: str) -> list[dict[str, int]]:
    """Parse tab-separated histogram rows from psql output."""
    rows: list[dict[str, int]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != HISTOGRAM_COLUMN_COUNT:
            continue
        rows.append(
            {
                "name_chars": int(parts[0]),
                "name_bytes": int(parts[1]),
                "name_frequency": int(parts[2]),
            }
        )
    return rows


def summarize_bytes(values: list[int]) -> dict[str, float | int]:
    """Return count/min/max/mean/percentiles for UTF-8 byte lengths."""
    sorted_values = sorted(values)
    return {
        "count": len(sorted_values),
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "mean": statistics.fmean(sorted_values),
        "p50": percentile(sorted_values, 0.50),
        "p95": percentile(sorted_values, 0.95),
        "p99": percentile(sorted_values, 0.99),
        "p999": percentile(sorted_values, 0.999),
    }


def main() -> None:
    """Load card names, simulate decklist queries, and write summary JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", type=Path, help="Optional file of distinct card names (one per line).")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--deck-size", type=int, default=DEFAULT_DECK_SIZE)
    parser.add_argument("--format", default=DEFAULT_FORMAT)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--skip-db-stats", action="store_true", help="Skip histogram SQL against blue.")
    args = parser.parse_args()

    names = load_names(args.names) if args.names else fetch_names_from_db()
    print(f"Loaded {len(names):,} distinct card names")

    histogram: list[dict[str, int]] = []
    fragment_stats: dict[str, object] = {}
    if not args.skip_db_stats and args.names is None:
        print("Running name-length histogram query…")
        histogram = parse_histogram(_run_psql(HISTOGRAM_SQL))
        frag_raw = _run_psql(FRAGMENT_STATS_SQL).strip()
        if frag_raw:
            parts = frag_raw.split("\t")
            perc = [float(x.strip('"')) for x in parts[0].strip("{}").split(",")]
            fragment_stats = {
                "distinct_names": int(parts[2]),
                "fragment_bytes_p50": perc[0],
                "fragment_bytes_p95": perc[1],
                "fragment_bytes_p99": perc[2],
                "fragment_bytes_p999": perc[3],
                "fragment_bytes_max": int(parts[1]),
            }

    print(f"Simulating {args.samples:,} random {args.deck_size}-card decklist queries…")
    byte_lengths = simulate_deck_queries(
        names,
        samples=args.samples,
        deck_size=args.deck_size,
        fmt=args.format,
        seed=args.seed,
    )
    deck_stats = summarize_bytes(byte_lengths)

    quoted_names = sum(1 for name in names if not _is_single_word_token(name))
    result = {
        "source": str(args.names) if args.names else f"docker:{DB_CONTAINER}",
        "distinct_names": len(names),
        "quoted_fragment_names": quoted_names,
        "unquoted_fragment_names": len(names) - quoted_names,
        "simulation": {
            "samples": args.samples,
            "deck_size": args.deck_size,
            "format_filter": args.format,
            "seed": args.seed,
            "query_shape": '(!"…" OR …) f:{format}',
            "utf8_bytes": deck_stats,
        },
        "name_length_histogram": histogram,
        "exact_fragment_byte_percentiles_sql": fragment_stats,
        "adopted_limits": {
            "max_query_utf8_bytes": 3500,
            "max_group_depth": 10,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
