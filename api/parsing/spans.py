"""Character-level span rules shared by the lexer and the query balancer.

A leaf module on purpose: ``parsing_f`` imports ``hand_parser``, so anything both need has to sit
below both of them. Keeping two copies of "where does a span start, and where does it end" is how
#905 happened — the balancer closed a quote that the lexer had already treated as content.
"""

from __future__ import annotations

import re

QUOTE_CHARS = frozenset("'\"")

# A backslash escapes the character after it inside a quoted string, so '\'' is one string holding a
# single quote. Anything that has to find the end of a string has to know that.
_ESCAPED_CHAR = re.compile(r"\\(.)", re.DOTALL)

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


def brace_close_index(query: str, start: int) -> int | None:
    """Return the index of the '}' closing a mana symbol whose content starts at *start*, or None if unterminated.

    A plain search for the next '}': no escape sequence exists inside a mana symbol, so a '{...}' is
    opaque whatever it holds. There is deliberately no charset bounding the content. Stopping the span
    at the first character no real symbol could contain looks attractive — it would keep a stray '{'
    from swallowing the rest of the query — but it cannot be done here, because the lexer demands a '}'
    for every '{': a balancer that declined to close the '{' in 'mana:{'' would leave a prefix of the
    accepted query "mana:{'}" unlexable halfway through being typed, which is #908's failure mode.

    Whether the content is a *real* symbol is a separate question, and not one a charset could settle
    in any case — every character of '{A/B/C/D}' is individually legal, so only checking the symbol as
    a whole rejects it. That check belongs wherever mana values are validated.
    """
    index = query.find("}", start)
    return None if index < 0 else index


def regex_close_index(query: str, start: int) -> int | None:
    """Return the index of the '/' closing a regex that opened before *start*, or None if unterminated."""
    return _close_index(query, start, "/")


def quote_close_index(query: str, start: int, quote: str) -> int | None:
    """Return the index of the *quote* closing a string that opened before *start*, or None if unterminated."""
    return _close_index(query, start, quote)


def _close_index(query: str, start: int, closer: str) -> int | None:
    """Return the index of the next unescaped *closer* at or after *start*, or None if there is none."""
    pos = start
    length = len(query)
    while pos < length:
        if query[pos] == "\\" and pos + 1 < length:
            pos += 2
        elif query[pos] == closer:
            return pos
        else:
            pos += 1
    return None


def has_dangling_escape(query: str, start: int) -> bool:
    """Return True if the span opening at *start* runs to the end of *query* on an unfinished escape.

    A trailing backslash has nothing to escape yet, so appending a closer would escape *that* instead
    of ending the span. Callers completing a partial query have to escape the backslash first.
    """
    pos = start
    length = len(query)
    while pos < length:
        if query[pos] == "\\":
            if pos + 1 >= length:
                return True
            pos += 2
        else:
            pos += 1
    return False


def unescape(text: str) -> str:
    r"""Resolve backslash escapes in span content, so ``a\'b`` becomes ``a'b``."""
    return _ESCAPED_CHAR.sub(r"\1", text)
