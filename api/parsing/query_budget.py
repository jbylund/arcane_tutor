"""Public search query size and parenthesis-nesting bounds.

Limits are calibrated on distinct ``magic.cards`` names from the blue database
(``scripts/measure_decklist_query_budget.py``):

* **3500 UTF-8 bytes** - 100k random 100-card decklist queries in the shape
  ``(!"…" OR …) f:commander`` landed at p99.9 ~2547 B and max ~2631 B; a
  real cEDH reference list (MTGTop8 Witherbloom) was ~2655 B. The limit adds
  headroom for longer names and trailing filters.
* **10 parenthesis nesting levels** - maximum ``( … ( … ) … )`` depth during
  parse (sibling ``(a) (b)`` groups do not accumulate). Legitimate Scryfall
  syntax is almost always depth 0-3; depth 10 was verified cheap in parse/SQL
  benchmarks; unbounded nesting hits ``RecursionError`` around depth 200.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_QUERY_UTF8_BYTES = 3500
MAX_GROUP_DEPTH = 10
MAX_QUERY_LOG_PREVIEW_CHARS = 80

QUERY_TOO_LONG_MESSAGE = "Search query exceeds the maximum allowed length."
QUERY_REGEX_REJECTED_MESSAGE = "Search query contains an unsupported regular expression."


class InvalidRegexPatternError(ValueError):
    """Raised when a regex leaf fails Python's regex parser before search runs."""

    def __init__(self, *, reason: str) -> None:
        """Initialize with the quotable reason (no `` at position N`` suffix)."""
        self.reason = reason
        super().__init__(reason)

    def user_message_for_query(self, query: str) -> str:
        """Return the same shape as the SQL InvalidRegularExpression handler."""
        return f"The search query '{query}' contains an invalid regular expression: {self.reason}."


class QueryBudgetExceeded(ValueError):  # noqa: N818
    """Raised when a query exceeds a measured public bound."""

    def __init__(self, *, kind: Literal["length", "depth", "regex_leaves", "regex_pattern"]) -> None:
        """Initialize with a stable, non-disclosing user message."""
        self.kind = kind
        self.user_message = QUERY_REGEX_REJECTED_MESSAGE if kind.startswith("regex") else QUERY_TOO_LONG_MESSAGE
        super().__init__(self.user_message)


def utf8_byte_length(text: str) -> int:
    """Return the UTF-8 byte length of *text*."""
    return len(text.encode("utf-8"))


def check_query_byte_length(query: str) -> None:
    """Reject *query* when it exceeds the public byte limit."""
    if utf8_byte_length(query) > MAX_QUERY_UTF8_BYTES:
        raise QueryBudgetExceeded(kind="length")


def check_search_param_lengths(params: Mapping[str, object]) -> None:
    """Reject when either ``q`` or ``query`` exceeds the byte limit.

    Both aliases are checked independently so an oversized unused alias cannot
    reach cache-key construction or downstream parsing.
    """
    for key in ("q", "query"):
        value = params.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                check_query_byte_length(str(item))
        else:
            check_query_byte_length(str(value))


def bounded_query_log_context(query: str) -> dict[str, str]:
    """Return a bounded preview and digest suitable for rejection logs."""
    preview_limit = MAX_QUERY_LOG_PREVIEW_CHARS
    preview = query if len(query) <= preview_limit else f"{query[:preview_limit]}…"
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return {"query_preview": preview, "query_digest": digest}
