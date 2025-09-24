"""Database field information and mappings for Scryfall queries."""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    """Enumeration of supported database field types."""
    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object"
    NUMERIC = "numeric"
    TEXT = "text"


class OperatorStrategy(StrEnum):
    """Enumeration of operator handling strategies for colon operator."""
    EXACT = "exact"      # : becomes = (exact matching)
    PATTERN = "pattern"  # : becomes ILIKE with wildcards (pattern matching)


class ParserClass(StrEnum):
    """Enumeration of parser classes for different field types."""
    NUMERIC = "numeric"      # Supports arithmetic operations (cmc, power, etc.)
    MANA = "mana"           # Mana cost fields with special mana value parsing
    RARITY = "rarity"       # Rarity fields with string-to-numeric conversion
    LEGALITY = "legality"   # Format/legal fields with JSON handling
    COLOR = "color"         # Color fields (card colors and color identity)
    TEXT = "text"           # Simple text fields (name, artist, oracle text)


class FieldInfo:
    """Information about a database field and its search aliases."""

    def __init__(
        self,
        *,
        db_column_name: str,
        field_type: FieldType,
        search_aliases: list[str],
        parser_class: ParserClass | None = None,
        operator_strategy: OperatorStrategy = OperatorStrategy.PATTERN,
    ) -> None:
        """Initialize field information.

        Args:
            db_column_name: The actual database column name.
            field_type: The type of the field.
            search_aliases: List of search aliases for this field.
            parser_class: The parser class to use for this field. If None, defaults based on field_type.
            operator_strategy: The operator strategy for colon operator handling. Defaults to PATTERN.
        """
        self.db_column_name = db_column_name
        self.field_type = field_type
        self.search_aliases = search_aliases
        self.operator_strategy = operator_strategy
        # Default parser class based on field type if not specified
        if parser_class is None:
            parser_class = ParserClass.NUMERIC if field_type == FieldType.NUMERIC else ParserClass.TEXT
        self.parser_class = parser_class


DB_COLUMNS = [
    FieldInfo(db_column_name="card_artist", field_type=FieldType.TEXT, search_aliases=["artist", "a"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_colors", field_type=FieldType.JSONB_OBJECT, search_aliases=["color", "colors", "c"], parser_class=ParserClass.COLOR),
    FieldInfo(db_column_name="card_color_identity", field_type=FieldType.JSONB_OBJECT, search_aliases=["color_identity", "coloridentity", "id", "identity"], parser_class=ParserClass.COLOR),
    FieldInfo(db_column_name="card_frame_data", field_type=FieldType.JSONB_OBJECT, search_aliases=["frame"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_keywords", field_type=FieldType.JSONB_OBJECT, search_aliases=["keyword"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_name", field_type=FieldType.TEXT, search_aliases=["name"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_subtypes", field_type=FieldType.JSONB_ARRAY, search_aliases=["subtype", "subtypes"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_types", field_type=FieldType.JSONB_ARRAY, search_aliases=["type", "types", "t"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="cmc", field_type=FieldType.NUMERIC, search_aliases=["cmc"], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="creature_power", field_type=FieldType.NUMERIC, search_aliases=["power", "pow"], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="creature_toughness", field_type=FieldType.NUMERIC, search_aliases=["toughness", "tou"], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="edhrec_rank", field_type=FieldType.NUMERIC, search_aliases=[], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="mana_cost_jsonb", field_type=FieldType.JSONB_OBJECT, search_aliases=["mana"], parser_class=ParserClass.MANA),
    FieldInfo(db_column_name="mana_cost_text", field_type=FieldType.TEXT, search_aliases=["mana", "m"], parser_class=ParserClass.MANA),
    FieldInfo(db_column_name="price_usd", field_type=FieldType.NUMERIC, search_aliases=["usd"], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="price_eur", field_type=FieldType.NUMERIC, search_aliases=["eur"], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="price_tix", field_type=FieldType.NUMERIC, search_aliases=["tix"], parser_class=ParserClass.NUMERIC),
    FieldInfo(db_column_name="produced_mana", field_type=FieldType.JSONB_OBJECT, search_aliases=["produces"], parser_class=ParserClass.COLOR),
    FieldInfo(db_column_name="raw_card_blob", field_type=FieldType.JSONB_OBJECT, search_aliases=[], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="oracle_text", field_type=FieldType.TEXT, search_aliases=["oracle", "o"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="flavor_text", field_type=FieldType.TEXT, search_aliases=["flavor"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_oracle_tags", field_type=FieldType.JSONB_OBJECT, search_aliases=["oracle_tags", "otag"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_is_tags", field_type=FieldType.JSONB_OBJECT, search_aliases=["is"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="card_rarity_int", field_type=FieldType.NUMERIC, search_aliases=["rarity", "r"], parser_class=ParserClass.RARITY),
    FieldInfo(db_column_name="card_set_code", field_type=FieldType.TEXT, search_aliases=["set", "s"], parser_class=ParserClass.TEXT, operator_strategy=OperatorStrategy.EXACT),
    FieldInfo(db_column_name="collector_number", field_type=FieldType.TEXT, search_aliases=["number", "cn"], parser_class=ParserClass.TEXT),
    FieldInfo(db_column_name="collector_number_int", field_type=FieldType.NUMERIC, search_aliases=[], parser_class=ParserClass.NUMERIC),  # No direct aliases - will be routed
    FieldInfo(db_column_name="card_legalities", field_type=FieldType.JSONB_OBJECT, search_aliases=["format", "f", "legal", "banned", "restricted"], parser_class=ParserClass.LEGALITY),
    FieldInfo(db_column_name="card_layout", field_type=FieldType.TEXT, search_aliases=["layout"], parser_class=ParserClass.TEXT, operator_strategy=OperatorStrategy.EXACT),
    FieldInfo(db_column_name="card_border", field_type=FieldType.TEXT, search_aliases=["border"], parser_class=ParserClass.TEXT, operator_strategy=OperatorStrategy.EXACT),
    FieldInfo(db_column_name="card_watermark", field_type=FieldType.TEXT, search_aliases=["watermark"], parser_class=ParserClass.TEXT, operator_strategy=OperatorStrategy.EXACT),
]

KNOWN_CARD_ATTRIBUTES = set()
NUMERIC_ATTRIBUTES = set()
NON_NUMERIC_ATTRIBUTES = set()
SEARCH_NAME_TO_DB_NAME = {}
DB_NAME_TO_FIELD_TYPE = {}
DB_NAME_TO_OPERATOR_STRATEGY = {}

# Parser class attribute groups for cleaner parsing logic
MANA_ATTRIBUTES = set()
RARITY_ATTRIBUTES = set()
LEGALITY_ATTRIBUTES = set()
COLOR_ATTRIBUTES = set()
TEXT_ATTRIBUTES = set()

for col in DB_COLUMNS:
    KNOWN_CARD_ATTRIBUTES.add(col.db_column_name.lower())
    KNOWN_CARD_ATTRIBUTES.update(alias.lower() for alias in col.search_aliases)
    SEARCH_NAME_TO_DB_NAME[col.db_column_name.lower()] = col.db_column_name
    DB_NAME_TO_FIELD_TYPE[col.db_column_name] = col.field_type
    DB_NAME_TO_OPERATOR_STRATEGY[col.db_column_name] = col.operator_strategy

    # Separate numeric and non-numeric attributes (maintain backwards compatibility)
    if col.field_type == FieldType.NUMERIC:
        NUMERIC_ATTRIBUTES.add(col.db_column_name)
        NUMERIC_ATTRIBUTES.update(col.search_aliases)
    else:
        NON_NUMERIC_ATTRIBUTES.add(col.db_column_name)
        NON_NUMERIC_ATTRIBUTES.update(col.search_aliases)

    # Group attributes by parser class for cleaner parsing logic
    if col.parser_class == ParserClass.MANA:
        MANA_ATTRIBUTES.add(col.db_column_name)
        MANA_ATTRIBUTES.update(col.search_aliases)
    elif col.parser_class == ParserClass.RARITY:
        RARITY_ATTRIBUTES.add(col.db_column_name)
        RARITY_ATTRIBUTES.update(col.search_aliases)
    elif col.parser_class == ParserClass.LEGALITY:
        LEGALITY_ATTRIBUTES.add(col.db_column_name)
        LEGALITY_ATTRIBUTES.update(col.search_aliases)
    elif col.parser_class == ParserClass.COLOR:
        COLOR_ATTRIBUTES.add(col.db_column_name)
        COLOR_ATTRIBUTES.update(col.search_aliases)
    elif col.parser_class == ParserClass.TEXT:
        TEXT_ATTRIBUTES.add(col.db_column_name)
        TEXT_ATTRIBUTES.update(col.search_aliases)

    for ialias in col.search_aliases:
        SEARCH_NAME_TO_DB_NAME[ialias.lower()] = col.db_column_name


CARD_SUPERTYPES = {
    "Basic",
    "Legendary",
    "Snow",
    "World",
}

CARD_TYPES = {
    "Artifact",
    "Conspiracy",
    "Creature",
    "Enchantment",
    "Instant",
    "Kindred",  # new name for tribal
    "Land",
    "Planeswalker",
    "Sorcery",
    "Tribal",
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
