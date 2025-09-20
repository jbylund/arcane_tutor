"""Scryfall-specific AST nodes and query processing."""

import re

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
    NumericValueNode,
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


# Rarity ordering for comparison operations
RARITY_TO_NUMBER = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
    "mythic": 3,
    "special": 4,
    "bonus": 5,
}


def get_rarity_number(rarity: str) -> int:
    """Convert rarity string to numeric value for comparison.

    Args:
        rarity: The rarity string (case-insensitive).

    Returns:
        Numeric value for the rarity.

    Raises:
        ValueError: If the rarity is not recognized.
    """
    rarity_lower = rarity.lower().strip()
    int_val = RARITY_TO_NUMBER.get(rarity_lower)
    if int_val is None:
        valid_rarities = str(tuple(RARITY_TO_NUMBER.keys()))
        msg = f"Unknown rarity: {rarity}. Valid rarities are: {valid_rarities}"
        raise ValueError(msg)
    return int_val


class ScryfallAttributeNode(AttributeNode):
    """Scryfall-specific attribute node with field mapping."""

    def __init__(self, attribute_name: str) -> None:
        """Initialize a Scryfall attribute node.

        Args:
            attribute_name: The search attribute name to map to database column.
        """
        # Preserve original attribute name BEFORE mapping for specialized handling
        self.original_attribute = attribute_name.lower()
        db_column_name = SEARCH_NAME_TO_DB_NAME.get(attribute_name.lower(), attribute_name)
        super().__init__(db_column_name)

    def to_sql(self, context: dict) -> str:
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


def get_legality_comparison_object(val: str, attr: str) -> dict[str, str]:
    """Convert legality search to comparison object for database queries.

    Args:
        val: Format name to search for.
        attr: The search attribute name (format, legal, banned, restricted).

    Returns:
        Dictionary mapping format to legality status.
    """
    # Normalize format name to lowercase
    format_name = val.strip().lower()

    # Map search attribute to legality status
    if attr in ("format", "f", "legal"):
        status = "legal"
    elif attr == "banned":
        status = "banned"  # Scryfall uses "banned" for banned cards
    elif attr == "restricted":
        status = "restricted"
    else:
        msg = f"Unknown legality attribute: {attr}"
        raise ValueError(msg)

    return {format_name: status}


def parse_mana_cost_string(mana_cost: str) -> dict[str, list[int]]:
    """Parse a mana cost string into JSONB representation for database queries.
    
    Args:
        mana_cost: Mana cost string like "{2}{G}", "{W/U}", "G", etc.
    
    Returns:
        Dictionary mapping mana symbols to lists of valid counts.
        
    Examples:
        "{2}{G}" -> {"{1}": [1, 2], "{G}": [1]}
        "{W/U}" -> {"{W}": [1], "{U}": [1]} (hybrid mana can be paid with either)
        "G" -> {"{G}": [1]} (shorthand for {G})
        "{2/G}" -> {"{1}": [1, 2], "{G}": [1]} (can be paid with 2 generic or 1 green)
    """
    result = {}
    
    # First, normalize shorthand mana symbols (e.g., "G" -> "{G}")
    # But preserve complex symbols like "{W/U}" that are already in braces
    normalized_cost = mana_cost
    
    # Find all mana symbols (both braced and shorthand)
    # Pattern matches: {anything} or single letters WUBRG/C/X/Y/Z/etc outside braces
    symbol_pattern = r'(\{[^}]+\})|([WUBRGCXYZ])'
    
    symbols = []
    for match in re.finditer(symbol_pattern, normalized_cost):
        if match.group(1):  # Braced symbol like {2}, {G}, {W/U}
            symbols.append(match.group(1))
        elif match.group(2):  # Shorthand symbol like G, W, U
            symbols.append(f"{{{match.group(2)}}}")
    
    # Process each symbol
    symbol_counts = {}
    for symbol in symbols:
        if symbol not in symbol_counts:
            symbol_counts[symbol] = 0
        symbol_counts[symbol] += 1
    
    # Convert counts to the JSONB format
    for symbol, count in symbol_counts.items():
        if '/' in symbol:
            # Hybrid mana symbol like {W/U}, {2/G}, {G/P}
            parts = symbol[1:-1].split('/')  # Remove braces and split
            for part in parts:
                if part.isdigit():
                    # Handle {2/G} style - the number becomes generic mana requirement
                    num = int(part)
                    key = "{1}"
                    if key not in result:
                        result[key] = []
                    for i in range(1, num + 1):
                        if i not in result[key]:
                            result[key].append(i)
                else:
                    # Regular color part like W, U, G, P
                    key = f"{{{part}}}"
                    if key not in result:
                        result[key] = []
                    for i in range(1, count + 1):
                        if i not in result[key]:
                            result[key].append(i)
        elif symbol[1:-1].isdigit():
            # Generic mana cost like {2}, {5}
            num = int(symbol[1:-1])
            # This represents needing 'num' generic mana, so {1} can be 1, 2, ..., num
            key = "{1}"
            if key not in result:
                result[key] = []
            for i in range(1, num + 1):
                if i not in result[key]:
                    result[key].append(i)
        else:
            # Colored mana symbol like {G}, {W}, {P}
            if symbol not in result:
                result[symbol] = []
            for i in range(1, count + 1):
                if i not in result[symbol]:
                    result[symbol].append(i)
    
    # Sort the lists for consistent representation
    for key in result:
        result[key].sort()
    
    return result


def get_mana_cost_comparison_object(val: str) -> dict[str, list[int]]:
    """Convert mana cost string to comparison object for database queries.

    Args:
        val: Mana cost string to parse.

    Returns:
        Dictionary mapping mana symbols to valid count lists.
    """
    return parse_mana_cost_string(val.strip())


class ScryfallBinaryOperatorNode(BinaryOperatorNode):
    """Scryfall-specific binary operator node with custom SQL generation."""

    def to_sql(self, context: dict) -> str:
        """Generate SQL for Scryfall-specific binary operations.

        Args:
            context: SQL parameter context (unused).

        Returns:
            SQL string for the binary operation.
        """
        if isinstance(self.lhs, ScryfallAttributeNode):
            return self._handle_scryfall_attribute(context)

        # Fallback: use default logic
        return super().to_sql(context)

    def _handle_scryfall_attribute(self, context: dict) -> str:
        """Handle Scryfall attribute-specific SQL generation."""
        attr = self.lhs.attribute_name

        # Special routing for collector numbers based on operator
        if attr == "collector_number":
            return self._handle_collector_number(context)

        lhs_sql = self.lhs.to_sql(context)
        field_type = get_field_type(attr)

        # handle numeric
        if field_type == FieldType.NUMERIC:
            if self.operator == ":":
                self.operator = "="

            # Special handling for rarity - convert text values to numeric
            if attr == "card_rarity_int" and isinstance(self.rhs, StringValueNode):
                try:
                    rarity_number = get_rarity_number(self.rhs.value)
                    # Replace the string value with the numeric value
                    self.rhs = NumericValueNode(rarity_number)
                except ValueError as e:
                    # Re-raise with more context
                    msg = f"Invalid rarity in comparison: {e}"
                    raise ValueError(msg) from e

            return super().to_sql(context)

        if field_type == FieldType.JSONB_OBJECT:
            return self._handle_jsonb_object(context)

        if field_type == FieldType.JSONB_ARRAY:
            return self._handle_jsonb_array(context)

        if self.operator == ":":
            return self._handle_colon_operator(context, field_type, lhs_sql, attr)

        msg = f"Unknown field type: {field_type}"
        raise NotImplementedError(msg)

    def _handle_colon_operator(self, context: dict, field_type: str, lhs_sql: str, attr: str) -> str:
        """Handle colon operator for different field types."""
        if field_type == FieldType.TEXT:
            # Handle set codes specially - use exact matching instead of pattern matching
            if attr == "card_set_code":
                if self.operator == ":":
                    self.operator = "="
                return super().to_sql(context)

            # Regular text field handling with pattern matching
            return self._handle_text_field_pattern_matching(context, lhs_sql)

        msg = f"Unknown field type: {field_type}"
        raise NotImplementedError(msg)

    def _handle_collector_number(self, context: dict) -> str:
        """Handle collector number routing based on operator type.

        Routes to appropriate column based on operator:
        - ':' and '!=' operators use collector_number (text) with exact matching
        - Comparison operators ('>', '>=', '<', '<=') use collector_number_int (numeric)
        """
        if self.operator in (":", "!=", "<>"):
            # Use text column with exact matching
            if self.operator == ":":
                self.operator = "="
            return super().to_sql(context)
        if self.operator in (">", ">=", "<", "<="):
            # Use numeric column for comparisons
            # But first we need to update the lhs to point to the int column
            original_attr = self.lhs.attribute_name
            self.lhs.attribute_name = "collector_number_int"

            # Convert string value to numeric if needed
            if isinstance(self.rhs, StringValueNode):
                try:
                    numeric_value = int(self.rhs.value)
                    self.rhs = NumericValueNode(numeric_value)
                except ValueError as e:
                    msg = f"Invalid collector number for numeric comparison: {self.rhs.value}"
                    raise ValueError(msg) from e

            result = super().to_sql(context)
            # Restore original attribute name for potential reuse
            self.lhs.attribute_name = original_attr
            return result
        # Default to text column for any other operators
        if self.operator == ":":
            self.operator = "="
        return super().to_sql(context)

    def _handle_text_field_pattern_matching(self, context: dict, lhs_sql: str) -> str:
        """Handle pattern matching for regular text fields."""
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

    def _handle_jsonb_object(self, context: dict) -> str:  # noqa: PLR0911, C901
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
        elif attr == "card_legalities":
            # Handle legality searches - need original search attribute for status mapping
            original_attr = getattr(self.lhs, "original_attribute", attr)
            rhs = get_legality_comparison_object(self.rhs.value.strip(), original_attr)
            pname = param_name(rhs)
            context[pname] = rhs
        elif attr == "mana_cost_jsonb":
            # Handle mana cost searches with special comparison logic
            rhs = get_mana_cost_comparison_object(self.rhs.value.strip())
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

    def _handle_jsonb_array(self, context: dict) -> str:
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


def to_scryfall_ast(node: QueryNode) -> QueryNode:  # noqa: PLR0911
    """Convert a generic query node to a Scryfall-specific AST node.

    Args:
        node: The query node to convert.

    Returns:
        The corresponding Scryfall-specific node.
    """
    # If already a Scryfall AST node, return as-is
    if isinstance(node, ScryfallBinaryOperatorNode):
        return node
    if isinstance(node, ScryfallAttributeNode):
        return node

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
