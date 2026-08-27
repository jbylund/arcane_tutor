"""Scryfall's alternate spellings for color identity and the two tag families.

Scryfall accepts three spellings per tag family — `art`/`atag`/`arttag` and
`otag`/`oracletag`/`function` — each returning identical results (verified live:
196/196/196 and 6427/6427/6427), and `ci` as a color-identity alias
(`ci<=bg` == `id<=bg`). Only one spelling per family was recognized here, so a
client forwarding Scryfall-shaped query strings verbatim hit a parse error on
the rest. Each added spelling must resolve to the same field as its canonical
form, in both parsers.
"""

from functools import partial

import pytest

from api.parsing import generate_sql_query, parse_query, parse_scryfall_query
from api.parsing.pyparsing_based import parse_str_to_query as pyparsing_parse_str_to_query

parse_with_pyparsing = partial(parse_query, parser_fn=pyparsing_parse_str_to_query)

# (alias spelling, the already-supported spelling it must match)
TAG_ALIAS_CASES = [
    ("atag:squirrel", "art:squirrel"),
    ("arttag:squirrel", "art:squirrel"),
    ("oracletag:removal", "otag:removal"),
    ("function:removal", "otag:removal"),
]


@pytest.mark.parametrize(
    argnames=["alias_query", "canonical_query"],
    argvalues=TAG_ALIAS_CASES,
    ids=[q for q, _ in TAG_ALIAS_CASES],
)
def test_tag_aliases_match_canonical(alias_query: str, canonical_query: str) -> None:
    """Each Scryfall tag-alias spelling produces identical SQL to its canonical form, in both parsers."""
    assert generate_sql_query(parse_scryfall_query(alias_query)) == generate_sql_query(parse_scryfall_query(canonical_query))
    assert generate_sql_query(parse_with_pyparsing(alias_query)) == generate_sql_query(parse_with_pyparsing(canonical_query))


CI_CASES = [
    ("ci<=bg", "id<=bg"),
    ("ci:wu", "id:wu"),
    ("ci>=rg", "identity>=rg"),
    ("t:land ci<=bg", "t:land id<=bg"),
]


@pytest.mark.parametrize(
    argnames=["ci_query", "id_query"],
    argvalues=CI_CASES,
    ids=[q for q, _ in CI_CASES],
)
def test_ci_is_an_identity_alias(ci_query: str, id_query: str) -> None:
    """`ci` produces identical SQL to the established identity aliases, in both parsers."""
    assert generate_sql_query(parse_scryfall_query(ci_query)) == generate_sql_query(parse_scryfall_query(id_query))
    assert generate_sql_query(parse_with_pyparsing(ci_query)) == generate_sql_query(parse_with_pyparsing(id_query))
