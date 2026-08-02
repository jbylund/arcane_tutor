"""`Shape` narrows what the sampler draws without changing where values come from.

Runs against a tiny synthetic corpus rather than the 239MB benchmark one, so these stay unit tests.
"""

from __future__ import annotations

import json
import random
import re
from typing import TYPE_CHECKING

import pytest

from scripts.query_sampler import ANY_SHAPE, MODES, QuerySampler, Shape

if TYPE_CHECKING:
    import pathlib

# Enough rows that every corpus-derived vocabulary is non-empty and the range columns have spread.
CORPUS_ROWS = 60
# Draws per assertion. Large enough that a family excluded by a shape would almost surely appear if
# the restriction were not applied, small enough to stay fast.
DRAWS = 300


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A small synthetic corpus with every column the sampler reads."""
    path = tmp_path_factory.mktemp("sampler") / "corpus.jsonl"
    with path.open("w") as handle:
        for i in range(CORPUS_ROWS):
            handle.write(
                json.dumps(
                    {
                        "card_name": f"Test Card {i}",
                        "card_artist": f"Alice Painter{i}",
                        "card_set_code": f"s{i % 7}",
                        "card_types": ["Creature"],
                        "card_subtypes": ["Human", "Wizard"][: 1 + i % 2],
                        "oracle_text": f"draw cards target creature gains flying number{i}",
                        "flavor_text": f"words about wizards and dragons here {i}",
                        "price_usd": round(0.05 + i * 3.1, 2),
                        "collector_number_int": i + 1,
                        "released_at": f"{1995 + i % 30}-06-15",
                    }
                )
                + "\n"
            )
    return path


@pytest.fixture(scope="module")
def sampler(corpus: pathlib.Path) -> QuerySampler:
    return QuerySampler(corpus, "uniform")


def test_unshaped_draw_reaches_many_families(sampler: QuerySampler) -> None:
    """Baseline: without a shape the pool is wide, so the restriction tests below mean something."""
    seen = {sampler.query(random.Random(i), ANY_SHAPE) for i in range(DRAWS)}
    assert len(seen) > DRAWS // 4


def test_families_restriction_excludes_everything_else(sampler: QuerySampler) -> None:
    """A range-only shape yields only range predicates, never a type or oracle one."""
    rng = random.Random(0)
    for _ in range(DRAWS):
        q = sampler.query(rng, Shape(families=frozenset({"range"}), predicates=1))
        assert re.fullmatch(r"(usd|cn|year|date)(<=|>=|<|>|:)\S+", q), q


def test_predicate_count_is_pinned(sampler: QuerySampler) -> None:
    """`predicates=1` means exactly one, which is what "bare range" plans require."""
    rng = random.Random(1)
    for _ in range(DRAWS):
        q = sampler.query(rng, Shape(families=frozenset({"range"}), predicates=1))
        assert " " not in q, q


def test_pinned_count_larger_than_pool_yields_the_pool(sampler: QuerySampler) -> None:
    """Families are drawn without replacement, so a count above the pool size cannot be met."""
    rng = random.Random(2)
    shape = Shape(families=frozenset({"range", "bounded"}), predicates=5)
    for _ in range(50):
        q = sampler.query(rng, shape)
        # `bounded` renders as two clauses (`usd>=a usd<=b`), so count fields, not tokens.
        assert len(re.findall(r"(?:usd|cn|year|date)(?:<=|>=|<|>|:)", q)) <= 4, q


def test_unique_and_orderby_are_restricted(sampler: QuerySampler) -> None:
    rng = random.Random(3)
    shape = Shape(unique=frozenset({"printing"}), orderby=frozenset({"usd", "cmc"}))
    for _ in range(DRAWS):
        assert sampler.unique(rng, shape) == "printing"
        assert sampler.orderby(rng, shape) in {"usd", "cmc"}


def test_shape_preserves_relative_weights_within_the_pool(corpus: pathlib.Path) -> None:
    """Restricting must not flatten the mode's weights over what survives.

    `realistic` weights name (20) far above flavor (0.5); a shape allowing only those two should
    still show that ratio rather than a coin flip.
    """
    realistic = QuerySampler(corpus, "realistic")
    rng = random.Random(4)
    shape = Shape(families=frozenset({"name", "flavor"}), predicates=1)
    names = sum(sampler_q.startswith("name:") for sampler_q in (realistic.query(rng, shape) for _ in range(DRAWS)))
    assert names > DRAWS * 0.8, f"expected name to dominate flavor 40:1, got {names}/{DRAWS}"


def test_default_shape_matches_the_old_unshaped_behaviour(sampler: QuerySampler) -> None:
    """The default argument must reproduce the pre-Shape stream exactly, seed for seed."""
    assert [sampler.query(random.Random(i)) for i in range(50)] == [sampler.query(random.Random(i), ANY_SHAPE) for i in range(50)]


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"families": frozenset({"nope"})}, "families"),
        ({"unique": frozenset({"cards"})}, "unique"),
        ({"orderby": frozenset({"released"})}, "orderby"),
        ({"predicates": 0}, "predicates"),
    ],
)
def test_impossible_shapes_are_rejected_at_construction(kwargs: dict, needle: str) -> None:
    """A typo should fail where it is written, not loop or silently sample the wrong thing."""
    with pytest.raises(ValueError, match=needle):
        Shape(**kwargs)


def test_every_mode_accepts_a_shape(corpus: pathlib.Path) -> None:
    for mode in MODES:
        s = QuerySampler(corpus, mode)
        assert s.query(random.Random(0), Shape(families=frozenset({"type"}), predicates=1)).startswith("t:")
