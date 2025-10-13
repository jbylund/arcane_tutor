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


class AttributeLevel(StrEnum):
    """Enumeration of attribute levels in the DFC schema."""
    FACE = "face"        # Attributes that differ between faces (power, toughness, types, etc.)
    CARD = "card"        # Attributes shared across all faces (color_identity, keywords, edhrec_rank)
    PRINT = "print"      # Attributes specific to a printing (set, collector_number, price, etc.)


class FieldInfo:
    """Information about a database field and its search aliases."""

    def __init__(
        self,
        *,
        db_column_name: str,
        field_type: FieldType,
        search_aliases: list[str],
        parser_class: ParserClass | None = None,
        attribute_level: AttributeLevel = AttributeLevel.FACE,
    ) -> None:
        """Initialize field information.

        Args:
            db_column_name: The actual database column name.
            field_type: The type of the field.
            search_aliases: List of search aliases for this field.
            parser_class: The parser class to use for this field. If None, defaults based on field_type.
            attribute_level: The level at which this attribute exists (face/card/print).
        """
        self.db_column_name = db_column_name
        self.field_type = field_type
        self.search_aliases = search_aliases
        self.attribute_level = attribute_level
        # Default parser class based on field type if not specified
        if parser_class is None:
            parser_class = ParserClass.NUMERIC if field_type == FieldType.NUMERIC else ParserClass.TEXT
        self.parser_class = parser_class


DB_COLUMNS = [
    # Print-level attributes (in front_face/back_face composites within print_info)
    FieldInfo(db_column_name="print_artist", field_type=FieldType.TEXT, search_aliases=["artist", "a"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_frame_data", field_type=FieldType.JSONB_OBJECT, search_aliases=["frame"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_is_tags", field_type=FieldType.JSONB_OBJECT, search_aliases=["is"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="card_layout", field_type=FieldType.TEXT, search_aliases=["layout"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.CARD),
    FieldInfo(db_column_name="card_border", field_type=FieldType.TEXT, search_aliases=["border"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.CARD),
    FieldInfo(db_column_name="print_watermark", field_type=FieldType.TEXT, search_aliases=["watermark"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_collector_number", field_type=FieldType.TEXT, search_aliases=["number", "cn"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_collector_number_int", field_type=FieldType.NUMERIC, search_aliases=[], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_illustration_id", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_image_location_uuid", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_prefer_score", field_type=FieldType.NUMERIC, search_aliases=[], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_flavor_text", field_type=FieldType.TEXT, search_aliases=["flavor", "ft"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="print_raw_card_blob", field_type=FieldType.JSONB_OBJECT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),

    # Card-level attributes (shared across faces, stored at oracle_id level)
    FieldInfo(db_column_name="card_name", field_type=FieldType.TEXT, search_aliases=["name"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.CARD),
    FieldInfo(db_column_name="oracle_id", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.CARD),
    FieldInfo(db_column_name="card_color_identity", field_type=FieldType.JSONB_OBJECT, search_aliases=["color_identity", "coloridentity", "id", "identity"], parser_class=ParserClass.COLOR, attribute_level=AttributeLevel.CARD),
    FieldInfo(db_column_name="card_keywords", field_type=FieldType.JSONB_OBJECT, search_aliases=["keyword"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.CARD),
    FieldInfo(db_column_name="edhrec_rank", field_type=FieldType.NUMERIC, search_aliases=[], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.CARD),

    # Print-level attributes that are direct columns in prints table (not in face composites)
    FieldInfo(db_column_name="scryfall_id", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="card_set_code", field_type=FieldType.TEXT, search_aliases=["set", "s"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="released_at", field_type=FieldType.DATE, search_aliases=["date"], parser_class=ParserClass.DATE, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="released_at", field_type=FieldType.DATE, search_aliases=["year"], parser_class=ParserClass.YEAR, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="set_name", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="price_usd", field_type=FieldType.NUMERIC, search_aliases=["usd"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="price_eur", field_type=FieldType.NUMERIC, search_aliases=["eur"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="price_tix", field_type=FieldType.NUMERIC, search_aliases=["tix"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="card_legalities", field_type=FieldType.JSONB_OBJECT, search_aliases=["format", "f", "legal", "banned", "restricted"], parser_class=ParserClass.LEGALITY, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="card_rarity_int", field_type=FieldType.NUMERIC, search_aliases=["rarity", "r"], parser_class=ParserClass.RARITY, attribute_level=AttributeLevel.PRINT),
    FieldInfo(db_column_name="card_rarity_text", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.PRINT),

    # Face-level attributes (differ between front and back face)
    FieldInfo(db_column_name="face_name", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_idx", field_type=FieldType.NUMERIC, search_aliases=[], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_colors", field_type=FieldType.JSONB_OBJECT, search_aliases=["color", "colors", "c"], parser_class=ParserClass.COLOR, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_subtypes", field_type=FieldType.JSONB_ARRAY, search_aliases=["subtype", "subtypes"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_types", field_type=FieldType.JSONB_ARRAY, search_aliases=["type", "types", "t"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_cmc", field_type=FieldType.NUMERIC, search_aliases=["cmc"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_creature_power", field_type=FieldType.NUMERIC, search_aliases=["power", "pow"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_creature_toughness", field_type=FieldType.NUMERIC, search_aliases=["toughness", "tou"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_planeswalker_loyalty", field_type=FieldType.NUMERIC, search_aliases=["loyalty", "loy"], parser_class=ParserClass.NUMERIC, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_mana_cost_jsonb", field_type=FieldType.JSONB_OBJECT, search_aliases=["mana"], parser_class=ParserClass.MANA, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_mana_cost_text", field_type=FieldType.TEXT, search_aliases=["mana", "m"], parser_class=ParserClass.MANA, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_devotion", field_type=FieldType.JSONB_OBJECT, search_aliases=["devotion"], parser_class=ParserClass.MANA, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_produced_mana", field_type=FieldType.JSONB_OBJECT, search_aliases=["produces"], parser_class=ParserClass.COLOR, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_oracle_text", field_type=FieldType.TEXT, search_aliases=["oracle", "o"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_oracle_tags", field_type=FieldType.JSONB_OBJECT, search_aliases=["oracle_tags", "otag"], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.FACE),
    FieldInfo(db_column_name="face_type_line", field_type=FieldType.TEXT, search_aliases=[], parser_class=ParserClass.TEXT, attribute_level=AttributeLevel.FACE),
]

KNOWN_CARD_ATTRIBUTES = set()
NUMERIC_ATTRIBUTES = set()
NON_NUMERIC_ATTRIBUTES = set()
SEARCH_NAME_TO_DB_NAME = {}
DB_NAME_TO_FIELD_TYPE = {}
DB_NAME_TO_ATTRIBUTE_LEVEL = {}

# Parser class attribute groups for cleaner parsing logic
MANA_ATTRIBUTES = set()
RARITY_ATTRIBUTES = set()
LEGALITY_ATTRIBUTES = set()
COLOR_ATTRIBUTES = set()
TEXT_ATTRIBUTES = set()
DATE_ATTRIBUTES = set()

# Attribute level groups for DFC schema
FACE_LEVEL_ATTRIBUTES = set()
CARD_LEVEL_ATTRIBUTES = set()
PRINT_LEVEL_ATTRIBUTES = set()
YEAR_ATTRIBUTES = set()

for col in DB_COLUMNS:
    KNOWN_CARD_ATTRIBUTES.add(col.db_column_name.lower())
    KNOWN_CARD_ATTRIBUTES.update(alias.lower() for alias in col.search_aliases)
    SEARCH_NAME_TO_DB_NAME[col.db_column_name.lower()] = col.db_column_name
    DB_NAME_TO_FIELD_TYPE[col.db_column_name] = col.field_type
    DB_NAME_TO_ATTRIBUTE_LEVEL[col.db_column_name] = col.attribute_level

    # Map search aliases to db name and attribute level
    for alias in col.search_aliases:
        SEARCH_NAME_TO_DB_NAME[alias.lower()] = col.db_column_name

    # Group by attribute level for DFC schema
    if col.attribute_level == AttributeLevel.FACE:
        FACE_LEVEL_ATTRIBUTES.add(col.db_column_name)
    elif col.attribute_level == AttributeLevel.CARD:
        CARD_LEVEL_ATTRIBUTES.add(col.db_column_name)
    elif col.attribute_level == AttributeLevel.PRINT:
        PRINT_LEVEL_ATTRIBUTES.add(col.db_column_name)

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
