"""Scryfall-specific AST nodes and query processing."""

from __future__ import annotations

import re

from api.parsing.db_info import (
    CARD_SUPERTYPES,
    CARD_TYPES,
    COLOR_CODE_TO_NAME,
    COLOR_NAME_TO_CODE,
    DATE_ATTRIBUTES,
    DB_NAME_TO_ATTRIBUTE_LEVEL,
    DB_NAME_TO_FIELD_TYPE,
    SEARCH_NAME_TO_DB_NAME,
    YEAR_ATTRIBUTES,
    AttributeLevel,
    FieldType,
)
from api.parsing.nodes import (
    AndNode,
    AttributeNode,
    BinaryOperatorNode,
    ManaValueNode,
    NotNode,
    NumericValueNode,
    OrNode,
    Query,
    QueryNode,
    RegexValueNode,
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

    def __init__(self: ScryfallAttributeNode, attribute_name: str) -> None:
        """Initialize a Scryfall attribute node.

        Args:
            attribute_name: The search attribute name to map to database column.
        """
        # Preserve original attribute name BEFORE mapping for specialized handling
        self.original_attribute = attribute_name.lower()
        db_column_name = SEARCH_NAME_TO_DB_NAME.get(attribute_name.lower(), attribute_name)
        super().__init__(db_column_name)

    def to_sql(self: ScryfallAttributeNode, context: dict) -> str:
        """Generate SQL for Scryfall attribute node.

        Args:
            context: SQL parameter context.

        Returns:
            SQL string for the attribute reference with proper DFC schema path.
        """
        del context
        remapped = SEARCH_NAME_TO_DB_NAME.get(self.attribute_name, self.attribute_name)
        attr_level = DB_NAME_TO_ATTRIBUTE_LEVEL.get(remapped, AttributeLevel.FACE)

        # Map the database column based on its level in the DFC schema
        if attr_level == AttributeLevel.FACE:
            # Face-level attributes - return placeholder that will be expanded in _wrap_face_level_predicate
            return f"card.{remapped}"
        if attr_level == AttributeLevel.CARD:
            # Card-level attributes from the card_info composite
            return f"((card.card_info).{remapped})"
        # AttributeLevel.PRINT
        # Print-level attributes from the print_info composite
        # Attributes with print_ prefix are in the front_face/back_face composites
        if remapped.startswith("print_"):
            return f"((card.print_info).front_face).{remapped}"
        # Other print-level attributes are direct columns in the print_info composite
        return f"((card.print_info).{remapped})"


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


def get_frame_data_comparison_object(val: str) -> dict[str, bool]:
    """Convert frame data string to comparison object for database queries.

    Handles both frame versions (e.g., "2015", "1997") and frame effects (e.g., "showcase", "legendary").
    All values are titlecased for consistency.

    Args:
        val: Frame data string to normalize.

    Returns:
        Dictionary mapping normalized frame data to True.
    """
    val = val.strip()

    # Always titlecase for consistency
    normalized_val = val.title()

    return {normalized_val: True}


def extract_frame_data_from_raw_card(raw_card: dict) -> dict[str, bool]:
    """Extract frame data from a raw card dictionary.

    Combines frame version and frame effects into a single JSONB object,
    following the same pattern as _preprocess_card method.

    Args:
        raw_card: Raw card dictionary from Scryfall API.

    Returns:
        Dictionary mapping frame data keys to True.
    """
    frame_data = {}

    # Add frame version if present (titlecased for consistency)
    frame_version = raw_card.get("frame")
    if frame_version:
        frame_data[frame_version.title()] = True

    # Add frame effects if present (titlecased for consistency)
    frame_effects = raw_card.get("frame_effects", [])
    for effect in frame_effects:
        frame_data[effect.title()] = True

    return frame_data


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


def get_is_tags_comparison_object(val: str) -> dict[str, bool]:
    """Convert is: tag string to comparison object for database queries.

    Args:
        val: is: tag string to normalize.

    Returns:
        Dictionary mapping normalized is: tag to True.
    """
    # is: tags are stored in lowercase
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


def mana_cost_str_to_dict(mana_cost_str: str) -> dict:
    """Convert a mana cost string to a dictionary of colored symbols and their counts."""
    colored_symbol_counts = {}
    for mana_symbol in re.findall(r"{([^}]*)}", mana_cost_str.upper()):
        try:
            int(mana_symbol)
        except ValueError:
            colored_symbol_counts[mana_symbol] = colored_symbol_counts.get(mana_symbol, 0) + 1
        else:
            pass
    as_dict = {}
    for colored_symbol, count in colored_symbol_counts.items():
        as_dict[colored_symbol] = list(range(1, count + 1))
    return as_dict


def calculate_cmc(mana_cost_str: str) -> int:
    """Calculate the converted mana cost from a mana cost string."""
    cmc = 0
    for mana_symbol in re.findall(r"{([^}]*)}", mana_cost_str):
        try:
            # Generic mana symbols add to CMC
            cmc += int(mana_symbol)
        except ValueError:
            # X costs count as 0 for CMC calculation
            if mana_symbol.upper() == "X":
                continue
            # Colored mana symbols (W, U, B, R, G, etc.) each count as 1
            # Handle hybrid symbols like {W/U} as 1
            # Handle Phyrexian symbols like {W/P} as 1
            # For simplicity, any non-numeric, non-X symbol counts as 1
            cmc += 1
    return cmc


def calculate_devotion(mana_cost_str: str) -> dict:
    """Calculate devotion from a mana cost string, handling split mana costs properly.

    For split mana costs like {R/G}, each color contributes 1 to its respective devotion.
    For example, {R/G} contributes 1 to both R devotion and G devotion.
    """
    devotion = {"W": [], "U": [], "B": [], "R": [], "G": [], "C": []}
    for ichar in mana_cost_str.upper().strip():
        current_devotion = devotion.get(ichar)
        if current_devotion is not None:
            current_devotion.append(len(current_devotion) + 1)
    # Remove colors with 0 devotion for cleaner storage
    return {
        color: color_devotion
        for color, color_devotion in devotion.items()
        if color_devotion
    }


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
            return self._handle_scryfall_attribute(context)

        # Fallback: use default logic
        return super().to_sql(context)

    def _handle_scryfall_attribute(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle Scryfall attribute-specific SQL generation."""
        attr = self.lhs.attribute_name

        # Special routing for collector numbers based on operator
        if attr == "print_collector_number":
            return self._handle_collector_number(context)

        # Special handling for mana attributes with comparison operators
        if attr in ("face_mana_cost_text", "face_mana_cost_jsonb") and isinstance(self.rhs, ManaValueNode | StringValueNode):
            return self._handle_mana_cost_comparison(context)

        # Special handling for date/year searches
        if self.lhs.original_attribute in DATE_ATTRIBUTES:
            return self._handle_date_search(context)
        if self.lhs.original_attribute in YEAR_ATTRIBUTES:
            return self._handle_year_search(context)

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

            sql = super().to_sql(context)
            # For face-level attributes, wrap in OR for front/back faces
            return self._wrap_face_level_predicate(sql, attr)

        if field_type == FieldType.JSONB_OBJECT:
            sql = self._handle_jsonb_object(context)
            return self._wrap_face_level_predicate(sql, attr)

        if field_type == FieldType.JSONB_ARRAY:
            sql = self._handle_jsonb_array(context)
            return self._wrap_face_level_predicate(sql, attr)

        if self.operator == ":":
            lhs_sql = self.lhs.to_sql(context)
            sql = self._handle_colon_operator(context, field_type, lhs_sql, attr)
            return self._wrap_face_level_predicate(sql, attr)

        if field_type == FieldType.TEXT:
            sql = super().to_sql(context)
            return self._wrap_face_level_predicate(sql, attr)

        msg = f"Unknown field type: {field_type}"
        raise NotImplementedError(msg)

    def _wrap_face_level_predicate(self: ScryfallBinaryOperatorNode, sql: str, attr: str) -> str:
        """Wrap face-level predicates to check both front and back faces."""
        attr_level = DB_NAME_TO_ATTRIBUTE_LEVEL.get(attr, AttributeLevel.FACE)

        # Only wrap if it's a face-level attribute
        if attr_level != AttributeLevel.FACE:
            return sql

        # Replace card.face_xxx with versions for front and back face
        # The pattern is: (card_info).front_face.face_xxx OR (card_info).back_face.face_xxx
        front_sql = sql.replace("card.", "((card.card_info).front_face).")
        back_sql = sql.replace("card.", "((card.card_info).back_face).")

        return f"({front_sql} OR {back_sql})"

    def _handle_colon_operator(self: ScryfallBinaryOperatorNode, context: dict, field_type: str, lhs_sql: str, attr: str) -> str:
        """Handle colon operator for different field types."""
        if field_type == FieldType.TEXT:
            # Handle fields that need exact matching instead of pattern matching
            if attr in ("card_set_code", "print_layout", "print_border", "print_watermark", "card_layout", "card_border"):
                # For layout, border, and watermark fields, lowercase the search value for case-insensitive matching
                if attr in ("print_layout", "print_border", "print_watermark", "card_layout", "card_border") and hasattr(self.rhs, "value"):
                    self.rhs.value = self.rhs.value.lower()

                if self.operator == ":":
                    self.operator = "="
                return super().to_sql(context)

            # Regular text field handling with pattern matching
            return self._handle_text_field_pattern_matching(context, lhs_sql)

        msg = f"Unknown field type: {field_type}"
        raise NotImplementedError(msg)

    def _handle_collector_number(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle collector number routing based on operator type.

        Routes to appropriate column based on operator:
        - ':' and '!=' operators use print_collector_number (text) with exact matching
        - Comparison operators ('>', '>=', '<', '<=') use print_collector_number_int (numeric)
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
            self.lhs.attribute_name = "print_collector_number_int"

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

    def _handle_mana_cost_comparison(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle mana cost comparisons with approximate matching."""
        attr = self.lhs.attribute_name
        mana_cost_str = self.rhs.value

        # For ":" and "=" operators, use text field matching for exact matches
        if self.operator in (":", "="):
            if attr == "face_mana_cost_text":
                # Use text matching for exact mana cost matches
                if self.operator == ":":
                    self.operator = "="
                return super().to_sql(context)
            # For face_mana_cost_jsonb, convert to JSONB and use equality
            mana_dict = mana_cost_str_to_dict(mana_cost_str)
            pname = param_name(mana_dict)
            context[pname] = mana_dict
            lhs_sql = self.lhs.to_sql(context)
            return f"({lhs_sql} = %({pname})s)"

        # For comparison operators, we need both containment check and CMC check
        if self.operator in ("<=", "<", ">=", ">"):
            return self._handle_mana_cost_approximate_comparison(context, mana_cost_str)

        # Fallback to text pattern matching
        return self._handle_text_field_pattern_matching(context, self.lhs.to_sql(context))

    def _handle_mana_cost_approximate_comparison(self: ScryfallBinaryOperatorNode, context: dict, mana_cost_str: str) -> str:
        """Handle approximate mana cost comparisons using containment and CMC."""
        # Convert the query mana cost to dict for containment checking
        query_mana_dict = mana_cost_str_to_dict(mana_cost_str)
        query_cmc = calculate_cmc(mana_cost_str)

        # Prepare parameters
        mana_param = param_name(query_mana_dict)
        cmc_param = param_name(query_cmc)
        context[mana_param] = query_mana_dict
        context[cmc_param] = query_cmc

        # SQL fragments
        mana_jsonb_sql = "card.face_mana_cost_jsonb"
        cmc_sql = "card.face_cmc"

        if self.operator == "<=":
            # Card costs <= query if:
            # 1. Card doesn't have more colored pips (card mana <@ query mana)
            # 2. Card doesn't cost more total (card cmc <= query cmc)
            return f"({mana_jsonb_sql} <@ %({mana_param})s AND {cmc_sql} <= %({cmc_param})s)"

        if self.operator == "<":
            # Card costs < query if:
            # 1. Card doesn't have more colored pips (card mana <@ query mana)
            # 2. Card doesn't cost more total (card cmc <= query cmc)
            # 3. Costs are not identical
            return f"({mana_jsonb_sql} <@ %({mana_param})s AND {cmc_sql} <= %({cmc_param})s AND {mana_jsonb_sql} <> %({mana_param})s)"

        if self.operator == ">=":
            # Card costs >= query if:
            # 1. Card has at least the colored pips (card mana @> query mana)
            # 2. Card costs at least as much total (card cmc >= query cmc)
            return f"(%({mana_param})s <@ {mana_jsonb_sql} AND {cmc_sql} >= %({cmc_param})s)"

        if self.operator == ">":
            # Card costs > query if:
            # 1. Card has at least the colored pips (card mana @> query mana)
            # 2. Card costs at least as much total (card cmc >= query cmc)
            # 3. Costs are not identical
            return f"(%({mana_param})s <@ {mana_jsonb_sql} AND {cmc_sql} >= %({cmc_param})s AND {mana_jsonb_sql} <> %({mana_param})s)"

        msg = f"Unsupported mana cost operator: {self.operator}"
        raise ValueError(msg)

    def _handle_date_search(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle date search queries.

        For 'date:' searches, compares against the full released_at date.
        Accepts either YYYY or YYYY-MM-DD format.

        Args:
            context: SQL parameter context.

        Returns:
            SQL string for the date comparison.
        """
        search_value = self.rhs.value if isinstance(self.rhs, StringValueNode | NumericValueNode) else str(self.rhs)

        # Normalize : operator to =
        operator = "=" if self.operator == ":" else self.operator

        # For date searches, compare against the full date
        # The value should be in YYYY-MM-DD or YYYY format
        pname = param_name(search_value)
        context[pname] = search_value
        return f"(card.released_at {operator} %({pname})s)"

    def _handle_year_search(self: ScryfallBinaryOperatorNode, context: dict) -> str:
        """Handle year search queries.

        For 'year:' searches, converts to date range queries for better index usage.
        Only accepts 4-digit year values (YYYY).

        Args:
            context: SQL parameter context.

        Returns:
            SQL string for the year comparison using date ranges.
        """
        search_value = self.rhs.value if isinstance(self.rhs, StringValueNode | NumericValueNode) else str(self.rhs)

        # Normalize : operator to =
        operator = "=" if self.operator == ":" else self.operator

        # For year searches, convert to date range queries for better index usage
        # Only accept 4-digit year values
        year_str_length = 4
        if (isinstance(search_value, str) and len(search_value) == year_str_length and search_value.isdigit()) or isinstance(search_value, int | float):
            year_value = int(search_value)
        else:
            msg = f"Invalid year value: {search_value}. Year must be a 4-digit number."
            raise ValueError(msg)

        # Convert year comparison to date range for index usage
        # year=2024 becomes: '2024-01-01' <= released_at AND released_at < '2025-01-01'
        # year>2024 becomes: released_at >= '2025-01-01'
        # year<2024 becomes: released_at < '2024-01-01'
        # year>=2024 becomes: released_at >= '2024-01-01'
        # year<=2024 becomes: released_at < '2025-01-01'

        start_of_year = f"{year_value}-01-01"
        start_of_next_year = f"{year_value + 1}-01-01"

        if operator == "=":
            p_start_name = param_name(start_of_year)
            p_end_name = param_name(start_of_next_year)
            context[p_start_name] = start_of_year
            context[p_end_name] = start_of_next_year
            return f"(%({p_start_name})s <= card.released_at AND card.released_at < %({p_end_name})s)"
        if operator == ">":
            # year > 2024 means released_at >= 2025-01-01
            pname = param_name(start_of_next_year)
            context[pname] = start_of_next_year
            return f"(card.released_at >= %({pname})s)"
        if operator == "<":
            # year < 2024 means released_at < 2024-01-01
            pname = param_name(start_of_year)
            context[pname] = start_of_year
            return f"(card.released_at < %({pname})s)"
        if operator == ">=":
            # year >= 2024 means released_at >= 2024-01-01
            pname = param_name(start_of_year)
            context[pname] = start_of_year
            return f"(card.released_at >= %({pname})s)"
        if operator == "<=":
            # year <= 2024 means released_at < 2025-01-01
            pname = param_name(start_of_next_year)
            context[pname] = start_of_next_year
            return f"(card.released_at < %({pname})s)"

        msg = f"Unsupported operator for year search: {operator}"
        raise ValueError(msg)

    def _handle_text_field_pattern_matching(self: ScryfallBinaryOperatorNode, context: dict, lhs_sql: str) -> str:
        """Handle pattern matching for regular text fields."""
        # Check if RHS is a regex pattern
        if isinstance(self.rhs, RegexValueNode):
            regex_pattern = self.rhs.value
            _param_name = param_name(regex_pattern)
            context[_param_name] = regex_pattern
            # Use PostgreSQL ~* operator for case-insensitive regex matching
            return f"({lhs_sql} ~* %({_param_name})s)"

        # Regular text pattern matching with ILIKE
        if isinstance(self.rhs, StringValueNode | ManaValueNode):
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

    def _handle_jsonb_object(self: ScryfallBinaryOperatorNode, context: dict) -> str:  # noqa: PLR0912
        # Produce the query as a jsonb object
        lhs_sql = self.lhs.to_sql(context)
        attr = self.lhs.attribute_name
        is_color_identity = False
        if attr in ("card_colors", "face_colors", "card_color_identity", "produced_mana"):
            rhs = get_colors_comparison_object(self.rhs.value.strip().lower())
            pname = param_name(rhs)
            context[pname] = rhs
            # Color identity has inverted semantics for the : operator only
            is_color_identity = attr == "card_color_identity"
        elif attr == "devotion":
            # Devotion uses mana cost syntax, so we need to convert it to color comparison
            # Extract color codes from mana cost syntax like {G}, {R}{G}, etc.
            query_devotion = calculate_devotion(self.rhs.value.strip())
            pname = param_name(query_devotion)
            context[pname] = query_devotion
        elif attr == "card_keywords":
            rhs = get_keywords_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs
        elif attr == "card_frame_data":
            # Frame data handling - treat like keywords (exact string match)
            rhs = get_frame_data_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs
        elif attr == "card_oracle_tags":
            # Oracle tags are stored in lowercase, unlike keywords
            rhs = get_oracle_tags_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs
        elif attr == "card_is_tags":
            # is: tags are stored in lowercase, similar to oracle tags
            rhs = get_is_tags_comparison_object(self.rhs.value.strip())
            pname = param_name(rhs)
            context[pname] = rhs
        elif attr == "card_legalities":
            # Handle legality searches - need original search attribute for status mapping
            original_attr = getattr(self.lhs, "original_attribute", attr)
            rhs = get_legality_comparison_object(self.rhs.value.strip(), original_attr)
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


def to_scryfall_ast(node: QueryNode) -> QueryNode:
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
