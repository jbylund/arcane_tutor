"""Which symbols may appear in a mana cost.

The companion to ``api.parsing.spans``, and deliberately a separate module: ``spans`` answers where a
``{...}`` ends, this answers whether what is inside it means anything. The second question cannot be
decided per character — every character of ``{A/B/C/D}`` is individually legal — so it takes the
symbol as a whole.

Narrower than "every real Magic symbol" on purpose. ``{T}``, ``{Q}``, ``{E}``, ``{CHAOS}`` and
``{PW}`` are all real, but none of them can appear in a mana cost, and a mana cost is the only thing a
MANA-class field matches against: both of them — ``mana``/``m`` and ``devotion`` — are derived from
``mana_cost_text``. Searching for one is a query that can never match, so it is worth a 400 rather
than an empty result set.
"""

from __future__ import annotations

import itertools
import re

_DIGITS = frozenset("0123456789")

_COLORS = frozenset("WUBRG")

# Single-character atoms: a colour, colourless, snow, the variables, and phyrexian. Grounded in the 48
# distinct symbols the card corpus uses in mana_cost_text, plus generic values no printing has used
# yet. Deliberately excludes symbols that appear only on cards from "funny" (Un-)sets — '∞' (Gleemax's
# whole cost) and 'H' + a colour (half mana, e.g. Little Girl's {HW}) — since `preprocess_card` filters
# every `set_type == "funny"` card out of the corpus, so no mana cost these queries can ever match
# contains them: they belong with the rest of `_REAL_BUT_NOT_A_COST` in test_mana_symbols.py, not here.
_ATOMS = _COLORS | frozenset("CSXYZP")

# The generic side of generic-hybrid mana is always specifically '2' ({2/W}, never {1/W} or {3/W}).
_GENERIC_HYBRID_VALUE = "2"

_TWO_PARTS = 2
_THREE_PARTS = 3

# A multi-part symbol's sides can appear in either order — a user typing from memory has no reason to
# know Scryfall prints hybrid as '{W/U}' rather than '{U/W}', and the two name the same symbol — so
# each shape is stored as a frozenset of its sides, not a tuple. What a *set* can't capture is a side
# repeating itself: '{W/W}' and '{2/2}' aren't symbols even though 'W' and '2' are each legal alone, so
# duplicate-side combinations are rejected before this table is ever consulted.
_TWO_PART_SHAPES = frozenset(
    frozenset(pair)
    for pair in (
        *itertools.combinations(_COLORS, 2),  # hybrid: any two colours, e.g. {W/U}
        *((c, "C") for c in _COLORS),  # colourless hybrid: {C/W}
        *((c, _GENERIC_HYBRID_VALUE) for c in _COLORS),  # generic hybrid: {2/W}
        *((c, "P") for c in _COLORS),  # phyrexian: {W/P}
    )
)

# Hybrid-phyrexian, e.g. {R/G/P}: corpus-grounded rather than every 2-colour combination + 'P', since
# real printings don't cover all ten colour pairs for this one.
_THREE_PART_SHAPES = frozenset(frozenset((*pair, "P")) for pair in (("G", "U"), ("G", "W"), ("R", "G"), ("R", "W")))

# A braced symbol anywhere in a mana value, e.g. the '2' and 'W' of '{2}{W}'.
_BRACED_SYMBOL = re.compile(r"\{([^}]*)\}")


def _is_atom(part: str) -> bool:
    """Return True if *part* is one side of a symbol: generic mana or a single atom."""
    if part and all(char in _DIGITS for char in part):
        return True  # generic mana of any size: {0}, {16}, {1000000}
    return part in _ATOMS


def is_valid_mana_symbol(symbol: str) -> bool:
    """Return True if *symbol* — the text between the braces, upper-cased — can appear in a mana cost.

    A symbol is either one atom, or two or three sides joined by '/' in either order: hybrid ({W/U} or
    {U/W}), generic-hybrid ({2/W}), phyrexian ({W/P}) and hybrid-phyrexian ({R/G/P}). Repeating a side
    is never real, even when the side is: {W/W} and {2/2} aren't symbols.
    """
    if not symbol:
        return False
    parts = symbol.split("/")
    if len(parts) == 1:
        return _is_atom(parts[0])
    if len(set(parts)) != len(parts):
        return False
    combo = frozenset(parts)
    if len(parts) == _TWO_PARTS:
        return combo in _TWO_PART_SHAPES
    if len(parts) == _THREE_PARTS:
        return combo in _THREE_PART_SHAPES
    return False


def first_invalid_mana_symbol(value: str) -> str | None:
    """Return the first braced symbol in *value* that no mana cost could contain, or None if every one can.

    Braced symbols only, for now. A mana value may also be written bare — '2WW' for '{2}{W}{W}' — but
    the two parsers do not agree on what a bare value is: pyparsing's mana pattern does not match
    'hello', so 'mana:hello' takes a different branch there and never becomes a mana value at all.
    Checking bare text here would reject on one side and not the other. '{...}' has no such ambiguity.
    """
    for symbol in _BRACED_SYMBOL.findall(value):
        if not is_valid_mana_symbol(symbol):
            return f"{{{symbol}}}"
    return None
