"""Time the needle-length cliff for text containment -- the measurement behind #858.

Three tiers: trigram narrowing needs 3 bytes (`trigram_candidates` returns None below that), 2-byte needles
resolve exactly through `NameBigramIndex`, and 1-byte needles have neither. The third case also loses
memoization, because `memoize_text_predicates` is gated on `trigram_candidates` returning `Some`, so the
filter stays a `TextContains` evaluated per card inside the match loop.

Reports routed time beside the acquire facts, because `narrowed_repr: none` is what distinguishes the broken
tier from the working ones.

    .venv/bin/python scripts/bench_short_needle_cliff.py --shm /tmp/needle.store
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from api.parsing import parse_scryfall_query  # noqa: E402
from scripts import costbench  # noqa: E402

# One char (no index), two chars (bigram, exact), three+ (trigram). Plus a couple of controls.
QUERIES = [
    "name:s",
    "name:e",
    "name:a",
    "name:so",
    "name:sol",
    "name:solr",
    "o:s",
    "o:the",
    "ft:s",
    "o:the or o:you",
    "o:the or o:zap",
    "o:tar or o:qua",
]


def main() -> None:
    """Time each needle length against a store built from the corpus."""
    ap = argparse.ArgumentParser(description="Time the needle-length cliff for text containment (#858).")
    ap.add_argument("--corpus", type=pathlib.Path, default=REPO / "benchmarks/bitplanes/corpus.jsonl")
    ap.add_argument("--shm", type=pathlib.Path, required=True)
    args = ap.parse_args()
    engine = costbench.load_engine(args.corpus, args.shm)
    print(f"\n{'query':<12}{'routed':>10}{'results':>10}{'plan':>16}{'count_src':>18}{'narrowed':>12}")
    for q in QUERIES:
        kw = {"filters": parse_scryfall_query(q), "unique": "card", "orderby": "name", "direction": "asc", "limit": 60, "offset": 0}
        acq = engine.explain(**kw)["acquire"]
        res = engine.explain_analyze(num_warmups=3, num_trials=15, **kw)
        routed = min(res["acquire"]["routed_ns"]) / 1000.0
        picked = next((p["plan"] for p in res["plans"] if p["picked"]), "?")
        total = next((p["result_total"] for p in res["plans"] if p["picked"]), 0)
        print(f"{q:<12}{routed:>9.1f}u{total:>10,}{picked:>16}{acq['count_source']:>18}{acq['narrowed_repr']:>12}")


if __name__ == "__main__":
    main()
