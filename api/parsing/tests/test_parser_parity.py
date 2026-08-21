"""Tests that both parser implementations produce identical SQL for the same queries."""

import pytest

from api.parsing import generate_sql_query, parse_scryfall_query
from api.parsing.pyparsing_based import parse_search_query
from api.parsing.tests.implicit_and_cases import TESTCASES


def assert_parsers_agree(query: str) -> None:
    """Assert both parsers accept or reject *query* alike, and emit identical SQL when they accept."""
    hand_exc: Exception | None = None
    pyp_exc: Exception | None = None
    hand_result: tuple | None = None
    pyp_result: tuple | None = None

    try:
        hand_result = generate_sql_query(parse_scryfall_query(query))
    except Exception as exc:  # noqa: BLE001
        hand_exc = exc

    try:
        pyp_result = generate_sql_query(parse_search_query(query))
    except Exception as exc:  # noqa: BLE001
        pyp_exc = exc

    assert (hand_exc is None) == (pyp_exc is None), (
        f"Parsers disagree on validity of {query!r}: "
        f"hand_rolled={'ok' if hand_exc is None else hand_exc!r}, "
        f"pyparsing={'ok' if pyp_exc is None else pyp_exc!r}"
    )
    if hand_result is not None:
        assert hand_result == pyp_result, (
            f"Parsers produce different SQL for {query!r}:\n  hand_rolled: {hand_result}\n  pyparsing:   {pyp_result}"
        )


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[[c["query"]] for c in TESTCASES if c["query"].strip()],
    ids=[c["id"] for c in TESTCASES if c["query"].strip()],
)
def test_both_parsers_agree(query: str) -> None:
    """Both parsers must produce identical SQL for every query in TESTCASES."""
    assert_parsers_agree(query)


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[
        ('mana:"2WW"',),
        ('mana:"q"',),
        ('mana:"2WWQ"',),
    ],
    ids=["quoted_valid", "quoted_invalid_bare_char", "quoted_invalid_trailing_char"],
)
def test_quoted_mana_value_parity(query: str) -> None:
    """A quoted mana value is validated the same as bare/braced notation in both parsers.

    'mana:"q"' used to skip the alphabet check entirely in both parsers, resolving to an empty
    cost dict that matched every card instead of 400ing or matching nothing.
    """
    assert_parsers_agree(query)


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[
        ("mana:s",),
        ("mana:snow",),
        ("mana:p",),
        ("mana:hello",),
    ],
    ids=["bare_snow_valid", "bare_snow_word_invalid", "bare_phyrexian_unpaired", "bare_junk_word"],
)
def test_bare_mana_character_parity(query: str) -> None:
    """A bare mana character is validated the same as its braced form in both parsers (#954).

    pyparsing's tokenizer used to only recognize a fixed subset of bare letters as mana-shaped at
    all; anything outside it (including 's', valid braced as '{s}') fell through to a plain string
    comparison and silently matched the wrong cards instead of 400ing or agreeing with the hand
    parser's rejection.
    """
    assert_parsers_agree(query)


# Real queries from benchmarks/wild-queries/wild-corpus.jsonl on which the two parsers disagree
# today, grouped by the root cause in #903. Sweeping that 14k-query corpus found exactly these.
#
# Each is xfail(strict=True): fixing one turns it into an XPASS and fails the suite, so the fix
# cannot land without deleting its entry here. Keep the minimal repro alongside the wild query —
# it is what a fixer works from, and it fails for the same reason.
KNOWN_DIVERGENCES: dict[str, tuple[str, list[str]]] = {
    # pyparsing's preprocess_implicit_and misreads the '-' as subtraction and inserts no AND,
    # because _rhs_introduces_comparison only counts comparison operators at depth 0 — the ones
    # inside the group are invisible to it. Numeric LHS yields `int - boolean` (invalid SQL);
    # a year LHS yields a parse error.
    "a_minus_before_group_after_numeric": (
        "#903 A: comparison nested in the group is invisible to _rhs_introduces_comparison",
        [
            "cmc>1 -(t:elf)",
            "pow=3 -(name:force or type:elf)",
            "usd>50 -(format:modern or format:commander)",
            "year:2019 -(t:elf)",
            "year:2019 -(oracle:exile or type:enchantment)",
            "year:2022 -(id:rg or usd<0.25)",
            "year:2022 -(id:ub or artist:avon)",
            "year:2022 -(set:mid or color:ubr)",
            "year:2022 -(tou=4 or set:mom)",
            "year:2023 -(id:rg or name:dark)",
            "year:2023 -(year:2020 or set:mom)",
            "year:2024 -(name:co or set:khm)",
        ],
    ),
    # The implicit-AND tokenizer matches CaselessKeyword("AND"/"OR") before it knows it is in
    # value position, so 'o:or' preprocesses to 'o: OR' and the value is gone. Only on the slow
    # path — a query with none of ()"'/{+* takes the fast path and is unaffected.
    "b_reserved_word_as_value": (
        "#903 B: and/or in value position consumed as a boolean operator by the tokenizer",
        [
            "o:and power+toughness>10",
            "(o:or o:more) t:land",
            "(oracle:two oracle:or oracle:more oracle:opponents) type:land",
        ],
    ),
    # On Scryfall '!' is an alias for '=' on color/mana/numeric/rarity/year/date fields, and not an
    # operator at all on text fields. Neither parser implements the alias: pyparsing rejects the
    # shape, and the hand parser applies the text-field fallback everywhere, so 'c!w' becomes
    # name LIKE %c% AND name = "w" and quietly matches nothing.
    "c_bang_equals_alias": (
        "#903 C: '!' as an '=' alias unimplemented — pyparsing rejects, hand parser mis-parses",
        [
            "c!w",
            "c!ubg cmc>=6 f:standard",
            "en-kor c!w",
            "o:destroy o:creature o:with o:flying c!g",
        ],
    ),
}


@pytest.mark.parametrize(
    argnames=["query"],
    argvalues=[
        pytest.param(query, marks=pytest.mark.xfail(strict=True, reason=reason), id=f"{cause}-{index}")
        for cause, (reason, queries) in KNOWN_DIVERGENCES.items()
        for index, query in enumerate(queries)
    ],
)
def test_known_parser_divergences(query: str) -> None:
    """Queries the parsers disagree on today (#903) — remove an entry when its cause is fixed."""
    assert_parsers_agree(query)
