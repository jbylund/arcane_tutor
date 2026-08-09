"""Public entry points for Scryfall query parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.parsing.hand_parser import parse_query as _parse_query
from api.parsing.rewrite import rewrite_query

if TYPE_CHECKING:
    from api.parsing.nodes import Query


# Last character of every comparison operator the lexer emits (':', '=', '!=', '>=', '<=', '>', '<').
# A '/' directly after one of these is in value position, which is the only place a regex opens.
_COMPARISON_TAIL_CHARS = frozenset(":=><")


def _opens_regex(query: str, slash_index: int) -> bool:
    """Return True if the '/' at *slash_index* opens a regex rather than being division.

    Mirrors the lexer's rule in hand_parser.tokenize: a regex only opens in value position, i.e.
    directly after a comparison operator. The two must agree on what counts as regex — where they
    disagree, the balancer "fixes" a quote the lexer never saw (#905).
    """
    pos = slash_index - 1
    while pos >= 0 and query[pos].isspace():
        pos -= 1
    return pos >= 0 and query[pos] in _COMPARISON_TAIL_CHARS


def _regex_close_index(query: str, start: int) -> int | None:
    """Return the index of the '/' closing a regex that opened before *start*, or None if unterminated."""
    pos = start
    length = len(query)
    while pos < length:
        if query[pos] == "\\" and pos + 1 < length:
            pos += 2
        elif query[pos] == "/":
            return pos
        else:
            pos += 1
    return None


def balance_partial_query(query: str) -> str:
    """Balance quotes and parentheses for typeahead searches using a stack."""
    char_to_mirror = {
        "(": ")",
        "'": "'",  # single quote is own mirror
        '"': '"',  # double quote is own mirror
        ")": "(",
    }
    unbalanced_closing_chars = {")"}
    quote_chars = {"'", '"'}

    current_stack = []
    pos = 0
    while pos < len(query):
        char = query[pos]
        pos += 1

        # When inside a quoted string, only the matching closing quote ends it.
        if current_stack and current_stack[-1] in quote_chars:
            if char == current_stack[-1]:
                current_stack.pop()
            continue

        # A closed /regex/ is opaque: the quotes and parens inside it are pattern characters, not
        # delimiters. A '/' that is division, or that opens an unterminated regex, falls through as
        # an ordinary character.
        if char == "/":
            if _opens_regex(query, pos - 1):
                close_index = _regex_close_index(query, pos)
                if close_index is not None:
                    pos = close_index + 1
            continue

        mirrored_char = char_to_mirror.get(char)
        if not mirrored_char:
            continue
        if current_stack and current_stack[-1] == mirrored_char:
            current_stack.pop()
        else:
            if char in unbalanced_closing_chars:
                msg = f"Unbalanced closing character '{char}' cannot be balanced"
                raise ValueError(msg)
            current_stack.append(char)
    while current_stack:
        char = current_stack.pop()
        mirrored_char = char_to_mirror[char]
        query += mirrored_char
    return query


def parse_scryfall_query(query: str) -> Query:
    """Parse a Scryfall search query into a card-specific AST.

    Args:
        query: The search query string to parse.

    Returns:
        A Scryfall-specific Query AST.
    """
    # parse => transform => rest: the whole rewrite pipeline runs on the common AST at this shared
    # seam, so it applies identically regardless of which parser _parse_query is.
    return rewrite_query(_parse_query(query))
