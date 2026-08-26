"""Static regex pattern and query-level limits for public search.

Calibrated in ``docs/issues/security-regex-execution-budget.md`` and
``scripts/regex_limit_survey/``. Enforced on the post-rewrite AST so only
patterns that will actually run as regex are checked (plain literals already
lowered to ``StringValueNode``).

Runtime/engine limits (``backtrack_limit``, request wall clock, SQL timeout) live
elsewhere; this module is parse-time static bounds only.
"""

from __future__ import annotations

import re
import re._constants as sre
import re._parser as sre_parser
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _PatternMetrics:
    nodes: int
    depth: int
    lookarounds: int
    alternations: int
    backreferences: int
    quantifier_bounds: tuple[tuple[int, int], ...]


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

    try:
        parsed = sre_parser.parse(pattern, re.IGNORECASE)
    except re.error:
        # Stacked `{n}{n}`, backrefs the parser rejects, and other ill-formed patterns.
        raise QueryBudgetExceeded(kind="regex_pattern") from None

    metrics = _analyze_pattern(parsed)
    if metrics.backreferences > 0:
        raise QueryBudgetExceeded(kind="regex_pattern")
    if metrics.lookarounds > MAX_LOOKAROUNDS_PER_PATTERN:
        raise QueryBudgetExceeded(kind="regex_pattern")
    if metrics.alternations > MAX_ALTERNATIONS_PER_PATTERN:
        raise QueryBudgetExceeded(kind="regex_pattern")
    for lower, upper in metrics.quantifier_bounds:
        if _explicit_numeric_quantifier_exceeds_bound(lower, upper):
            raise QueryBudgetExceeded(kind="regex_pattern")
    if metrics.nodes > MAX_REGEX_AST_NODES or metrics.depth > MAX_REGEX_PARSE_DEPTH:
        raise QueryBudgetExceeded(kind="regex_pattern")


def _analyze_pattern(code: list[tuple[int, object]], *, depth: int = 1) -> _PatternMetrics:
    nodes = 0
    max_depth = depth
    lookarounds = 0
    alternations = 0
    backreferences = 0
    quantifier_bounds: list[tuple[int, int]] = []

    for op, av in code:
        if op is sre.LITERAL:
            continue
        nodes += 1
        child_depth = depth + 1 if op not in (sre.AT,) else depth
        sub: _PatternMetrics | None = None

        if op in (sre.ASSERT, sre.ASSERT_NOT):
            lookarounds += 1
            sub = _analyze_pattern(av[1], depth=child_depth)
        elif op is sre.BRANCH:
            branches = av[1]
            alternations += max(0, len(branches) - 1)
            sub = _merge_metrics(*(_analyze_pattern(branch, depth=child_depth) for branch in branches))
        elif op is sre.GROUPREF:
            backreferences += 1
        elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT):
            quantifier_bounds.append((av[0], av[1]))
            sub = _analyze_pattern(av[-1], depth=child_depth)
        elif op in (sre.SUBPATTERN, sre.GROUPREF_EXISTS):
            sub = _analyze_pattern(av[-1], depth=child_depth)

        if sub is not None:
            nodes += sub.nodes
            max_depth = max(max_depth, sub.depth)
            lookarounds += sub.lookarounds
            alternations += sub.alternations
            backreferences += sub.backreferences
            quantifier_bounds.extend(sub.quantifier_bounds)

    return _PatternMetrics(
        nodes,
        max_depth,
        lookarounds,
        alternations,
        backreferences,
        tuple(quantifier_bounds),
    )


def _merge_metrics(*metrics: _PatternMetrics) -> _PatternMetrics:
    if not metrics:
        return _PatternMetrics(0, 1, 0, 0, 0, ())
    nodes = sum(m.nodes for m in metrics)
    depth = max(m.depth for m in metrics)
    lookarounds = sum(m.lookarounds for m in metrics)
    alternations = sum(m.alternations for m in metrics)
    backreferences = sum(m.backreferences for m in metrics)
    quantifier_bounds = tuple(bound for m in metrics for bound in m.quantifier_bounds)
    return _PatternMetrics(nodes, depth, lookarounds, alternations, backreferences, quantifier_bounds)


def _explicit_numeric_quantifier_exceeds_bound(lower: int, upper: int) -> bool:
    """True for ``{m}`` / ``{m,n}`` / ``{m,}`` shapes, not for ``*`` / ``+`` / ``?``."""
    if upper == sre.MAXREPEAT:
        # ``{m,}`` with m > 1 is an explicit numeric unbounded quantifier; ``*``/``+`` are not.
        return lower > 1
    return lower > MAX_QUANTIFIER_BOUND or upper > MAX_QUANTIFIER_BOUND
