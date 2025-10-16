"""Enums for the API."""

from __future__ import annotations

import enum


class UniqueOn(enum.StrEnum):
    """Enum for the distinct on column for the search."""
    CARD = enum.auto()
    PRINTING = enum.auto()
    ARTWORK = enum.auto()

    @classmethod
    def from_value(cls, value: str | UniqueOn) -> UniqueOn:
        """Convert a string or UniqueOn to UniqueOn, with a default fallback."""
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except (ValueError, KeyError):
            return cls.CARD

    def get_distinct_on(self) -> str:
        """Get the distinct on column for the search."""
        return _DISTINCT_ON_MAPPING[self]

_DISTINCT_ON_MAPPING = {
    UniqueOn.ARTWORK: "illustration_id",
    UniqueOn.CARD: "card_name",
    UniqueOn.PRINTING: "scryfall_id",
}

class PreferOrder(enum.StrEnum):
    """Enum for the prefer order column for the search."""
    DEFAULT = enum.auto()
    OLDEST = enum.auto()
    NEWEST = enum.auto()
    USD_LOW = enum.auto()
    USD_HIGH = enum.auto()
    PROMO = enum.auto()

    @classmethod
    def from_value(cls, value: str | PreferOrder) -> PreferOrder:
        """Convert a string or PreferOrder to PreferOrder, with a default fallback."""
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except (ValueError, KeyError):
            return cls.DEFAULT

    def get_column_and_direction(self) -> tuple[str, str]:
        """Get the column and direction for the prefer order."""
        return _PREFER_MAPPING[self]

_PREFER_MAPPING = {
    PreferOrder.OLDEST: ("released_at", "ASC"),
    PreferOrder.NEWEST: ("released_at", "DESC"),
    PreferOrder.USD_LOW: ("price_usd", "ASC"),
    PreferOrder.USD_HIGH: ("price_usd", "DESC"),
    PreferOrder.PROMO: ("edhrec_rank", "ASC"),  # Use edhrec_rank as fallback for promo
    PreferOrder.DEFAULT: ("prefer_score", "DESC"),
}

class CardOrdering(enum.StrEnum):
    """Enum for the ordering of the cards."""
    CMC = enum.auto()
    EDHREC = enum.auto()
    POWER = enum.auto()
    RARITY = enum.auto()
    TOUGHNESS = enum.auto()
    USD = enum.auto()

    @classmethod
    def from_value(cls, value: str | CardOrdering) -> CardOrdering:
        """Convert a string or CardOrdering to CardOrdering, with a default fallback.

        Args:
            value: String or CardOrdering instance to convert

        Returns:
            CardOrdering instance, defaulting to EDHREC if conversion fails
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except (ValueError, KeyError):
            return cls.EDHREC

    def get_sql_orderby(self) -> str:
        """Get the SQL column name for this ordering."""
        return _ORDER_MAPPING[self]

_ORDER_MAPPING = {
    CardOrdering.CMC: "cmc",
    CardOrdering.EDHREC: "edhrec_rank",
    CardOrdering.POWER: "creature_power",
    CardOrdering.RARITY: "card_rarity_int",
    CardOrdering.TOUGHNESS: "creature_toughness",
    CardOrdering.USD: "price_usd",
}

class SortDirection(enum.StrEnum):
    """Enum for the direction of the sort."""
    ASC = enum.auto()
    DESC = enum.auto()

    @classmethod
    def from_value(cls, value: str | SortDirection) -> SortDirection:
        """Convert a string or SortDirection to SortDirection, with a default fallback."""
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except (ValueError, KeyError):
            return cls.ASC

    def get_sql_direction(self) -> str:
        """Get the SQL direction for the sort."""
        return self.value.upper()
