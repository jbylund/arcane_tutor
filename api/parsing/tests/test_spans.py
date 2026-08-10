"""Tests for the regex-span rules shared by the lexer and the balancers."""

from __future__ import annotations

import inspect
import re

import pytest

from api.parsing import hand_parser
from api.parsing.spans import (
    COMPARISON_TAIL_CHARS,
    has_dangling_escape,
    opens_regex,
    quote_close_index,
    regex_close_index,
    unescape,
)

# Matches the operator literals the lexer emits, e.g. Token(TT.OP, ">=", start, sb).
_OP_TOKEN_LITERAL = re.compile(r'Token\(TT\.OP,\s*"([^"]+)"')


def test_comparison_tail_chars_covers_every_operator_the_lexer_emits() -> None:
    """Every comparison operator must end in a character COMPARISON_TAIL_CHARS knows about.

    The balancers cannot see tokens, so they recognise value position from the character before the
    '/'. That approximation only holds while the lexer's operators all end in one of these chars, and
    nothing else forces the two to agree — hence the source scan: adding an operator that ends in a
    new character (say '~=') has to fail here rather than in the frontend, where it would show up as
    a regex the balancer mangles.
    """
    operators = set(_OP_TOKEN_LITERAL.findall(inspect.getsource(hand_parser)))
    assert operators, "found no TT.OP literals — the scan pattern no longer matches the lexer"
    assert {op[-1] for op in operators} <= COMPARISON_TAIL_CHARS


@pytest.mark.parametrize(
    argnames=["query", "slash_index", "expected"],
    argvalues=[
        ("name:/bolt/", 5, True),
        ("name: /bolt/", 6, True),  # whitespace between operator and pattern
        ("power>/x/", 6, True),
        ("power>=/x/", 7, True),
        ("power/2", 5, False),  # division
        ("/bolt/", 0, False),  # bare regex: nothing in front of the slash
        ("(t:elf) /foo/", 8, False),  # not directly after an operator
    ],
    ids=[
        "colon",
        "colon_then_space",
        "greater_than",
        "greater_or_equal",
        "division",
        "bare_leading_slash",
        "after_group",
    ],
)
def test_opens_regex(query: str, slash_index: int, expected: bool) -> None:
    """A regex opens only in value position, directly after a comparison operator."""
    assert opens_regex(query, slash_index) is expected


@pytest.mark.parametrize(
    argnames=["query", "start", "expected"],
    argvalues=[
        ("name:/bolt/", 6, 10),
        ("name:/a/ o:/b/", 6, 7),  # stops at the first close, not the last slash in the query
        (r"name:/a\/b/", 6, 10),  # escaped delimiter is pattern content
        ("name:/bolt", 6, None),  # unterminated
        ("name:/a\\", 6, None),  # trailing backslash consumes the end of the query
    ],
    ids=["simple", "first_close_wins", "escaped_delimiter", "unterminated", "trailing_backslash"],
)
def test_regex_close_index(query: str, start: int, expected: int | None) -> None:
    """The close scan steps over escapes and returns None when the pattern never closes."""
    assert regex_close_index(query, start) == expected


@pytest.mark.parametrize(
    argnames=["query", "quote", "expected"],
    argvalues=[
        ("'abc'", "'", 4),
        (r"'don\'t'", "'", 7),  # the escaped quote is content, not the close
        (r"'a\\'", "'", 4),  # an escaped backslash does not escape the quote after it
        (r'"say \"hi\""', '"', 11),
        ("'a\"b'", "'", 4),  # the other quote type is content
        ("'abc", "'", None),  # unterminated
        (r"'abc\'", "'", None),  # the only candidate close is escaped
    ],
    ids=["plain", "escaped_quote", "escaped_backslash", "double_quoted", "other_quote_type", "unterminated", "escaped_close"],
)
def test_quote_close_index(query: str, quote: str, expected: int | None) -> None:
    """A backslash escapes the next character, so it cannot end the string."""
    assert quote_close_index(query, 1, quote) == expected


@pytest.mark.parametrize(
    argnames=["query", "expected"],
    argvalues=[
        ("'abc", False),
        ("'abc" + "\\", True),  # nothing left to escape: a closer appended now would be escaped
        (r"'abc\\", False),  # the backslash is itself escaped
        (r"'a\'b", False),
    ],
    ids=["no_backslash", "dangling", "escaped_backslash", "escape_consumed"],
)
def test_has_dangling_escape(query: str, expected: bool) -> None:
    """A trailing backslash leaves an escape with nothing to apply to."""
    assert has_dangling_escape(query, 1) is expected


@pytest.mark.parametrize(
    argnames=["text", "expected"],
    argvalues=[
        ("abc", "abc"),
        (r"don\'t", "don't"),
        (r"a\\b", r"a\b"),
        (r"say \"hi\"", 'say "hi"'),
    ],
    ids=["no_escapes", "escaped_quote", "escaped_backslash", "escaped_double_quotes"],
)
def test_unescape(text: str, expected: str) -> None:
    """Span content drops one level of backslash escaping, as the lexer's QUOTED token does."""
    assert unescape(text) == expected
