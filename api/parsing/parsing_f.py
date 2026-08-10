"""Public entry points for Scryfall query parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.parsing.hand_parser import parse_query as _parse_query
from api.parsing.rewrite import rewrite_query
from api.parsing.spans import QUOTE_CHARS, has_dangling_escape, opens_regex, quote_close_index, regex_close_index

if TYPE_CHECKING:
    from api.parsing.nodes import Query


def _closer_for_partial_span(query: str, content_start: int, closer: str) -> str:
    """Return the suffix that closes a span whose content starts at *content_start* and runs to the end.

    A trailing backslash has nothing to escape yet, so appending *closer* on its own would escape
    *that* instead of ending the span — escape the backslash first.
    """
    return ("\\" if has_dangling_escape(query, content_start) else "") + closer


def balance_partial_query(query: str) -> str:
    """Balance quotes, regexes, and parentheses for typeahead searches using a stack."""
    open_parens = 0
    # Closer for a quoted string or regex still open at the end of the query. A span only ever needs
    # one, because everything after an unterminated opener is span content — there is nothing left to
    # open, and nothing after it to close.
    span_suffix = ""

    pos = 0
    while pos < len(query):
        char = query[pos]
        pos += 1

        # A quoted string and a /regex/ are both opaque: the quotes and parens inside them are
        # content, not delimiters. The span rules come from api.parsing.spans so the balancer and the
        # lexer cannot drift apart — where they disagree, the balancer "fixes" a quote the lexer
        # never saw (#905).
        if char in QUOTE_CHARS:
            close_index = quote_close_index(query, pos, char)
            if close_index is None:
                span_suffix = _closer_for_partial_span(query, pos, char)
                break
            pos = close_index + 1
            continue

        # A '/' in value position opens a regex; anywhere else it is division, an ordinary character.
        if char == "/":
            if opens_regex(query, pos - 1):
                close_index = regex_close_index(query, pos)
                if close_index is None:
                    # Still being typed. Close the regex rather than reading on, or the metacharacters
                    # the user has typed so far get balanced as query structure: `o:/[)` is a partial
                    # `o:/[)]/`, not a stray ')'.
                    span_suffix = _closer_for_partial_span(query, pos, "/")
                    break
                pos = close_index + 1
            continue

        if char == "(":
            open_parens += 1
        elif char == ")":
            if not open_parens:
                msg = f"Unbalanced closing character '{char}' cannot be balanced"
                raise ValueError(msg)
            open_parens -= 1

    return query + span_suffix + ")" * open_parens


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
