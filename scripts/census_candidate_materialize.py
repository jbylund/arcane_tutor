"""Census: which real queries would a change to candidate materialization actually touch?

Step 1 of docs/issues/local-engine-candidate-materialize.md. Six engine sites turn selected
index rows into a sorted candidate list via `collect` + `sort_unstable`; replacing that with
a bitmap scatter pays off from roughly `n_cards / 460` candidates up. This asks how many
queries land in that band before any engine code changes.

Uses `explain`, not `explain_analyze`: one acquire per query, so 14k queries is seconds, and
`acquire.count_source` + `acquire.eval_domain` is all the filtering needs. Feed the shortlist
this prints to `explain_analyze` for real timings.

    .venv/bin/python scripts/census_candidate_materialize.py --wild
    .venv/bin/python scripts/census_candidate_materialize.py --random 5000 --out /tmp/shortlist.csv

**Reads card-space counts only.** `eval_domain` is candidate CARDS the walk iterates, so for
the three printing-space sites (`range_narrowed`, `expand_artist_ids`, `expand_flavor_ids`)
this sees the post-projection card count, not the printing count those sites sort. Their band
is wider (crossover ~194 against ~81), so this census under-counts them.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import random
import re
import sys
from typing import TYPE_CHECKING

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from client.query_sampler import ENGINE_ORDERBYS, QuerySampler  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402

if TYPE_CHECKING:
    import card_engine

# Crossover ratio from bench_candidate_materialize's axis G: the bitmap wins once
# `count * 460 > domain`, flat to +/-13% across a 95x range of domain. Divided into the live
# corpus size rather than hardcoded as a count, so this tracks the corpus instead of drifting.
CROSSOVER_RATIO = 460
# Mirror of the engine's BITS_PROMOTE (card_engine/src/lib.rs). At or above it a narrowing
# already returns a bitmap, so those queries are unaffected by the production-side change.
BITS_PROMOTE = 4_096
# The wild corpus records Scryfall's `unique` spellings; the engine's `mode_from_unique`
# falls through to Card for anything it does not recognize, so an unmapped value would be
# silently mis-measured rather than rejected. Map explicitly.
UNIQUE_FROM_SCRYFALL = {
    "card": "card",
    "cards": "card",
    "art": "artwork",
    "artwork": "artwork",
    "prints": "printing",
    "printing": "printing",
}
# A corpus row carrying an orderby the engine has no sort column for falls back to the default,
# so it cannot quietly change which plan is measured while still being labelled as itself.
DEFAULT_ORDERBY = "edhrec"
# Overridable so the harness runs from a git worktree, which has no benchmarks/ tree of its own.
WILD_CORPUS = REPO_ROOT / "benchmarks/wild-queries/wild-corpus.jsonl"
# Same rule build_wild_corpus.py uses to split its own census, so the partition here is
# comparable to the counts in benchmarks/wild-queries/README.md (3,566 with operators against
# 10,907 bare name lookups). The corpus is 75% name lookups — largely bot/tooling deep links —
# which narrow to a single card by construction, so aggregating the two halves measures
# mostly bots and says nothing about search.
OP_RE = re.compile(r"[a-z]+[:<>=]", re.I)


# Representations that mean a sorted vec was built, so a `collect` + `sort_unstable` ran. The
# bits-shaped and `none` labels mean the narrowing stayed word-wise or came from the plane
# bitmap, and no sort happened — those queries are in the band but cannot be affected.
SORTED_REPRS = frozenset({"cards", "printings"})


def band_of(eval_domain: int, n_cards: int, crossover: int, narrowed_repr: str) -> str:
    """Which materialization band a query falls in, counting only queries that actually sort."""
    if eval_domain >= n_cards:
        return "unnarrowed"
    if eval_domain >= BITS_PROMOTE:
        return "already_bitmap"
    if eval_domain >= crossover:
        # In the band by count, but the change can only touch it if a sort produced the list.
        return "AFFECTED" if narrowed_repr in SORTED_REPRS else f"band_no_sort_{narrowed_repr}"
    return "below_crossover"


def wild_queries(path: pathlib.Path, *, with_operators: bool) -> list[tuple[str, str, str, int]]:
    """(query, unique, orderby, weight) from the Common Crawl wild-query corpus.

    `with_operators` selects one half of the partition: operator-bearing queries (the actual
    search workload) or bare name lookups (bot deep links). Never both — see OP_RE.
    """
    out = []
    for line in path.open():
        row = json.loads(line)
        unique = UNIQUE_FROM_SCRYFALL.get(row.get("unique", "card"))
        if unique is None or bool(OP_RE.search(row["q"])) != with_operators:
            continue
        order = row.get("order", DEFAULT_ORDERBY)
        out.append((row["q"], unique, order if order in ENGINE_ORDERBYS else DEFAULT_ORDERBY, int(row.get("weight", 1))))
    return out


def random_queries(n: int, seed: int, corpus: pathlib.Path) -> list[tuple[str, str, str, int]]:
    """(query, unique, orderby, weight=1) from the query sampler, for shape coverage."""
    rng = random.Random(seed)
    sampler = QuerySampler(corpus, "realistic")
    return [(sampler.query(rng), sampler.unique(rng), DEFAULT_ORDERBY, 1) for _ in range(n)]


def census_one(
    engine: card_engine.QueryEngine, name: str, queries: list[tuple[str, str, str, int]], limit: int
) -> list[tuple[str, str, str, str, int, int]]:
    """Explain every query, print the band distribution, and return the affected-band rows."""
    # Counted both weighted by occurrence (production share) and unweighted (distinct-query
    # share) — a rare-but-heavy query and a common-but-light one are different findings.
    bands: collections.Counter[str] = collections.Counter()
    weighted: collections.Counter[str] = collections.Counter()
    by_source: collections.Counter[str] = collections.Counter()
    affected: list[tuple[str, str, str, str, int, int]] = []
    rejected = 0
    n_cards = 0

    for q, unique, orderby, weight in queries:
        try:
            filters = parse_scryfall_query(q)
            res = engine.explain(filters=filters, unique=unique, orderby=orderby, direction="asc", limit=limit, offset=0)
        except Exception:  # noqa: BLE001 - a query the parser or bind rejects is a census skip, not an error
            rejected += 1
            continue
        acq = res["acquire"]
        n_cards = acq["n_cards"]
        crossover = max(1, n_cards // CROSSOVER_RATIO)
        by_source[acq["count_source"]] += weight
        band = (
            band_of(acq["eval_domain"], n_cards, crossover, acq["narrowed_repr"])
            if acq["count_source"] == "candidates"
            else f"prep_{acq['count_source']}"
        )
        bands[band] += 1
        weighted[band] += weight
        if band == "AFFECTED":
            affected.append((name, q, unique, orderby, acq["eval_domain"], weight))

    crossover = max(1, n_cards // CROSSOVER_RATIO)
    total, total_w = sum(bands.values()) or 1, sum(weighted.values()) or 1
    print(f"\n{name}: {sum(bands.values()):,} queries explained ({rejected:,} rejected), corpus {n_cards:,} cards")
    print(f"  affected band: {crossover:,} <= eval_domain < {BITS_PROMOTE:,} (crossover = n_cards / {CROSSOVER_RATIO})")
    print(f"  {'band':<18}{'queries':>10}{'share':>9}{'weighted':>12}{'share':>9}")
    for band, n in sorted(bands.items(), key=lambda kv: -kv[1]):
        w = weighted[band]
        print(f"  {band:<18}{n:>10,}{n / total:>8.1%}{w:>12,}{w / total_w:>8.1%}")
    print(f"  count_source: {', '.join(f'{k}={v:,}' for k, v in by_source.most_common())}")
    return affected


def main() -> None:
    """Run the census over the requested corpora and print the band distribution."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--corpus",
        type=pathlib.Path,
        default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl",
        help="card rows to build the store from",
    )
    parser.add_argument("--shm-path", type=pathlib.Path, default=None, help="engine archive path (default: alongside --corpus)")
    parser.add_argument("--wild-corpus", type=pathlib.Path, default=WILD_CORPUS, help="real-traffic corpus read by --wild")
    parser.add_argument("--wild", action="store_true", help="census the Common Crawl wild-query corpus")
    parser.add_argument("--random", type=int, default=0, metavar="N", help="also census N generated queries")
    parser.add_argument("--seed", type=int, default=0, help="seed for --random")
    parser.add_argument("--out", type=pathlib.Path, default=None, help="CSV shortlist of affected-band queries")
    parser.add_argument("--limit", type=int, default=100, help="page size to explain at")
    args = parser.parse_args()

    if not args.wild and not args.random:
        parser.error("nothing to census: pass --wild and/or --random N")

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".census.store"))

    sources: list[tuple[str, list[tuple[str, str, str, int]]]] = []
    if args.wild:
        corpus = args.wild_corpus
        sources.append(("wild-operators", wild_queries(corpus, with_operators=True)))
        sources.append(("wild-namelookup", wild_queries(corpus, with_operators=False)))
    if args.random:
        sources.append(("random", random_queries(args.random, args.seed, args.corpus)))

    affected: list[tuple[str, str, str, str, int, int]] = []
    for name, queries in sources:
        affected += census_one(engine, name, queries, args.limit)

    if args.out and affected:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["source", "query", "unique", "orderby", "eval_domain", "weight"])
            writer.writerows(sorted(affected, key=lambda r: -r[4]))
        print(f"\nwrote {len(affected):,} affected-band queries to {args.out}")


if __name__ == "__main__":
    main()
