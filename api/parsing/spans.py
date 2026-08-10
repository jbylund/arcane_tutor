"""Character-level regex-span rules shared by the lexer and the query balancer.

A leaf module on purpose: ``parsing_f`` imports ``hand_parser``, so anything both need has to sit
below both of them. Keeping two copies of "where does a regex start, and where does it end" is how
#905 happened — the balancer closed a quote that the lexer had already treated as pattern content.
"""

from __future__ import annotations

# Last character of every comparison operator the lexer emits (':', '=', '!=', '>=', '<=', '>', '<').
# A '/' directly after one of these is in value position, which is the only place a regex opens.
# test_spans.py pins this against the operators hand_parser actually emits.
COMPARISON_TAIL_CHARS = frozenset(":=><")


def opens_regex(query: str, slash_index: int) -> bool:
    """Return True if the '/' at *slash_index* opens a regex rather than being division.

    The character-level form of the rule in hand_parser.tokenize, for callers that have no token
    stream to look back into — the balancers, which run on partially typed queries. The lexer checks
    the same thing more precisely, by asking whether the previous token was a comparison operator.
    """
    pos = slash_index - 1
    while pos >= 0 and query[pos].isspace():
        pos -= 1
    return pos >= 0 and query[pos] in COMPARISON_TAIL_CHARS


def regex_close_index(query: str, start: int) -> int | None:
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
