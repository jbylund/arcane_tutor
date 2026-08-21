"""Tests for which symbols a mana cost may contain."""

from __future__ import annotations

import pytest

from api.parsing.mana_symbols import first_invalid_mana_symbol, is_valid_mana_symbol

# Every distinct symbol the card corpus uses in mana_cost_text — 48 of them, via
# `regexp_matches(mana_cost_text, '[{]([^}]*)[}]', 'g')` over magic.cards — plus generic values no
# printing has used yet, plus every rule-valid hybrid-phyrexian colour pair (only 4 of the 10 have
# actually been printed, but a mana search isn't limited to what's been printed — see mana_symbols.py).
# Every one of these has to stay searchable.
_VALID = (
    *[str(n) for n in (*range(21), 100, 1000000)],
    *"WUBRGCSX",
    *("2/W", "2/U", "2/B", "2/R", "2/G"),
    *("W/U", "W/B", "U/B", "U/R", "B/R", "B/G", "R/G", "R/W", "G/W", "G/U"),
    *("C/W", "C/U", "C/B", "C/R", "C/G"),
    *("W/P", "U/P", "B/P", "R/P", "G/P"),
    *("G/U/P", "G/W/P", "R/G/P", "R/W/P", "W/U/P", "W/B/P", "U/B/P", "U/R/P", "B/R/P", "B/G/P"),
    # Hybrid and colourless hybrid are a set, not a slot: whichever order a user types a real symbol's
    # colours in, it's the same symbol. Only a sample of reversed forms, not every pair reversed.
    *("U/W", "W/C", "U/G/P"),
)

# Real Magic symbols that no mana cost can hold: tap, untap, energy, and the planar symbols. Searching
# mana for one of these can never match, which is the whole reason this check exists. '∞' (Gleemax's
# whole cost), half mana ({HW}, {HR}, ...), and 'Y'/'Z' belong here too, not in _VALID: all are real
# printed symbols, but only on cards from "funny" (Un-)sets — 'Y'/'Z' via The Ultimate Nightmare of
# Wizards of the Coast Customer Service's {X}{Y}{Z}{R}{R} — which `preprocess_card` filters out of the
# corpus entirely, so a mana cost containing them can never match a row either.
_REAL_BUT_NOT_A_COST = ("T", "Q", "E", "A", "CHAOS", "PW", "TK", "∞", "HW", "HR", "Y", "Z")


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
        ("1.5",),
        ("-1",),
        ("W/W",),  # a side never repeats, even though 'W' is legal alone
        ("2/2",),
        ("C/C",),
        ("W/U/B",),  # three plain colours: no 'P', so not a hybrid-phyrexian shape either
        ("C/P",),  # phyrexian mana is only ever a colour's, never colourless's
        ("2/P",),  # nor generic's
        ("W/1",),  # generic-hybrid's generic side is always '2'
        ("W/2",),  # ... and it's always first: {2/W}, never {W/2}
        ("P/W",),  # phyrexian is always last: {W/P}, never {P/W}
        ("P",),  # phyrexian never appears unpaired: {P} matches no real cost (#941)
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
        "decimal",
        "negative",
        "repeated_color_side",
        "repeated_generic_side",
        "repeated_colorless_side",
        "three_colors_no_phyrexian",
        "colorless_phyrexian",
        "generic_phyrexian",
        "non_two_generic",
        "generic_wrong_position",
        "phyrexian_wrong_position",
        "phyrexian_unpaired",
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
        ("HELLO", "H"),  # bare text is checked one character at a time, not as a word
        ("{W}T", "T"),  # ... including a bare marker alongside a braced symbol
        ("2WWQ", "Q"),  # the bug this exists to close: silently dropped by calculate_cmc, not rejected
        ("2WWX", None),  # X is bare notation's own real pip, not a leftover from a braced check
        ("2WWS", None),  # 'S' (snow) is bare-valid too — mana:s means the same as mana:{s} (#954)
        ("SNOW", "N"),  # 'S' alone is valid, but 'N' isn't — same offender {s}{n}{o}{w} reports
        ("H{Q}", "H"),  # the bare 'H' comes before '{Q}' in the string, so it is the true first offender
        ("{Q}H", "{Q}"),  # here the braced symbol genuinely is first
        ("{P}", "{P}"),  # phyrexian never appears unpaired: {P} matches no real cost (#941)
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
        "bare_word_rejected",
        "bare_marker_rejected",
        "bare_extra_char_rejected",
        "bare_x_accepted",
        "bare_s_accepted",
        "bare_snow_word_first_bad_char",
        "bare_before_braced",
        "braced_before_bare",
        "braced_phyrexian_unpaired",
    ],
)
def test_first_invalid_mana_symbol(value: str, expected: str | None) -> None:
    """Braced symbols are checked as a whole; bare characters are checked one at a time."""
    assert first_invalid_mana_symbol(value) == expected
