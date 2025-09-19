"""Scryfall-specific AST nodes and query processing."""

from __future__ import annotations

from .db_info import (
    CARD_SUPERTYPES,
    CARD_TYPES,
    COLOR_CODE_TO_NAME,
    COLOR_NAME_TO_CODE,
    DB_NAME_TO_FIELD_TYPE,
    RARITY_FIELDS,
    RARITY_TEXT_TO_NUMERIC,
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
    """Get the field type for a given attribute name.

    Args:
        attr: The attribute name to look up.

    Returns:
        The field type for the attribute, or TEXT if not found.
    """
    return DB_NAME_TO_FIELD_TYPE.get(attr, FieldType.TEXT)


class ScryfallAttributeNode(AttributeNode):
    """Scryfall-specific attribute node with field mapping."""

    def __init__(self: ScryfallAttributeNode, attribute_name: str) -> None:
        """Initialize a Scryfall attribute node.

        Args:
            attribute_name: The search attribute name to map to database column.
        """
        db_column_name = SEARCH_NAME_TO_DB_NAME.get(attribute_name.lower(), attribute_name)
        super().__init__(db_column_name)

    def to_sql(self: ScryfallAttributeNode, context: dict) -> str:
        """Generate SQL for Scryfall attribute node.

        Args:
            context: SQL parameter context.

        Returns:
            SQL string for the attribute reference.
        """
        del context
        remapped = SEARCH_NAME_TO_DB_NAME.get(self.attribute_name, self.attribute_name)
        return f"card.{remapped}"


def get_colors_comparison_object(val: str) -> dict[str, bool]:
    """Convert color string to comparison object for database queries.

    Args:
        val: Color string (either color codes like 'WUBRG' or color name like 'red').

    Returns:
        Dictionary mapping color codes to True for matching colors.

    Raises:
        ValueError: If the color string is invalid.
    """
    # If all chars are color codes
    color_code_set = set(COLOR_CODE_TO_NAME)
    if val and set(val) <= color_code_set:
        return {c.upper(): True for c in val}
    # If it's a color name (e.g. 'red', 'blue', etc.)
    try:
        letter_code = COLOR_NAME_TO_CODE[val]
        return {letter_code.upper(): True}
    except KeyError as e:
        msg = f"Invalid color string: {val}"
        raise ValueError(msg) from e


def get_keywords_comparison_object(val: str) -> dict[str, bool]:
    """Convert keyword string to comparison object for database queries.

    Args:
        val: Keyword string to normalize.

    Returns:
        Dictionary mapping normalized keyword to True.
    """
    # Normalize the input keyword
    normalized_keyword = val.strip().title()
    return {normalized_keyword: True}


def get_oracle_tags_comparison_object(val: str) -> dict[str, bool]:
    """Convert oracle tag string to comparison object for database queries.

    Args:
        val: Oracle tag string to normalize.

    Returns:
        Dictionary mapping normalized oracle tag to True.
    """
    # Oracle tags are stored in lowercase
    normalized_tag = val.strip().lower()
    return {normalized_tag: True}


class ScryfallBinaryOperatorNode(BinaryOperatorNode):
    """Scryfall-specific binary operator node with custom SQL generation."""

    def to_sql(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Generate SQL for Scryfall-specific binary operations.

        Args:
            context: SQL parameter context (unused).

        Returns:
            SQL string for the binary operation.
        """
        if isinstance(self.lhs, ScryfallAttributeNode):
            attr = self.lhs.attribute_name
            field_type = get_field_type(attr)

            # handle numeric
            if field_type == FieldType.NUMERIC:
                if self.operator == ":":
                    self.operator = "="
                return super().to_sql(context)

            # Special handling for rarity fields
            if attr in RARITY_FIELDS or attr.lower() in RARITY_FIELDS:
                return self._handle_rarity_field(context)

            if field_type == FieldType.JSONB_OBJECT:
                return self._handle_jsonb_object(context)

            if field_type == FieldType.JSONB_ARRAY:
                return self._handle_jsonb_array(context)

            if self.operator == ":":
                if field_type == FieldType.TEXT:
                    return self._handle_text_field(context)
                msg = f"Unknown field type: {field_type}"
                raise NotImplementedError(msg)

        # Fallback: use default logic
        return super().to_sql(context)

    def _handle_text_field(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle text field queries with ILIKE pattern matching."""
        lhs_sql = self.lhs.to_sql(context)

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

    def _handle_jsonb_object(self: ScryfallBinaryOperatorNode, context: dict) -> str:  # noqa: PLR0911, C901
        # Produce the query as a jsonb object
        lhs_sql = self.lhs.to_sql(context)
        attr = self.lhs.attribute_name
        is_color_identity = False
        if attr in ("card_colors", "card_color_identity"):
            rhs = get_colors_comparison_object(self.rhs.value.strip().lower())
            pname = param_name(rhs)
            context[pname] = rhs
            # Color identity has inverted semantics for the : operator only
            is_color_identity = attr == "card_color_identity"
        elif attr == "card_keywords":
            rhs = get_keywords_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs
        elif attr == "card_oracle_tags":
            # Oracle tags are stored in lowercase, unlike keywords
            rhs = get_oracle_tags_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs
        else:
            msg = f"Unknown attribute: {attr}"
            raise ValueError(msg)

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
        if self.operator in ("!=", "<>"):
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
        msg = f"Unknown operator: {self.operator}"
        raise ValueError(msg)

    def _handle_rarity_field(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle rarity field queries with special text/numeric handling.

        For ':' operator: use text column with exact match
        For comparison operators (>, <, >=, <=): use numeric column
        For = and !=: use text column with exact match

        Args:
            context: SQL parameter context

        Returns:
            SQL string for the rarity query
        """
        if isinstance(self.rhs, StringValueNode):
            rhs_val = self.rhs.value.strip().lower()
        elif isinstance(self.rhs, str):
            rhs_val = self.rhs.strip().lower()
        else:
            msg = f"Rarity value must be string, got {type(self.rhs)}"
            raise TypeError(msg)

        # Validate rarity value
        if rhs_val not in RARITY_TEXT_TO_NUMERIC:
            # For unknown rarity values, still allow the query but it may return no results
            pass

        # For text-based operations (:, =, !=), use text column
        if self.operator in (":", "=", "!=", "<>"):
            text_col = "card.card_rarity_text"
            param_name_str = param_name(rhs_val)
            context[param_name_str] = rhs_val

            if self.operator == ":":
                # For : operator, use exact match (case-insensitive)
                return f"(LOWER({text_col}) = %({param_name_str})s)"
            if self.operator == "=":
                return f"(LOWER({text_col}) = %({param_name_str})s)"
            if self.operator in ("!=", "<>"):
                return f"(LOWER({text_col}) != %({param_name_str})s)"

        # For comparison operations (>, <, >=, <=), use numeric column
        elif self.operator in (">", "<", ">=", "<="):
            numeric_col = "card.card_rarity_numeric"
            if rhs_val not in RARITY_TEXT_TO_NUMERIC:
                # If rarity is not recognized, return a condition that won't match
                return "(FALSE)"

            numeric_val = RARITY_TEXT_TO_NUMERIC[rhs_val]
            param_name_num = param_name(numeric_val)
            context[param_name_num] = numeric_val

            return f"({numeric_col} {self.operator} %({param_name_num})s)"

        msg = f"Unknown operator for rarity field: {self.operator}"
        raise ValueError(msg)


def to_scryfall_ast(node: QueryNode) -> QueryNode:  # noqa: PLR0911
    """Convert a generic query node to a Scryfall-specific AST node.

    Args:
        node: The query node to convert.

    Returns:
        The corresponding Scryfall-specific node.
    """
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
