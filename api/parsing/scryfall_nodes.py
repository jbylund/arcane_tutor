from __future__ import annotations

from .db_info import (
    CARD_SUPERTYPES,
    CARD_TYPES,
    COLOR_CODE_TO_NAME,
    COLOR_NAME_TO_CODE,
    DB_NAME_TO_FIELD_TYPE,
    SEARCH_NAME_TO_DB_NAME,
    FieldType,
)
from .nodes import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    NotNode,
    OrNode,
    Query,
    QueryNode,
    StringValueNode,
    param_name,
)

"""

# equality is the one where order not mattering is nice
# because otherwise it's all of a in b and all of b in a
color = query
color = query # as object
color ?& query and query ?& color # as array

color >= query
color @> query # as object
color ?& query # as array

color <= query
color <@ query # as object
query ?& color # as array

color > query
color @> query AND color <> query # as object
color ?& query AND not(query ?& color) # as array

color < query
color @> query AND color <> query # as object
query ?& color AND not(color ?& query) # as array
"""


def get_field_type(attr: str) -> str:
    return DB_NAME_TO_FIELD_TYPE.get(attr, FieldType.TEXT)


class ScryfallAttributeNode(AttributeNode):
    def __init__(self: ScryfallAttributeNode, attribute_name: str) -> None:
        db_column_name = SEARCH_NAME_TO_DB_NAME.get(attribute_name, attribute_name)
        super().__init__(db_column_name)

    def to_sql(self: ScryfallAttributeNode, context: dict) -> str:
        remapped = SEARCH_NAME_TO_DB_NAME.get(self.attribute_name, self.attribute_name)
        return f"card.{remapped}"


def get_colors_comparison_object(val: str) -> dict[str, bool]:
    # If all chars are color codes
    color_code_set = set(COLOR_CODE_TO_NAME)
    if val and set(val) <= color_code_set:
        return {c.upper(): True for c in val}
    # If it's a color name (e.g. 'red', 'blue', etc.)
    try:
        letter_code = COLOR_NAME_TO_CODE[val]
        return {letter_code.upper(): True}
    except KeyError:
        msg = f"Invalid color string: {val}"
        raise ValueError(msg)


def get_keywords_comparison_object(val: str) -> dict[str, bool]:
    # Normalize the input keyword
    normalized_keyword = val.strip().title()
    return {normalized_keyword: True}


class ScryfallBinaryOperatorNode(BinaryOperatorNode):
    def to_sql(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        if isinstance(self.lhs, ScryfallAttributeNode):
            lhs_sql = self.lhs.to_sql(context)
            attr = self.lhs.attribute_name
            field_type = get_field_type(attr)

            # handle numeric
            if field_type == FieldType.NUMERIC:
                if self.operator == ":":
                    self.operator = "="
                return super().to_sql(context)

            if field_type == FieldType.JSONB_OBJECT:
                return self._handle_jsonb_object(context)

            if field_type == FieldType.JSONB_ARRAY:
                return self._handle_jsonb_array(context)

            if self.operator == ":":
                if field_type == FieldType.TEXT:
                    if isinstance(self.rhs, StringValueNode):
                        txt_val = self.rhs.value.strip()
                    elif isinstance(self.rhs, str):
                        txt_val = self.rhs.strip()
                    else:
                        msg = f"Unknown type: {type(self.rhs)}, {locals()}"
                        raise TypeError(msg)
                    words = ["", *txt_val.split(), ""]
                    pattern = "%".join(words)
                    _param_name = param_name(pattern)
                    context[_param_name] = pattern
                    return f"({lhs_sql} ILIKE %({_param_name})s)"
                msg = f"Unknown field type: {field_type}"
                raise NotImplementedError(msg)
                # # Fallback: treat as string search
                # value = self.rhs.to_sql(context).strip("'")
                # return f"({lhs_sql} ILIKE '%%{value}%%')"

        # Fallback: use default logic
        return super().to_sql(context)

    """
    col = query
    col = query # as object
    col ?& query and query ?& col # as array

    col >= query
    col @> query # as object
    col ?& query # as array

    col <= query
    col <@ query # as object
    query ?& col # as array

    col > query
    col @> query AND col <> query # as object
    col ?& query AND not(query ?& col) # as array

    col < query
    col @> query AND col <> query # as object
    query ?& col AND not(col ?& query) # as array
    """

    def _handle_jsonb_object(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        # Produce the query as a jsonb object
        lhs_sql = self.lhs.to_sql(context)
        attr = self.lhs.attribute_name
        if attr in ("card_colors", "card_color_identity"):
            rhs = get_colors_comparison_object(self.rhs.value.strip().lower())
            pname = param_name(rhs)
            context[pname] = rhs
            # query = json.dumps(rhs, sort_keys=True) + "::jsonb"
        elif attr == "card_keywords":
            rhs = get_keywords_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs

        # Color identity has inverted semantics for the : operator only
        is_color_identity = attr == "card_color_identity"

        if self.operator == "=":
            return f"({lhs_sql} = %({pname})s)"
        if self.operator in (">=", ":"):
            # For color identity, : should behave like <=, but >= should still be >=
            if is_color_identity and self.operator == ":":
                return f"({lhs_sql} <@ %({pname})s)"
            return f"({lhs_sql} @> %({pname})s)"
        if self.operator == "<=":
            return f"({lhs_sql} <@ %({pname})s)"
        if self.operator == ">":
            return f"({lhs_sql} @> %({pname})s AND {lhs_sql} <> %({pname})s)"
        if self.operator == "<":
            return f"({lhs_sql} <@ %({pname})s AND {lhs_sql} <> %({pname})s)"
        if self.operator == "!=":
            return f"({lhs_sql} <> %({pname})s)"
        if self.operator == "<>":
            return f"({lhs_sql} <> %({pname})s)"
        msg = f"Unknown operator: {self.operator}"
        raise ValueError(msg)

    def _handle_jsonb_array(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        # TODO: this should produce the query as an array, not jsonb
        rhs_val = self.rhs.value.strip().title()
        if self.lhs.attribute_name.lower() in ("card_types", "card_subtypes", "type"):
            if rhs_val in CARD_SUPERTYPES | CARD_TYPES:
                self.lhs.attribute_name = "card_types"
            else:
                self.lhs.attribute_name = "card_subtypes"
        col = self.lhs.to_sql(context)

        inners = [rhs_val]
        pname = param_name(inners)
        context[pname] = inners
        query = f"%({pname})s"
        if self.operator == "=":
            return f"({col} <@ {query}) AND ({query} <@ {col})"
        if self.operator in (">=", ":"):
            return f"({query} <@ {col})"
        if self.operator == "<=":
            return f"({col} <@ {query})"
        if self.operator == ">":
            return f"({query} <@ {col}) AND NOT({col} <@ {query})"
        # if self.operator == "<": - this type of search doesn't make sense
        #     return f"({query} <@ {col}) AND NOT({col} @> {query})"
        msg = f"Unknown operator: {self.operator}"
        raise ValueError(msg)


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
