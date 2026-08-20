"""Tests for which symbols a mana cost may contain."""

from __future__ import annotations

import pytest

from api.parsing.mana_symbols import first_invalid_mana_symbol, is_valid_mana_symbol

# Every distinct symbol the card corpus uses in mana_cost_text — 48 of them, via
# `regexp_matches(mana_cost_text, '[{]([^}]*)[}]', 'g')` over magic.cards — plus generic values no
# printing has used yet and the Un-set symbols the corpus holds no cards for ({HW} half mana on Little
# Girl, {∞} as Gleemax's whole cost). Every one of these has to stay searchable.
_VALID = (
    *[str(n) for n in (*range(21), 100, 1000000)],
    *"WUBRGCSXYZP",
    "∞",
    "HW",
    "HR",
    *("2/W", "2/U", "2/B", "2/R", "2/G"),
    *("W/U", "W/B", "U/B", "U/R", "B/R", "B/G", "R/G", "R/W", "G/W", "G/U"),
    *("C/W", "C/U", "C/B", "C/R", "C/G"),
    *("W/P", "U/P", "B/P", "R/P", "G/P"),
    *("G/U/P", "G/W/P", "R/G/P", "R/W/P"),
)

# Real Magic symbols that no mana cost can hold: tap, untap, energy, and the planar symbols. Searching
# mana for one of these can never match, which is the whole reason this check exists.
_REAL_BUT_NOT_A_COST = ("T", "Q", "E", "A", "CHAOS", "PW", "TK")


@pytest.mark.parametrize(argnames=["symbol"], argvalues=[(sym,) for sym in _VALID], ids=_VALID)
def test_real_cost_symbols_are_valid(symbol: str) -> None:
    """Anything a printed mana cost contains has to pass, or a legitimate search starts 400ing."""
    assert is_valid_mana_symbol(symbol) is True


@pytest.mark.parametrize(argnames=["symbol"], argvalues=[(sym,) for sym in _REAL_BUT_NOT_A_COST], ids=_REAL_BUT_NOT_A_COST)
def test_symbols_that_cannot_appear_in_a_cost_are_rejected(symbol: str) -> None:
    """'{Q}' is a real symbol — untap — but never part of a cost, so 'mana:{q}' cannot match."""
    assert is_valid_mana_symbol(symbol) is False


@pytest.mark.parametrize(
    argnames=["symbol"],
    argvalues=[
        ("",),  # 'mana:{}'
        ("'",),
        (")",),
        ("hello",),
        ("A/B/C/D",),  # every character is legal on its own, only the whole symbol is not
        ("W/",),  # trailing separator leaves an empty part
        ("/W",),
        ("W W",),
        ("HX",),  # half mana only combines with a colour
        ("HWW",),
        ("1.5",),
        ("-1",),
    ],
    ids=[
        "empty",
        "quote",
        "paren",
        "word",
        "every_char_legal_alone",
        "trailing_separator",
        "leading_separator",
        "inner_space",
        "half_non_color",
        "half_too_long",
        "decimal",
        "negative",
    ],
)
def test_junk_is_rejected(symbol: str) -> None:
    """A charset cannot settle this: 'A/B/C/D' is all-legal characters and still not a symbol."""
    assert is_valid_mana_symbol(symbol) is False


@pytest.mark.parametrize(
    argnames=["value", "expected"],
    argvalues=[
        ("{2}{W}{W}", None),
        ("2WW", None),  # the bare notation calculate_cmc also accepts
        ("{2/W}{U}", None),
        ("WU", None),
        ("{W}{Q}", "{Q}"),  # reports the offending symbol, not just a boolean
        ("{Q}{W}", "{Q}"),  # the first one, scanning left to right
        ("{W}{}", "{}"),
        ("{ AND O:BOLT)}", "{ AND O:BOLT)}"),  # what a swallowed stray brace balances to
        ("HELLO", "H"),  # bare text is checked too: dropping 'H','E','L','L','O' matched everything
        ("SNOW", "N"),  # 'mana:snow' used to keep only the 'W' and quietly answer 'mana:w'
        ("{W}T", "T"),  # a bare marker alongside a braced symbol
        ("S", None),  # snow, dropped by the old charset
        ("P", None),  # phyrexian, likewise
        ("2WWS", None),
        ("T{Q}", "T"),  # an invalid bare marker before an invalid braced symbol: bare wins, left to right
        ("{W}T{Q}", "T"),  # valid braced, then invalid bare, then invalid braced: the bare one is still first
    ],
    ids=[
        "braced",
        "bare",
        "hybrid_and_single",
        "bare_two_colors",
        "invalid_second",
        "invalid_first",
        "empty_symbol",
        "swallowed_brace",
        "bare_word",
        "bare_word_keeping_one_letter",
        "bare_marker",
        "bare_snow",
        "bare_phyrexian",
        "bare_mixed",
        "bare_before_braced",
        "bare_between_braced",
    ],
)
def test_first_invalid_mana_symbol(value: str, expected: str | None) -> None:
    """Both notations are checked, and the first offender is named so the message can say which."""
    assert first_invalid_mana_symbol(value) == expected
