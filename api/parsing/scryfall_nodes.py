from __future__ import annotations

import json

from .nodes import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    NotNode,
    OrNode,
    Query,
    QueryNode,
    StringValueNode,
)

# Field type mapping for Scryfall
FIELD_TYPE_MAP = {
    "card_subtypes": "array",
    "card_types": "array",
    "cmc": "numeric",
    "colors": "array",
    "creature_power": "numeric",
    "creature_toughness": "numeric",
    "mana_cost_text": "text",
    "mana_cost": "text",
    "name": "text",
    "oracle": "text",
    "power": "numeric",
    "toughness": "numeric",
    "type": "text",
}

REMAPPER = {
    "name": "card_name",
    "power": "creature_power",
    "toughness": "creature_toughness",
}

COLOR_CODE_TO_NAME = {
    "b": "black",
    "c": "colorless",
    "g": "green",
    "r": "red",
    "u": "blue",
    "w": "white",
}

COLOR_NAME_TO_CODE = {v: k for k, v in COLOR_CODE_TO_NAME.items()}


def get_field_type(attr: str) -> str:
    return FIELD_TYPE_MAP.get(attr, "text")

def get_sql_column(attr: str) -> str:
    remapped_name = REMAPPER.get(attr, attr)
    return f"card.{remapped_name}"

class ScryfallAttributeNode(AttributeNode):
    def to_sql(self: ScryfallAttributeNode) -> str:
        remapped = REMAPPER.get(self.attribute_name, self.attribute_name)
        return f"card.{remapped}"

def get_colors_comparison_object(val: str) -> dict[str, bool]:
    # If all chars are color codes
    color_code_set = set(COLOR_CODE_TO_NAME.keys())
    if val and set(val) <= color_code_set:
        return {c.upper(): True for c in val}
    # If it's a color name (e.g. 'red', 'blue', etc.)
    try:
        letter_code = COLOR_NAME_TO_CODE[val]
        return {letter_code.upper(): True}
    except KeyError:
        msg = f"Invalid color string: {val}"
        raise ValueError(msg)

class ScryfallBinaryOperatorNode(BinaryOperatorNode):
    def to_sql(self: ScryfallBinaryOperatorNode) -> str:
        if isinstance(self.lhs, ScryfallAttributeNode):
            lhs_sql = self.lhs.to_sql()
            attr = self.lhs.attribute_name
            field_type = get_field_type(attr)

            # handle numeric
            if field_type == "numeric":
                if self.operator == ":":
                    self.operator = "="
                return super().to_sql()

            if field_type == "array" and isinstance(self.rhs, StringValueNode):
                if attr == "colors":
                    rhs = get_colors_comparison_object(self.rhs.value.strip().lower())
                    rhs_json = json.dumps(rhs, sort_keys=True)
                    if self.operator in (":", ">="):
                        return f"({lhs_sql} @> '{rhs_json}'::jsonb)"
                    elif self.operator == "<=":
                        return f"({lhs_sql} <@ '{rhs_json}'::jsonb)"
                    elif self.operator == "=":
                        return f"({lhs_sql} = '{rhs_json}'::jsonb)"
                    elif self.operator == ">":
                        return f"({lhs_sql} @> '{rhs_json}'::jsonb AND {lhs_sql} <> '{rhs_json}'::jsonb)"
                    elif self.operator == "<":
                        return f"({lhs_sql} <@ '{rhs_json}'::jsonb AND {lhs_sql} <> '{rhs_json}'::jsonb)"
                    else:
                        msg = f"Unknown operator: {self.operator}"
                        raise ValueError(msg)
                # fallback for other array fields (still as array)
                return f"({lhs_sql} @> '[\"{self.rhs.value}\"]'::jsonb)"

            if self.operator == ":":
                if field_type == "text" and isinstance(self.rhs, StringValueNode):
                    words = ["", *self.rhs.value.strip().split(), ""]
                    pattern = "%".join(words)
                    return f"({lhs_sql} ILIKE '{pattern}')"
                # Fallback: treat as string search
                value = self.rhs.to_sql().strip("'")
                return f"({lhs_sql} ILIKE '%{value}%')"

        # Fallback: use default logic
        return super().to_sql()

def to_scryfall_ast(node: QueryNode) -> QueryNode:
    if isinstance(node, BinaryOperatorNode):
        return ScryfallBinaryOperatorNode(
            to_scryfall_ast(node.lhs),
            node.operator,
            to_scryfall_ast(node.rhs),
        )
    if isinstance(node, AttributeNode):
        return ScryfallAttributeNode(node.attribute_name)
    if isinstance(node, AndNode):
        return AndNode([to_scryfall_ast(op) for op in node.operands])
    if isinstance(node, OrNode):
        return OrNode([to_scryfall_ast(op) for op in node.operands])
    if isinstance(node, NotNode):
        return NotNode(to_scryfall_ast(node.operand))
    if isinstance(node, Query):
        return Query(to_scryfall_ast(node.root))
    return node
