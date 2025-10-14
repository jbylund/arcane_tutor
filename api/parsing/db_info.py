"""Database field information and mappings for Scryfall queries."""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    """Enumeration of supported database field types."""
    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object"
    NUMERIC = "numeric"
    TEXT = "text"
    DATE = "date"


class ParserClass(StrEnum):
    """Enumeration of parser classes for different field types."""
    NUMERIC = "numeric"      # Supports arithmetic operations (cmc, power, etc.)
    MANA = "mana"           # Mana cost fields with special mana value parsing
    RARITY = "rarity"       # Rarity fields with string-to-numeric conversion
    LEGALITY = "legality"   # Format/legal fields with JSON handling
    COLOR = "color"         # Color fields (card colors and color identity)
    TEXT = "text"           # Simple text fields (name, artist, oracle text)
    DATE = "date"           # Date fields with full date values
    YEAR = "year"           # Year fields with 4-digit year values


class FieldInfo:
    """Information about a database field and its search aliases."""

    def __init__(self, db_column_name: str, field_type: FieldType, search_aliases: list[str], parser_class: ParserClass | None = None) -> None:
        """Initialize field information.

        Args:
            db_column_name: The actual database column name.
            field_type: The type of the field.
            search_aliases: List of search aliases for this field.
            parser_class: The parser class to use for this field. If None, defaults based on field_type.
        """
        self.db_column_name = db_column_name
        self.field_type = field_type
        self.search_aliases = search_aliases
        # Default parser class based on field type if not specified
        if parser_class is None:
            parser_class = ParserClass.NUMERIC if field_type == FieldType.NUMERIC else ParserClass.TEXT
        self.parser_class = parser_class


DB_COLUMNS = [
    FieldInfo("card_artist", FieldType.TEXT, ["artist", "a"], ParserClass.TEXT),
    FieldInfo("card_colors", FieldType.JSONB_OBJECT, ["color", "colors", "c"], ParserClass.COLOR),
    FieldInfo("card_color_identity", FieldType.JSONB_OBJECT, ["color_identity", "coloridentity", "id", "identity"], ParserClass.COLOR),
    FieldInfo("card_frame_data", FieldType.JSONB_OBJECT, ["frame"], ParserClass.TEXT),
    FieldInfo("card_keywords", FieldType.JSONB_OBJECT, ["keyword"], ParserClass.TEXT),
    FieldInfo("card_name", FieldType.TEXT, ["name"], ParserClass.TEXT),
    FieldInfo("card_subtypes", FieldType.JSONB_ARRAY, ["subtype", "subtypes"], ParserClass.TEXT),
    FieldInfo("card_types", FieldType.JSONB_ARRAY, ["type", "types", "t"], ParserClass.TEXT),
    FieldInfo("cmc", FieldType.NUMERIC, ["cmc", "mv", "manavalue"], ParserClass.NUMERIC),
    FieldInfo("creature_power", FieldType.NUMERIC, ["power", "pow"], ParserClass.NUMERIC),
    FieldInfo("creature_toughness", FieldType.NUMERIC, ["toughness", "tou"], ParserClass.NUMERIC),
    FieldInfo("planeswalker_loyalty", FieldType.NUMERIC, ["loyalty", "loy"], ParserClass.NUMERIC),
    FieldInfo("edhrec_rank", FieldType.NUMERIC, [], ParserClass.NUMERIC),
    FieldInfo("mana_cost_jsonb", FieldType.JSONB_OBJECT, ["mana"], ParserClass.MANA),
    FieldInfo("mana_cost_text", FieldType.TEXT, ["mana", "m"], ParserClass.MANA),
    FieldInfo("devotion", FieldType.JSONB_OBJECT, ["devotion"], ParserClass.MANA),
    FieldInfo("price_usd", FieldType.NUMERIC, ["usd"], ParserClass.NUMERIC),
    FieldInfo("price_eur", FieldType.NUMERIC, ["eur"], ParserClass.NUMERIC),
    FieldInfo("price_tix", FieldType.NUMERIC, ["tix"], ParserClass.NUMERIC),
    FieldInfo("produced_mana", FieldType.JSONB_OBJECT, ["produces"], ParserClass.COLOR),
    FieldInfo("raw_card_blob", FieldType.JSONB_OBJECT, [], ParserClass.TEXT),
    FieldInfo("oracle_text", FieldType.TEXT, ["oracle", "o"], ParserClass.TEXT),
    FieldInfo("flavor_text", FieldType.TEXT, ["flavor", "ft"], ParserClass.TEXT),
    FieldInfo("card_oracle_tags", FieldType.JSONB_OBJECT, ["oracle_tags", "otag"], ParserClass.TEXT),
    FieldInfo("card_is_tags", FieldType.JSONB_OBJECT, ["is"], ParserClass.TEXT),
    FieldInfo("card_rarity_int", FieldType.NUMERIC, ["rarity", "r"], ParserClass.RARITY),
    FieldInfo("card_set_code", FieldType.TEXT, ["set", "s"], ParserClass.TEXT),
    FieldInfo("collector_number", FieldType.TEXT, ["number", "cn"], ParserClass.TEXT),
    FieldInfo("collector_number_int", FieldType.NUMERIC, [], ParserClass.NUMERIC),  # No direct aliases - will be routed
    FieldInfo("card_legalities", FieldType.JSONB_OBJECT, ["format", "f", "legal", "banned", "restricted"], ParserClass.LEGALITY),
    FieldInfo("card_layout", FieldType.TEXT, ["layout"], ParserClass.TEXT),
    FieldInfo("card_border", FieldType.TEXT, ["border"], ParserClass.TEXT),
    FieldInfo("card_watermark", FieldType.TEXT, ["watermark"], ParserClass.TEXT),
    FieldInfo("released_at", FieldType.DATE, ["date"], ParserClass.DATE),
    FieldInfo("released_at", FieldType.DATE, ["year"], ParserClass.YEAR),
]

KNOWN_CARD_ATTRIBUTES = set()
NUMERIC_ATTRIBUTES = set()
NON_NUMERIC_ATTRIBUTES = set()
SEARCH_NAME_TO_DB_NAME = {}
DB_NAME_TO_FIELD_TYPE = {}

# Parser class attribute groups for cleaner parsing logic
MANA_ATTRIBUTES = set()
RARITY_ATTRIBUTES = set()
LEGALITY_ATTRIBUTES = set()
COLOR_ATTRIBUTES = set()
TEXT_ATTRIBUTES = set()
DATE_ATTRIBUTES = set()
YEAR_ATTRIBUTES = set()

for col in DB_COLUMNS:
    KNOWN_CARD_ATTRIBUTES.add(col.db_column_name.lower())
    KNOWN_CARD_ATTRIBUTES.update(alias.lower() for alias in col.search_aliases)
    SEARCH_NAME_TO_DB_NAME[col.db_column_name.lower()] = col.db_column_name
    DB_NAME_TO_FIELD_TYPE[col.db_column_name] = col.field_type

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
    elif col.parser_class == ParserClass.DATE:
        DATE_ATTRIBUTES.add(col.db_column_name)
        DATE_ATTRIBUTES.update(col.search_aliases)
    elif col.parser_class == ParserClass.YEAR:
        YEAR_ATTRIBUTES.add(col.db_column_name)
        YEAR_ATTRIBUTES.update(col.search_aliases)
    elif col.parser_class == ParserClass.NUMERIC:
        # NUMERIC fields are already tracked in NUMERIC_ATTRIBUTES based on field_type
        pass
    else:
        msg = f"Unknown parser class: {col.parser_class}"
        raise ValueError(msg)

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
