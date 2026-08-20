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

from api.parsing.card_query_nodes import BARE_MANA_ATOMS as _BARE_ATOMS
from api.parsing.card_query_nodes import BRACED_MANA_SYMBOL as _BRACED_SYMBOL

_DIGITS = frozenset("0123456789")

# The five colours, derived from _BARE_ATOMS rather than retyped, so the two can't silently disagree
# about which letters they are.
_COLORS = _BARE_ATOMS - frozenset("CX")

# Single-character atoms: a colour, colourless, snow, and X (the only variable a real cost uses).
# Grounded in the 48 distinct symbols the card corpus uses in mana_cost_text, plus generic values no
# printing has used yet. Deliberately excludes symbols that appear only on cards from "funny" (Un-)sets
# — '∞' (Gleemax's whole cost), 'H' + a colour (half mana, e.g. Little Girl's {HW}), and 'Y'/'Z' (only
# ever printed together with X and each other, on The Ultimate Nightmare of Wizards of the Coast
# Customer Service's {X}{Y}{Z}{R}{R}) — since `preprocess_card` filters every `set_type == "funny"`
# card out of the corpus, so no mana cost these queries can ever match contains them: they belong with
# the rest of `_REAL_BUT_NOT_A_COST` in test_mana_symbols.py, not here.
# Phyrexian ('P') is deliberately absent too: it never appears unpaired in a real cost, only through
# the paired shapes `_PART_SHAPES` already enumerates ({W/P}, {2/P}, hybrid-phyrexian).
_ATOMS = _COLORS | frozenset("CSX")

# The generic side of generic-hybrid mana is always specifically '2' ({2/W}, never {1/W} or {3/W}).
_GENERIC_HYBRID_VALUE = "2"

# Colourless joins colour on the "which side does this go on" ambiguity a hybrid pair has — a user
# typing {C/W} from memory has as little reason to know it isn't printed {W/C} as they do for two
# colours — so both share one permutation-generated set of ordered pairs. Generic and phyrexian don't:
# every printing has the generic side first and the phyrexian side last, so those two are one-directional.
_HYBRID_ATOMS = _COLORS | frozenset("C")

# Every valid 2- or 3-part shape, as the exact ordered tuple `symbol.split("/")` must produce. Rule- not
# corpus-derived: a colour pair or hybrid-phyrexian triple is valid because the game defines that shape,
# whether or not a card has ever been printed with it (real corpus printings only cover 4 of the 10
# possible hybrid-phyrexian colour pairs, but {U/B/P} is exactly as valid a request as the printed ones).
# `itertools.permutations` never repeats an element in one draw, so {W/W}, {2/2} and {C/C} are excluded
# for free — no separate duplicate-side check is needed.
_PART_SHAPES = frozenset(
    (
        *itertools.permutations(_HYBRID_ATOMS, 2),  # hybrid & colourless hybrid, either order: {W/U}, {C/W}
        *((_GENERIC_HYBRID_VALUE, c) for c in _COLORS),  # generic hybrid, generic first: {2/W}
        *((c, "P") for c in _COLORS),  # phyrexian, colour first: {W/P}
        *((a, b, "P") for a, b in itertools.permutations(_COLORS, 2)),  # hybrid-phyrexian, colours either order
    )
)


def _is_atom(part: str) -> bool:
    """Return True if *part* is a whole one-part symbol: generic mana or a single atom."""
    if part and all(char in _DIGITS for char in part):
        return True  # generic mana of any size: {0}, {16}, {1000000}
    return part in _ATOMS


def is_valid_mana_symbol(symbol: str) -> bool:
    """Return True if *symbol* — the text between the braces, upper-cased — can appear in a mana cost.

    A symbol is either one atom, or two or three sides joined by '/'. See `_PART_SHAPES` for which
    combinations, and in which order, are real: {W/U}/{U/W} are the same symbol, but {W/2} and {P/W}
    are not — the generic and phyrexian sides never move — and a side never repeats: {W/W}, {2/2}
    aren't symbols even though 'W' and '2' are each legal alone.
    """
    if not symbol:
        return False
    parts = tuple(symbol.split("/"))
    if len(parts) == 1:
        return _is_atom(parts[0])
    return parts in _PART_SHAPES


def first_invalid_mana_symbol(value: str) -> str | None:
    """Return the first symbol in *value* that no mana cost could contain, or None if every one can.

    Braced symbols are checked as a whole (see `is_valid_mana_symbol`); bare characters are checked
    one at a time against `_BARE_ATOMS`, the exact alphabet `mana_cost_str_to_dict`/`calculate_cmc`
    count outside braces. Without this, a bare character neither function recognises — 'Q' in
    '2WWQ', or all of 'hello' — is silently dropped by both instead of rejected: 'mana:2WWQ' would
    quietly run as 'mana:2WW', matching cards the query never named.

    One pass over `value`: `_BRACED_SYMBOL.finditer` walks it once, and the bare characters between
    (and after) the matches it finds are checked as that single walk goes, left to right — rather than
    a first pass collecting every braced symbol and a second re-deriving the bare characters by
    stripping them back out.
    """
    pos = 0
    for match in _BRACED_SYMBOL.finditer(value):
        for char in value[pos : match.start()]:
            if char not in _DIGITS and char not in _BARE_ATOMS:
                return char
        symbol = match.group(1)
        if not is_valid_mana_symbol(symbol):
            return f"{{{symbol}}}"
        pos = match.end()
    for char in value[pos:]:
        if char not in _DIGITS and char not in _BARE_ATOMS:
            return char
    return None
