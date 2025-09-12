from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object"
    NUMERIC = "numeric"
    TEXT = "text"


class FieldInfo:
    def __init__(self, db_column_name: str, field_type: FieldType, search_aliases: list[str]) -> None:
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
    # FieldInfo("creature_power_text", FieldType.TEXT, ["power", "pow"]),
    FieldInfo("creature_toughness", FieldType.NUMERIC, ["toughness", "tou"]),
    # FieldInfo("creature_toughness_text", FieldType.TEXT, ["toughness", "tou"]),
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


# Keywords will be fetched dynamically from the database
# This reduces maintenance and ensures the keyword list is always up to date
_CACHED_KEYWORDS = None


def get_magic_keywords():
    """Get magic keywords from cache or database.
    
    This function will be called by the API resource to populate keywords
    from the database and cache them for performance.
    """
    global _CACHED_KEYWORDS
    if _CACHED_KEYWORDS is None:
        # Fallback keywords for development/testing when database is not available
        _CACHED_KEYWORDS = {
            "Adapt", "Addendum", "Affinity", "Afterlife", "Aftermath", "Ascend", "Assist", "Awaken",
            "Battalion", "Bestow", "Bloodthirst", "Boast", "Buyback", "Cascade", "Channel", "Cipher",
            "Cleave", "Conspire", "Convoke", "Crew", "Cumulative upkeep", "Cycling", "Dash", "Deathtouch",
            "Defender", "Delve", "Disturb", "Double strike", "Dredge", "Echo", "Embalm", "Emerge",
            "Entwine", "Epic", "Escape", "Eternalize", "Evoke", "Exploit", "Explorer", "Fading",
            "Fear", "First strike", "Flanking", "Flash", "Flashback", "Flying", "Foretell", "Haste",
            "Hexproof", "Hideaway", "Horsemanship", "Indestructible", "Infect", "Intimidate", "Jump-start",
            "Kicker", "Landfall", "Lifelink", "Madness", "Menace", "Miracle", "Morph", "Multikicker",
            "Myriad", "Overload", "Partner", "Persist", "Phasing", "Plainswalk", "Poisonous", "Protection",
            "Prowess", "Prowl", "Reach", "Rebound", "Regenerate", "Reinforce", "Renown", "Replicate",
            "Retrace", "Riot", "Scavenge", "Shadow", "Shroud", "Skulk", "Storm", "Sunburst", "Suspend",
            "Totem armor", "Trample", "Transform", "Transmute", "Tribute", "Undying", "Unearth", "Unleash",
            "Vanishing", "Vigilance", "Wither"
        }
    return _CACHED_KEYWORDS


def set_magic_keywords(keywords):
    """Set the cached keywords from database results."""
    global _CACHED_KEYWORDS
    _CACHED_KEYWORDS = keywords
