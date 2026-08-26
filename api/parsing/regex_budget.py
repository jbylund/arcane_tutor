"""Static regex pattern and query-level limits for public search.

Calibrated in ``docs/issues/security-regex-execution-budget.md`` and
``scripts/regex_limit_survey/``. Enforced on the post-rewrite AST so only
patterns that will actually run as regex are checked (plain literals already
lowered to ``StringValueNode``).

Runtime/engine limits (``backtrack_limit``, request wall clock, SQL timeout) live
elsewhere; this module is parse-time static bounds only.
"""

from __future__ import annotations

import sre_constants as sre
import sre_parse
from typing import TYPE_CHECKING

from api.parsing.nodes import AndNode, BinaryOperatorNode, NotNode, OrNode, QueryNode, RegexValueNode
from api.parsing.query_budget import QueryBudgetExceeded

if TYPE_CHECKING:
    from api.parsing.nodes import Query

MAX_REGEX_LEAVES_PER_QUERY = 10
MAX_PATTERN_UTF8_BYTES = 256
MAX_LOOKAROUNDS_PER_PATTERN = 4
MAX_ALTERNATIONS_PER_PATTERN = 32
MAX_REGEX_AST_NODES = 64
MAX_REGEX_PARSE_DEPTH = 16
MAX_QUANTIFIER_BOUND = 1024

_LOOKAROUND_TOKENS = ("(?=", "(?!", "(?<=", "(?<!")


def check_regex_cost(query: Query) -> None:
    """Reject *query* when any surviving regex leaf exceeds a public static bound."""
    patterns = _collect_regex_patterns(query.root)
    if len(patterns) > MAX_REGEX_LEAVES_PER_QUERY:
        raise QueryBudgetExceeded(kind="regex_leaves")
    for pattern in patterns:
        _check_pattern(pattern)


def _collect_regex_patterns(node: QueryNode) -> list[str]:
    if isinstance(node, (AndNode, OrNode)):
        out: list[str] = []
        for operand in node.operands:
            out.extend(_collect_regex_patterns(operand))
        return out
    if isinstance(node, NotNode):
        return _collect_regex_patterns(node.operand)
    if isinstance(node, BinaryOperatorNode) and node.operator == ":" and isinstance(node.rhs, RegexValueNode):
        return [node.rhs.value]
    return []


def _check_pattern(pattern: str) -> None:
    if len(pattern.encode("utf-8")) > MAX_PATTERN_UTF8_BYTES:
        raise QueryBudgetExceeded(kind="regex_pattern")

    lookarounds = sum(pattern.count(token) for token in _LOOKAROUND_TOKENS)
    if lookarounds > MAX_LOOKAROUNDS_PER_PATTERN:
        raise QueryBudgetExceeded(kind="regex_pattern")

    if _count_alternations(pattern) > MAX_ALTERNATIONS_PER_PATTERN:
        raise QueryBudgetExceeded(kind="regex_pattern")

    if _has_stacked_numeric_quantifiers(pattern):
        raise QueryBudgetExceeded(kind="regex_pattern")

    for lower, upper in _numeric_quantifier_bounds(pattern):
        if lower > MAX_QUANTIFIER_BOUND or upper > MAX_QUANTIFIER_BOUND:
            raise QueryBudgetExceeded(kind="regex_pattern")

    metrics = _regex_parse_metrics(pattern)
    if metrics is not None:
        nodes, depth = metrics
        if nodes > MAX_REGEX_AST_NODES or depth > MAX_REGEX_PARSE_DEPTH:
            raise QueryBudgetExceeded(kind="regex_pattern")


def _count_alternations(pattern: str) -> int:
    count = 0
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        elif ch == "|" and not in_class:
            count += 1
        i += 1
    return count


def _has_stacked_numeric_quantifiers(pattern: str) -> bool:
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "{":
            end = _numeric_quantifier_end(pattern, i)
            if end is not None:
                nxt = end + 1
                if nxt < n and pattern[nxt] == "{" and _numeric_quantifier_end(pattern, nxt) is not None:
                    return True
                i = end + 1
                continue
        i += 1
    return False


def _numeric_quantifier_bounds(pattern: str) -> list[tuple[int, int]]:
    bounds: list[tuple[int, int]] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "{":
            parsed = _parse_numeric_quantifier(pattern, i)
            if parsed is not None:
                lower, upper, end = parsed
                bounds.append((lower, upper))
                i = end + 1
                continue
        i += 1
    return bounds


def _numeric_quantifier_end(pattern: str, start: int) -> int | None:
    parsed = _parse_numeric_quantifier(pattern, start)
    if parsed is None:
        return None
    return parsed[2]


def _parse_numeric_quantifier(pattern: str, start: int) -> tuple[int, int, int] | None:
    if start >= len(pattern) or pattern[start] != "{":
        return None
    i = start + 1
    if i >= len(pattern) or not pattern[i].isdigit():
        return None
    lower = 0
    while i < len(pattern) and pattern[i].isdigit():
        lower = lower * 10 + int(pattern[i])
        i += 1
    upper = lower
    if i < len(pattern) and pattern[i] == ",":
        i += 1
        if i < len(pattern) and pattern[i].isdigit():
            upper = 0
            while i < len(pattern) and pattern[i].isdigit():
                upper = upper * 10 + int(pattern[i])
                i += 1
        else:
            upper = MAX_QUANTIFIER_BOUND + 1
    if i >= len(pattern) or pattern[i] != "}":
        return None
    return lower, upper, i


def _regex_parse_metrics(pattern: str) -> tuple[int, int] | None:
    try:
        parsed = sre_parse.parse(pattern)
    except sre_parse.error:
        return None
    return _walk_sre(parsed)


def _walk_sre(code: list[tuple[object, object]], depth: int = 1) -> tuple[int, int]:
    nodes = 0
    max_depth = depth
    for op, av in code:
        if op is not sre.LITERAL:
            nodes += 1
        child_depth = depth + 1 if op not in (sre.LITERAL, sre.AT) else depth
        if op in (sre.SUBPATTERN, sre.GROUPREF_EXISTS, sre.MAX_REPEAT, sre.MIN_REPEAT):
            sub_nodes, sub_depth = _walk_sre(av[-1], child_depth)
            nodes += sub_nodes
            max_depth = max(max_depth, sub_depth)
        elif op is sre.BRANCH:
            branches = av[1]
            nodes += max(0, len(branches) - 1)
            for branch in branches:
                sub_nodes, sub_depth = _walk_sre(branch, child_depth)
                nodes += sub_nodes
                max_depth = max(max_depth, sub_depth)
        elif op in (sre.ASSERT, sre.ASSERT_NOT):
            sub_nodes, sub_depth = _walk_sre(av[1], child_depth)
            nodes += sub_nodes
            max_depth = max(max_depth, sub_depth)
        elif op is sre.IN:
            nodes += 1
    return nodes, max_depth
