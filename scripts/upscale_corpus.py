"""Build an upscaled store from the real corpus, so loop benchmarks can miss cache the way production does.

`bench_gather_loop` and `bench_streamed_loop` walk one candidate list `ITERS` times and keep the minimum,
which makes every rate they report a WARM-CACHE rate: from the second pass every card, printing and
string is resident. Production walks a candidate set once. Measured against the warm minimum, the first
pass costs 1.6-2.2x more, and that gap is the entire reason five refits of the shipped constants each
made routing worse -- each lowered rates toward a cache state that never occurs.

Two things fix that, and they compose:

  chunk rotation   walk a DIFFERENT slice of candidates each iteration, so consecutive walks share no
                   cards. Implemented in the harnesses themselves.
  a bigger store   with 31,508 oracle cards the whole archive is ~68 MB, so a full rotation still fits
                   in the system-level cache on this machine and chunk 0 may survive until it is walked
                   again. At 4-13x that, a rotation cannot stay resident.

The real corpus cannot supply 400k cards, so this replicates it. Each copy rewrites the three identity
fields that decide how the engine groups rows -- `oracle_id` (which printings form one oracle card),
`scryfall_id` (the printing key) and `illustration_id` (artwork groups) -- keeping UUID shape so nothing
downstream has to special-case them. Everything that drives cost stays untouched: printings per card,
the printings-per-card DISTRIBUTION, text lengths, legality masks, colours. So an upscaled store is the
real corpus's shape at N times the size, which is what a cache measurement needs; it is NOT a
realistic future corpus, and nothing about selectivity or query mix should be read off it.

Names get a per-copy suffix, because `card_name` feeds the name index and the sort permutations and
duplicate names across copies would collapse orderings that production keeps distinct.

    .venv/bin/python scripts/upscale_corpus.py --copies 4 --out benchmarks/loop-scale/cards-4x.store

Reports the realized card and printing counts, since those are what the harness needs to report
alongside its rates.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Rows per `add_batch`, matching costbench.BATCH_SIZE's reasoning: large enough that per-call overhead
# vanishes, small enough that the batch list stays cheap to hold.
BATCH_SIZE = 2000
# The identity fields a copy must rewrite. `oracle_id` decides which printings form one oracle card, so
# leaving it would fold every copy's printings into the SAME cards -- the store would grow in printings
# per card rather than in cards, which is the opposite of what this is for. `illustration_id` decides
# artwork groups, and `scryfall_id` is the printing primary key.
UUID_FIELDS = ("oracle_id", "scryfall_id", "illustration_id")
# Hex characters in a UUID's leading block, which is the part `rewrite_uuid` stamps the copy index over.
UUID_FIRST_BLOCK = 8


def rewrite_uuid(value: str, copy: int) -> str:
    """Replace a UUID's first block with the copy index, preserving shape and uniqueness.

    The originals are unique within one copy, so stamping the copy index over the leading block keeps
    them unique across copies without needing a counter or a hash. Values that are not UUID-shaped
    (absent, null, or some other spelling) are returned unchanged rather than corrupted.
    """
    if not isinstance(value, str) or len(value) < UUID_FIRST_BLOCK or "-" not in value:
        return value
    return f"{copy:08x}{value[UUID_FIRST_BLOCK:]}"


def replicate(record: dict, copy: int) -> dict:
    """One copy of a record, with identities rewritten and everything cost-relevant left alone."""
    out = dict(record)
    for field in UUID_FIELDS:
        if field in out:
            out[field] = rewrite_uuid(out[field], copy)
    # Names feed the name index and both sort permutations; duplicates across copies would collapse
    # orderings production keeps distinct, and the permutation walk is exactly what P3 benchmarks.
    if isinstance(out.get("card_name"), str):
        out["card_name"] = f"{out['card_name']} c{copy}"
    if isinstance(out.get("card_name_folded"), str):
        out["card_name_folded"] = f"{out['card_name_folded']} c{copy}"
    return out


def main() -> None:
    """Build an upscaled store by replicating the corpus, then report what it came out as."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--copies", type=int, required=True, help="replication factor; 1 reproduces the real corpus")
    ap.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks" / "bitplanes" / "corpus.jsonl")
    ap.add_argument("--out", type=pathlib.Path, required=True, help="store path to write")
    args = ap.parse_args()

    if args.copies < 1:
        msg = "--copies must be at least 1"
        raise SystemExit(msg)

    import card_engine  # noqa: PLC0415 - keeps this importable without the built extension

    args.out.parent.mkdir(parents=True, exist_ok=True)
    engine = card_engine.QueryEngine(str(args.out))
    if not engine.reload_begin():
        msg = "reload_begin returned False (stale archive published concurrently?)"
        raise SystemExit(msg)

    # Streamed per copy rather than building the whole list: at 13 copies this is ~1.26M records, and
    # holding them all would cost more than the store itself.
    records = [json.loads(line) for line in args.corpus.open()]
    print(f"read {len(records):,} printings from {args.corpus}", flush=True)
    batch: list[dict] = []
    for copy in range(args.copies):
        for record in records:
            batch.append(replicate(record, copy))
            if len(batch) == BATCH_SIZE:
                engine.add_batch(batch)
                batch.clear()
        print(f"  copy {copy + 1}/{args.copies} staged", flush=True)
    if batch:
        engine.add_batch(batch)
    engine.reload_commit()

    size_mb = args.out.stat().st_size / (1024 * 1024)
    print(f"\nwrote {args.out}  ({size_mb:,.0f} MB, {engine.size():,} printings)")
    print("card count comes from the harness, which reports it alongside its rates.")


if __name__ == "__main__":
    main()
