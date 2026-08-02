"""One query universe for every cost-model benchmark, with two weightings over it.

The generator in `client/query_runner.py` picks values from hardcoded lists — `year:2019` through
`year:2024` on a corpus spanning 1993-2026, a dozen fixed price points, `pow=0..6`. That is fine for
load generation, where the point is plausible traffic. It is wrong for cost-model work, because it
clusters *selectivity* at a handful of arbitrary points and leaves most of the range unexercised. A
cost model is a function of selectivity; a benchmark that samples six values of it cannot say
whether the model is right.

Everything here is drawn from the corpus — real card names, types and subtypes, artists, set codes,
oracle and flavor vocabulary, and real values for every range column — so the universe is as large
as the data. `t:` alone spans 437 distinct values from `Creature` (45,976 printings) down to
one-offs, which is a far better selectivity ladder than any hand-written list.

## Two modes over the same universe

- **`realistic`** weights toward traffic we expect. This applies at BOTH levels: which family a
  predicate comes from (name/oracle/type/numeric over flavor text) and which value within it
  (`t:creature` far more often than `t:vronos`, by corpus frequency).
- **`uniform`** weights families evenly and values flat over the distinct vocabulary. Use it to
  explore the space and catch regressions — `artwork` is 5% of realistic traffic but was where a 12x
  routing regression hid, precisely because nothing sampled it hard enough to notice.

They differ ONLY in weights, never in what can be produced, so a finding under one is reproducible
under the other with enough samples.

## Sampling is uniform in QUANTILE, not in value

Drawing a price uniformly from [0.05, 400] produces almost nothing but "matches nearly everything",
because real prices are heavily skewed. Drawing the p-th percentile for uniform p spreads
*selectivity* evenly across (0, 1), which is the axis the cost model varies along.

The bounded-range shape (`usd>=a usd<=b`) matters more than it looks: it is absent from every older
generator, and the first paired A/B run with it found a 12x routing regression that those generators
could not see, concentrated entirely in bounded ranges at artwork granularity.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    import random

MODES = ("realistic", "uniform")

# Range fields mapped to the corpus column each draws its thresholds from.
RANGE_COLUMNS: dict[str, str] = {
    "usd": "price_usd",
    "cn": "collector_number_int",
    "year": "released_at",
    "date": "released_at",
}
RANGE_OPS = ("<", "<=", ">", ">=", ":")

# Predicate families and their relative weight in `realistic`; `uniform` uses all-ones.
REALISTIC_FAMILY_WEIGHTS: dict[str, float] = {
    "name": 20,
    "oracle": 18,
    "type": 14,  # t:, over merged types + subtypes
    "color": 12,
    "numeric": 12,  # pow / tou / cmc / loyalty — the arithmetic path
    "legality": 8,
    "range": 6,  # usd / cn / year / date, one-sided
    "set": 5,
    "rarity": 4,
    "collection": 3,
    "bounded": 2,  # two-sided range
    "artist": 1.5,
    "flavor": 0.5,
}
REALISTIC_UNIQUE_WEIGHTS: dict[str, float] = {"card": 75, "printing": 20, "artwork": 5}
REALISTIC_ORDERBY_WEIGHTS: dict[str, float] = {
    "edhrec": 40,
    "name": 15,
    "cmc": 10,
    "usd": 10,
    "rarity": 8,
    "power": 6,
    "toughness": 5,
    "cubecobra": 6,
}
# How many predicates a query gets. Deeper conjunctions narrow to nothing and stop exercising plan
# choice at all, which is the thing being measured.
PREDICATE_COUNT_WEIGHTS = ((1, 45), (2, 40), (3, 15))

# Closed vocabularies: small fixed sets where the corpus adds nothing.
STATIC_VALUES: dict[str, list[str]] = {
    "color": ["c:w", "c:u", "c:b", "c:r", "c:g", "c:wu", "c:br", "c:rg", "id:g", "id:wu", "id:brg", "c>=2"],
    "legality": ["f:modern", "f:commander", "f:legacy", "f:pauper", "f:standard", "f:vintage", "f:pioneer"],
    "numeric": [
        "pow>2",
        "pow=3",
        "pow<2",
        "pow>=5",
        "tou<4",
        "tou=2",
        "tou>=4",
        "cmc>=4",
        "cmc=2",
        "cmc<3",
        "loyalty>=3",
        "power+toughness<6",
        "cmc+1<pow",
    ],
    "rarity": ["r:common", "r:uncommon", "r:rare", "r:mythic", "r>=rare"],
    "collection": ["is:reprint", "border:black", "border:borderless", "frame:showcase", "watermark:set", "is:permanent"],
}

# A word must be this long and appear in this many rows to be worth querying: rarer words match
# nothing and only produce degenerate empty results.
MIN_WORD_LEN = 4
MIN_WORD_ROWS = 30
MAX_VOCAB = 4000
# Name predicates take a word from a real card name; this often is shortened to a prefix of this
# length, which is what makes broad `name:` searches (`name:bo`) appear alongside selective ones.
NAME_PREFIX_LEN = (2, 6)
NAME_PREFIX_FRACTION = 0.5
WORD_RE = re.compile(r"[a-z]{4,}")


class QuerySampler:
    """The corpus-derived query universe, sampled under one of the two `MODES`."""

    def __init__(self, corpus: pathlib.Path, mode: str = "uniform") -> None:
        """Read the corpus once, building the value universe and this mode's weight tables."""
        if mode not in MODES:
            msg = f"mode must be one of {MODES}, got {mode!r}"
            raise ValueError(msg)
        self.mode = mode
        self.realistic = mode == "realistic"
        self._read_corpus(corpus)
        self.families = self._weights(REALISTIC_FAMILY_WEIGHTS)
        self.uniques = self._weights(REALISTIC_UNIQUE_WEIGHTS)
        self.orderbys = self._weights(REALISTIC_ORDERBY_WEIGHTS)

    def _weights(self, realistic_table: dict[str, float]) -> tuple[list[str], list[float]]:
        """Keys with their weights — the realistic table, or all-ones for uniform."""
        keys = list(realistic_table)
        return keys, ([realistic_table[k] for k in keys] if self.realistic else [1.0] * len(keys))

    def _vocab(self, counts: collections.Counter[str], *, floor: int = 1, cap: int | None = None) -> tuple[list[str], list[float]]:
        """A corpus vocabulary as (values, weights).

        Realistic mode weights by corpus frequency, so `t:creature` dominates `t:vronos` the way
        real traffic does. Uniform mode goes flat over the distinct values, which is what makes it
        reach the rare tail — and the rare tail is where selectivity extremes, and the plans that
        only appear at them, actually live.
        """
        items = [(w, n) for w, n in (counts.most_common(cap) if cap else counts.most_common()) if n >= floor]
        if not items:
            return ["the"], [1.0]
        values = [w for w, _ in items]
        return values, ([float(n) for _, n in items] if self.realistic else [1.0] * len(items))

    def _read_corpus(self, corpus: pathlib.Path) -> None:
        """One pass: sorted values per range column, plus every corpus-derived vocabulary."""
        cols: dict[str, list[float]] = {c: [] for c in set(RANGE_COLUMNS.values())}
        names: collections.Counter[str] = collections.Counter()
        artists: collections.Counter[str] = collections.Counter()
        sets_: collections.Counter[str] = collections.Counter()
        # Types and subtypes share the `t:` operator (`t:creature` and `t:human` are both valid), so
        # they are one vocabulary, not two.
        types: collections.Counter[str] = collections.Counter()
        oracle: collections.Counter[str] = collections.Counter()
        flavor: collections.Counter[str] = collections.Counter()
        with corpus.open() as handle:
            for line in handle:
                row = json.loads(line)
                for col, out in cols.items():
                    value = row.get(col)
                    if value is None:
                        continue
                    out.append(dt.date.fromisoformat(value[:10]).toordinal() if col == "released_at" else float(value))
                if name := row.get("card_name"):
                    names[name.lower()] += 1
                if artist := row.get("card_artist"):
                    artists[artist.lower().split()[-1]] += 1
                if code := row.get("card_set_code"):
                    sets_[code.lower()] += 1
                for kind in ("card_types", "card_subtypes"):
                    for value in row.get(kind) or []:
                        types[value.lower()] += 1
                # Counter over the SET of words per row, so a word repeated within one card counts
                # once and MIN_WORD_ROWS means "appears in N rows", not "appears N times".
                oracle.update(set(WORD_RE.findall((row.get("oracle_text") or "").lower())))
                flavor.update(set(WORD_RE.findall((row.get("flavor_text") or "").lower())))

        self.sorted: dict[str, list[float]] = {c: sorted(v) for c, v in cols.items() if v}
        self.vocab: dict[str, tuple[list[str], list[float]]] = {
            "name": self._vocab(names),
            "artist": self._vocab(artists),
            "set": self._vocab(sets_),
            "type": self._vocab(types),
            "oracle": self._vocab(oracle, floor=MIN_WORD_ROWS, cap=MAX_VOCAB),
            "flavor": self._vocab(flavor, floor=MIN_WORD_ROWS, cap=MAX_VOCAB),
        }

    def _pick(self, family: str, rng: random.Random) -> str:
        """One value from a corpus vocabulary, weighted by mode."""
        values, weights = self.vocab[family]
        return rng.choices(values, weights=weights)[0]

    def quantile(self, column: str, p: float) -> float:
        """The value at quantile `p`, so a threshold placed here splits the column p / (1-p)."""
        values = self.sorted[column]
        return values[min(int(p * len(values)), len(values) - 1)]

    def _render(self, field: str, raw: float) -> str:
        """A sampled column value back into query syntax for `field`."""
        if field == "usd":
            return f"{raw:.2f}"
        if field == "cn":
            return str(int(raw))
        if field == "year":
            return str(dt.date.fromordinal(int(raw)).year)
        return dt.date.fromordinal(int(raw)).isoformat()

    def range_predicate(self, rng: random.Random) -> str:
        """One-sided range whose threshold sits at a uniformly-drawn quantile of its column."""
        field = rng.choice(list(RANGE_COLUMNS))
        value = self._render(field, self.quantile(RANGE_COLUMNS[field], rng.random()))
        return f"{field}{rng.choice(RANGE_OPS)}{value}"

    def bounded_predicate(self, rng: random.Random) -> str:
        """A two-sided range (`usd>=a usd<=b`), the shape one-sided sampling never produces."""
        field = rng.choice([f for f in RANGE_COLUMNS if f != "year"])
        column = RANGE_COLUMNS[field]
        lo_p, hi_p = sorted((rng.random(), rng.random()))
        lo = self._render(field, self.quantile(column, lo_p))
        hi = self._render(field, self.quantile(column, hi_p))
        return f"{field}>={lo} {field}<={hi}"

    def predicate(self, family: str, rng: random.Random) -> str:
        """One predicate from `family`, drawn from the corpus universe where there is one."""
        if family in STATIC_VALUES:
            return rng.choice(STATIC_VALUES[family])
        if family == "range":
            return self.range_predicate(rng)
        if family == "bounded":
            return self.bounded_predicate(rng)
        if family == "name":
            # `name:` is a SUBSTRING match, and people search the distinctive word — "bolt", not
            # "ligh". Taking only a leading prefix of the full name would never produce `name:bolt`
            # for "Lightning Bolt", so pick a word from anywhere in the name, then sometimes shorten
            # it to a prefix. Full words are selective, short prefixes are broad; both are real.
            words = [w for w in re.split(r"[^a-z0-9]+", self._pick("name", rng)) if w] or ["a"]
            word = rng.choice(words)
            if rng.random() < NAME_PREFIX_FRACTION:
                word = word[: rng.randint(*NAME_PREFIX_LEN)]
            return f"name:{word}"
        prefix = {"oracle": "o", "flavor": "ft", "artist": "a", "set": "set", "type": "t"}[family]
        return f"{prefix}:{self._pick(family, rng)}"

    def query(self, rng: random.Random) -> str:
        """One query: a few predicates from distinct families, weighted by this sampler's mode."""
        counts, count_weights = zip(*PREDICATE_COUNT_WEIGHTS, strict=True)
        keys, weights = self.families
        parts, used = [], set()
        for _ in range(rng.choices(counts, weights=count_weights)[0]):
            family = rng.choices(keys, weights=weights)[0]
            if family in used:
                continue
            used.add(family)
            parts.append(self.predicate(family, rng))
        return " ".join(parts) or self.predicate("type", rng)

    def unique(self, rng: random.Random) -> str:
        """A distinct-on, weighted by mode."""
        keys, weights = self.uniques
        return rng.choices(keys, weights=weights)[0]

    def orderby(self, rng: random.Random) -> str:
        """An orderby, weighted by mode. Which one gates StreamedSelect/PlanePopcountOrder."""
        keys, weights = self.orderbys
        return rng.choices(keys, weights=weights)[0]
