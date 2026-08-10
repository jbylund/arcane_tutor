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

import re

_DIGITS = frozenset("0123456789")

_COLORS = frozenset("WUBRG")

# Single-character atoms: a colour, colourless, snow, the variables, phyrexian, and Gleemax's cost.
# Grounded in the 48 distinct symbols the card corpus uses in mana_cost_text, plus the Un-set symbols
# that corpus holds no cards for — '∞' is Gleemax's whole mana cost, and 'H' + a colour is half mana
# ({HW} on Little Girl). The frontend already renders both, in the mana maps in app.js.
#
# Public because it is the one vocabulary: mana_cost_str_to_dict and calculate_cmc read a bare mana
# value one character at a time and each used to carry its own copy of this list, both incomplete in
# different ways. 'S' and 'P' missing from those copies is what made 'mana:s' match every card.
MANA_COST_ATOMS = _COLORS | frozenset("CSXYZP∞")

# Variables contribute nothing to mana value; every other non-generic atom counts as one pip.
ZERO_COST_ATOMS = frozenset("X")

_ATOMS = MANA_COST_ATOMS

# Half mana is written as 'H' followed by a colour: {HW}, {HR}.
_HALF_PREFIX = "H"
_HALF_SYMBOL_LENGTH = 2  # the 'H' and the colour it applies to

# A braced symbol anywhere in a mana value, e.g. the '2' and 'W' of '{2}{W}'.
_BRACED_SYMBOL = re.compile(r"\{([^}]*)\}")


def _is_atom(part: str) -> bool:
    """Return True if *part* is one side of a symbol: generic mana, a single atom, or half mana."""
    if part and all(char in _DIGITS for char in part):
        return True  # generic mana of any size: {0}, {16}, {1000000}
    if part in _ATOMS:
        return True
    return len(part) == _HALF_SYMBOL_LENGTH and part[0] == _HALF_PREFIX and part[1] in _COLORS


def is_valid_mana_symbol(symbol: str) -> bool:
    """Return True if *symbol* — the text between the braces, upper-cased — can appear in a mana cost.

    A symbol is either one atom or several joined by '/': hybrid ({W/U}), generic-hybrid ({2/W}),
    phyrexian ({W/P}) and hybrid-phyrexian ({R/G/P}) all follow that one shape.
    """
    return bool(symbol) and all(_is_atom(part) for part in symbol.split("/"))


def first_invalid_mana_symbol(value: str) -> str | None:
    """Return the first symbol in *value* that no mana cost could contain, or None if every one can.

    Covers both notations: the braced '{2}{W}{W}' and the bare '2WW', which mana_cost_str_to_dict and
    calculate_cmc read one character at a time. Checking the bare form matters more than it looks —
    those readers used to *drop* any character they did not recognise, so 'mana:snow' kept the 'W' and
    silently answered 'mana:w', and 'mana:hello' kept nothing and matched every card in the corpus
    (an empty cost dict is a subset of every cost). Dropping quietly is worse than refusing.
    """
    for symbol in _BRACED_SYMBOL.findall(value):
        if not is_valid_mana_symbol(symbol):
            return f"{{{symbol}}}"

    for char in _BRACED_SYMBOL.sub("", value):
        if char not in _DIGITS and char not in MANA_COST_ATOMS:
            return char

    return None
