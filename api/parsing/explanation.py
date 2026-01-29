"""Generate human-readable explanations from query AST nodes."""

from __future__ import annotations

from api.parsing.card_query_nodes import CardAttributeNode
from api.parsing.db_info import (
    ALIAS_TO_FIELD_INFOS,
    COLOR_CODE_TO_NAME,
    FORMAT_CODE_TO_NAME,
)
from api.parsing.nodes import (
    AndNode,
    BinaryOperatorNode,
    ManaValueNode,
    NotNode,
    NumericValueNode,
    OrNode,
    Query,
    QueryNode,
    RegexValueNode,
    StringValueNode,
)


def explain_query(query_node: QueryNode) -> str:
    """Generate a human-readable explanation of a query AST.

    Args:
        query_node: The root query node to explain.

    Returns:
        A human-readable string explaining the query.
    """
    if isinstance(query_node, Query):
        return explain_query(query_node.root)

    if isinstance(query_node, AndNode):
        if not query_node.operands:
            return ""
        if len(query_node.operands) == 1:
            return explain_query(query_node.operands[0])
        parts = [explain_query(op) for op in query_node.operands]
        return " and ".join(parts)

    if isinstance(query_node, OrNode):
        if not query_node.operands:
            return ""
        if len(query_node.operands) == 1:
            return explain_query(query_node.operands[0])
        parts = [explain_query(op) for op in query_node.operands]
        return f"({' or '.join(parts)})"

    if isinstance(query_node, NotNode):
        operand_explanation = explain_query(query_node.operand)
        return f"not ({operand_explanation})"

    if isinstance(query_node, BinaryOperatorNode):
        return _explain_binary_operator(query_node)

    # For other node types, return a generic representation
    return str(query_node)


def _explain_binary_operator(node: BinaryOperatorNode) -> str:
    """Explain a binary operator node.

    Args:
        node: The binary operator node to explain.

    Returns:
        A human-readable explanation of the binary operator.
    """
    # Get left side explanation
    if isinstance(node.lhs, CardAttributeNode):
        lhs_str = _explain_attribute(node.lhs)
    elif isinstance(node.lhs, (NumericValueNode, StringValueNode, ManaValueNode, RegexValueNode)):
        lhs_str = str(node.lhs.value)
    else:
        lhs_str = explain_query(node.lhs)

    # Get right side explanation
    if isinstance(node.rhs, CardAttributeNode):
        rhs_str = _explain_attribute(node.rhs)
    elif isinstance(node.rhs, StringValueNode):
        # Check if the value is empty
        if not node.rhs.value.strip():
            return ""
        rhs_str = _explain_value(node.rhs, node.lhs)
    elif isinstance(node.rhs, (NumericValueNode, ManaValueNode, RegexValueNode)):
        rhs_str = str(node.rhs.value)
    elif isinstance(node.rhs, str):
        # Handle string rhs (for empty queries)
        if not node.rhs.strip():
            return ""
        rhs_str = node.rhs
    else:
        rhs_str = explain_query(node.rhs)

    # Get operator explanation
    operator_str = _explain_operator(node.operator)

    # Special case for attribute-value pairs that read naturally
    if isinstance(node.lhs, CardAttributeNode):
        db_column_name = node.lhs.attribute_name.lower()
        # Special formatting for certain attributes
        if db_column_name == "card_color_identity":
            if node.operator in ("=", ":"):
                return f"the color identity is {rhs_str}"
        elif db_column_name == "card_legalities":
            if node.operator in ("=", ":"):
                return f"it's legal in {rhs_str}"
        elif db_column_name == "card_colors":
            if node.operator in ("=", ":"):
                return f"the color is {rhs_str}"
        elif db_column_name == "creature_power":
            return f"the power {operator_str} {rhs_str}"
        elif db_column_name == "creature_toughness":
            return f"the toughness {operator_str} {rhs_str}"
        elif db_column_name == "cmc":
            return f"the mana value {operator_str} {rhs_str}"
        elif db_column_name == "card_name":
            if node.operator in (":", "="):
                return f"the name contains {rhs_str}"
        elif db_column_name == "oracle_text":
            if node.operator in (":", "="):
                return f"the oracle text contains {rhs_str}"
        elif db_column_name == "card_types":
            if node.operator in (":", "="):
                return f"the type contains {rhs_str}"
        elif db_column_name == "card_rarity_int":
            return f"the rarity {operator_str} {rhs_str}"
        elif db_column_name == "card_artist":
            if node.operator in (":", "="):
                return f"the artist contains {rhs_str}"
        elif db_column_name == "card_set_code":
            if node.operator in (":", "="):
                return f"the set contains {rhs_str}"

    # Default format: lhs operator rhs
    return f"{lhs_str} {operator_str} {rhs_str}"


def _explain_attribute(attr_node: CardAttributeNode) -> str:
    """Explain an attribute node.

    Args:
        attr_node: The attribute node to explain.

    Returns:
        A human-readable name for the attribute.
    """
    db_column_name = attr_node.attribute_name.lower()

    # Map database column names to readable names
    name_map = {
        "cmc": "mana value",
        "creature_power": "power",
        "creature_toughness": "toughness",
        "card_color_identity": "color identity",
        "card_colors": "color",
        "card_name": "name",
        "oracle_text": "oracle text",
        "card_types": "type",
        "card_subtypes": "subtype",
        "card_rarity_int": "rarity",
        "card_legalities": "format",
        "card_artist": "artist",
        "card_set_code": "set",
        "mana_cost_jsonb": "mana cost",
        "planeswalker_loyalty": "loyalty",
        "type_line": "type line",
        "flavor_text": "flavor text",
        "card_keywords": "keyword",
        "card_layout": "layout",
        "card_border": "border",
        "card_watermark": "watermark",
        "released_at": "release date",
        "collector_number": "collector number",
        "price_usd": "price (USD)",
        "price_eur": "price (EUR)",
        "price_tix": "price (TIX)",
        "edhrec_rank": "EDHREC rank",
    }

    return name_map.get(db_column_name, db_column_name.replace("_", " "))


def _explain_operator(operator: str) -> str:
    """Explain an operator.

    Args:
        operator: The operator to explain.

    Returns:
        A human-readable version of the operator.
    """
    operator_map = {
        "=": "is",
        "!=": "is not",
        ">": ">",
        "<": "<",
        ">=": "≥",
        "<=": "≤",
        ":": "contains",
        "+": "+",
        "-": "-",
        "*": "×",
        "/": "÷",
    }
    return operator_map.get(operator, operator)


def _explain_value(value_node: StringValueNode, context_node: QueryNode | None = None) -> str:
    """Explain a value node, potentially expanding codes based on context.

    Args:
        value_node: The value node to explain.
        context_node: The context node (e.g., the attribute being compared to).

    Returns:
        A human-readable version of the value.
    """
    value = value_node.value.strip()

    # If context is a color-related attribute, try to expand color codes
    if isinstance(context_node, CardAttributeNode):
        db_column_name = context_node.attribute_name.lower()
        if db_column_name in ("card_colors", "card_color_identity"):
            # Try to expand single-letter color codes
            if len(value) == 1 and value.lower() in COLOR_CODE_TO_NAME:
                return COLOR_CODE_TO_NAME[value.lower()].capitalize()
            # Try to expand multi-letter color codes (e.g., "ug" -> "Blue/Green")
            if len(value) <= 5 and all(c.lower() in COLOR_CODE_TO_NAME for c in value):
                color_names = [COLOR_CODE_TO_NAME[c.lower()].capitalize() for c in value.lower()]
                return "/".join(color_names)

        # If context is a format-related attribute, try to expand format codes
        if db_column_name == "card_legalities":
            if value.lower() in FORMAT_CODE_TO_NAME:
                return FORMAT_CODE_TO_NAME[value.lower()].capitalize()

    return value
