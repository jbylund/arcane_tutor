"""Time the needle-length cliff for text containment, per tier.

Originally the measurement behind #858; now a regression guard for what shipped and the standing
measurement of what did not.

NAME needles are fully tiered as of #862 / #863: 1 byte resolves exactly through `NameUnigramIndex`, 2 through
`NameBigramIndex`, 3 through a single trigram (all three TIGHT, so `card_pass` never runs), and 4+ narrow to a
trigram superset that the walk verifies. Every row below should read `narrowed_repr` of `cards` or `card_bits`.

ORACLE and FLAVOR are untouched and still show the original cliff -- `o:s` and `ft:s` cost ~1 ms with
`narrowed_repr: none`, because sub-trigram needles have no index there and lose memoization too
(`memoize_text_predicates` is gated on `trigram_candidates` returning `Some`). Keeping them here is the point:
`narrowed_repr` is what distinguishes a served tier from an unserved one at a glance.

The `Or` rows are what surfaced #860 -- `o:the or o:you` reported `none` where both halves narrow.

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
