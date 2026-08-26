"""Measured complexity bounds for public search queries."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

from api.parsing.nodes import AndNode, BinaryOperatorNode, NotNode, OrNode, Query

if TYPE_CHECKING:
    from collections.abc import Mapping

    from api.parsing.nodes import QueryNode

MAX_QUERY_UTF8_BYTES = 1024
MAX_QUERY_TOKENS = 256
MAX_AST_NODES = 128
MAX_BOOLEAN_CLAUSES = 32
MAX_GROUP_DEPTH = 32
MAX_QUERY_LOG_PREVIEW_CHARS = 80

QUERY_TOO_LONG_MESSAGE = "Search query exceeds the maximum allowed length."
QUERY_TOO_COMPLEX_MESSAGE = "Search query exceeds the maximum allowed complexity."


class QueryBudgetExceeded(ValueError):  # noqa: N818
    """Raised when a query exceeds a measured public complexity bound."""

    def __init__(self, *, kind: Literal["length", "complexity"]) -> None:
        """Initialize with a stable, non-disclosing user message."""
        self.kind = kind
        self.user_message = QUERY_TOO_LONG_MESSAGE if kind == "length" else QUERY_TOO_COMPLEX_MESSAGE
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


def _measure_node(node: QueryNode, *, depth: int) -> tuple[int, int, int]:
    """Return ``(node_count, boolean_clauses, max_depth)`` for *node*."""
    node_count = 1
    boolean_clauses = 0
    max_depth = depth

    if isinstance(node, Query):
        child_nodes, child_clauses, child_depth = _measure_node(node.root, depth=depth + 1)
        return node_count + child_nodes, boolean_clauses + child_clauses, max(max_depth, child_depth)
    if isinstance(node, (AndNode, OrNode)):
        boolean_clauses += len(node.operands)
        for operand in node.operands:
            child_nodes, child_clauses, child_depth = _measure_node(operand, depth=depth + 1)
            node_count += child_nodes
            boolean_clauses += child_clauses
            max_depth = max(max_depth, child_depth)
        return node_count, boolean_clauses, max_depth
    if isinstance(node, NotNode):
        child_nodes, child_clauses, child_depth = _measure_node(node.operand, depth=depth + 1)
        return node_count + child_nodes, boolean_clauses + child_clauses, max(max_depth, child_depth)
    if isinstance(node, BinaryOperatorNode):
        lhs_nodes, lhs_clauses, lhs_depth = _measure_node(node.lhs, depth=depth + 1)
        rhs_nodes, rhs_clauses, rhs_depth = _measure_node(node.rhs, depth=depth + 1)
        return (
            node_count + lhs_nodes + rhs_nodes,
            boolean_clauses + lhs_clauses + rhs_clauses,
            max(max_depth, lhs_depth, rhs_depth),
        )
    return node_count, boolean_clauses, max_depth


def check_ast_budget(query: Query) -> None:
    """Reject *query* when its AST exceeds node, clause, or depth limits."""
    node_count, boolean_clauses, max_depth = _measure_node(query, depth=1)
    if node_count > MAX_AST_NODES or boolean_clauses > MAX_BOOLEAN_CLAUSES or max_depth > MAX_GROUP_DEPTH:
        raise QueryBudgetExceeded(kind="complexity")
