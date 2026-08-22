"""The four typographic quotes Scryfall folds before lexing, and the only four.

Curly quotes (U+2018/U+2019 single, U+201C/U+201D double) show up in pasted text and were read as
ordinary letters, silently turning a search for Gaea's Blessing (curly apostrophe) into zero
results. Fold table matched against what api.scryfall.com itself treats as a quote (2026-08-16);
everything else quotation-shaped -- guillemets, low-9 quotes, primes, fullwidth forms, CJK
brackets, ornate quotes, backtick, acute, U+02BC -- stays literal.
"""

import pytest

from api.parsing import generate_sql_query, parse_scryfall_query
from api.parsing.parsing_f import balance_partial_query
from api.parsing.pyparsing_based import parse_search_query
from api.parsing.spans import fold_typographic_quotes

_LEFT_SINGLE = chr(0x2018)
_RIGHT_SINGLE = chr(0x2019)
_LEFT_DOUBLE = chr(0x201C)
_RIGHT_DOUBLE = chr(0x201D)

# (query with typographic quotes, the ASCII query it must mean)
FOLDED_CASES = [
    (f"name:{_LEFT_DOUBLE}Gaea{_RIGHT_SINGLE}s Blessing{_RIGHT_DOUBLE}", 'name:"Gaea\'s Blessing"'),
    (f"name:{_LEFT_SINGLE}Lightning Bolt{_RIGHT_SINGLE}", "name:'Lightning Bolt'"),
    (f"o:{_LEFT_DOUBLE}draw a card{_RIGHT_DOUBLE}", 'o:"draw a card"'),
    # The fold is a character substitution over the WHOLE query, not a rule about quoted regions:
    # a curly apostrophe INSIDE double quotes folds too, which is what makes Gaea's Blessing
    # findable at all.
    (f'name:"Gaea{_RIGHT_SINGLE}s Blessing"', 'name:"Gaea\'s Blessing"'),
    (f"t:creature o:{_LEFT_DOUBLE}flying{_RIGHT_DOUBLE} c:azorius", 't:creature o:"flying" c:azorius'),
]


@pytest.mark.parametrize(
    argnames=("query", "canonical_query"),
    argvalues=FOLDED_CASES,
    ids=[str(i) for i in range(len(FOLDED_CASES))],
)
def test_typographic_quotes_fold(query: str, canonical_query: str) -> None:
    """A curly-quoted query parses to exactly what its ASCII-quoted twin parses to, in both parsers."""
    assert generate_sql_query(parse_scryfall_query(query)) == generate_sql_query(parse_scryfall_query(canonical_query))
    assert generate_sql_query(parse_search_query(query)) == generate_sql_query(parse_search_query(canonical_query))


# Quotation-shaped characters Scryfall does NOT fold. Asserted on the fold itself rather than on a
# parse, because several of them are not lexable at all here -- the claim being pinned is that the
# substitution table has exactly four entries, and a wider table is the way this goes wrong.
@pytest.mark.parametrize(
    argnames="candidate",
    argvalues=[
        chr(0x00AB),  # left guillemet
        chr(0x00BB),  # right guillemet
        chr(0x2039),  # single left guillemet
        chr(0x203A),  # single right guillemet
        chr(0x201E),  # double low-9
        chr(0x201A),  # single low-9
        chr(0x2032),  # prime
        chr(0x2033),  # double prime
        chr(0x2035),  # reversed prime
        chr(0xFF02),  # fullwidth quotation mark
        chr(0xFF07),  # fullwidth apostrophe
        chr(0x300C),  # CJK corner bracket, opening
        chr(0x300D),  # CJK corner bracket, closing
        chr(0x300E),  # CJK white corner bracket, opening
        chr(0x300F),  # CJK white corner bracket, closing
        chr(0x275B),  # heavy single turned comma quotation mark ornament
        chr(0x275C),  # heavy single comma quotation mark ornament
        chr(0x275D),  # heavy double turned comma quotation mark ornament
        chr(0x275E),  # heavy double comma quotation mark ornament
        "`",  # grave accent
        chr(0x00B4),  # acute accent
        chr(0x02BC),  # modifier letter apostrophe
    ],
)
def test_other_quotation_marks_do_not_fold(candidate: str) -> None:
    """Everything except the four measured characters is left literal."""
    assert fold_typographic_quotes(f"name:{candidate}Bolt{candidate}") == f"name:{candidate}Bolt{candidate}"


@pytest.mark.parametrize(
    argnames=("candidate", "folded"),
    argvalues=[(_LEFT_SINGLE, "'"), (_RIGHT_SINGLE, "'"), (_LEFT_DOUBLE, '"'), (_RIGHT_DOUBLE, '"')],
)
def test_the_four_that_fold(candidate: str, folded: str) -> None:
    """The whole table, one row at a time."""
    assert fold_typographic_quotes(f"a{candidate}b") == f"a{folded}b"


def test_balance_folds_before_counting_quotes() -> None:
    """The balancer sees the folded text, or a typed opening curly quote balances to nothing."""
    assert balance_partial_query(f"name:{_LEFT_SINGLE}Lightning") == "name:'Lightning'"
    assert balance_partial_query(f"o:{_LEFT_DOUBLE}draw") == 'o:"draw"'
