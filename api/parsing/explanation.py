"""Generate human-readable explanations from query AST nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.parsing.nodes import QueryNode


def explain_query(query_node: QueryNode) -> str:
    """Generate a human-readable explanation of a query AST.

    Args:
        query_node: The root query node to explain.

    Returns:
        A human-readable string explaining the query.
    """
    return query_node.to_human_explanation()
