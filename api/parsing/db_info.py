"""Database field information and mappings for Scryfall queries."""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    """Enumeration of supported database field types."""
    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object"
    NUMERIC = "numeric"
    TEXT = "text"


class FieldInfo:
    """Information about a database field and its search aliases."""

    def __init__(self, db_column_name: str, field_type: FieldType, search_aliases: list[str]) -> None:
        """Initialize field information.

        Args:
            db_column_name: The actual database column name.
            field_type: The type of the field.
            search_aliases: List of search aliases for this field.
        """
        self.db_column_name = db_column_name
        self.field_type = field_type
        self.search_aliases = search_aliases


DB_COLUMNS = [
    FieldInfo("card_colors", FieldType.JSONB_OBJECT, ["color", "colors", "c"]),
    FieldInfo("card_color_identity", FieldType.JSONB_OBJECT, ["color_identity", "coloridentity", "id", "identity"]),
    FieldInfo("card_keywords", FieldType.JSONB_OBJECT, ["keyword", "keywords", "k"]),
    FieldInfo("card_name", FieldType.TEXT, ["name"]),
    FieldInfo("card_subtypes", FieldType.JSONB_ARRAY, ["subtype", "subtypes"]),
    FieldInfo("card_types", FieldType.JSONB_ARRAY, ["type", "types", "t"]),
    FieldInfo("cmc", FieldType.NUMERIC, ["cmc"]),
    FieldInfo("creature_power", FieldType.NUMERIC, ["power", "pow"]),
    FieldInfo("creature_toughness", FieldType.NUMERIC, ["toughness", "tou"]),
    FieldInfo("edhrec_rank", FieldType.NUMERIC, []),
    FieldInfo("mana_cost_jsonb", FieldType.JSONB_OBJECT, ["mana"]),
    FieldInfo("mana_cost_text", FieldType.TEXT, ["mana"]),
    FieldInfo("raw_card_blob", FieldType.JSONB_OBJECT, []),
    FieldInfo("oracle_text", FieldType.TEXT, ["oracle", "o"]),
]

KNOWN_CARD_ATTRIBUTES = set()
SEARCH_NAME_TO_DB_NAME = {}
DB_NAME_TO_FIELD_TYPE = {}
for col in DB_COLUMNS:
    KNOWN_CARD_ATTRIBUTES.add(col.db_column_name)
    KNOWN_CARD_ATTRIBUTES.update(col.search_aliases)
    SEARCH_NAME_TO_DB_NAME[col.db_column_name] = col.db_column_name
    DB_NAME_TO_FIELD_TYPE[col.db_column_name] = col.field_type
    for ialias in col.search_aliases:
        SEARCH_NAME_TO_DB_NAME[ialias] = col.db_column_name


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
