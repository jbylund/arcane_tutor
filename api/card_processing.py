"""Card processing functions."""
from __future__ import annotations

import functools
import re
from typing import Any


def extract_image_location_uuid(card: dict[str, Any]) -> str:
    """Extract the image location UUID from a card."""
    for image_location in card.get("image_uris", {}).values():
        if ".jpg" in image_location:
            return image_location.rpartition("/")[-1].partition(".")[0]
    msg = f"No image location found for card: {card}"
    raise AssertionError(msg)

def parse_type_line(type_line: str) -> tuple[list[str], list[str]]:
    """Parse the type line of a card."""
    card_types, _, card_subtypes = (x.strip().split() for x in type_line.title().partition("\u2014"))
    return card_types, card_subtypes or []

def maybeify(func: callable) -> callable:
    """Convert value to int (via float first), returning None if conversion fails."""
    @functools.wraps(func)
    def wrapper(val: str | int | float | None) -> int | None:
        if val is None:
            return None
        try:
            return func(val)
        except (ValueError, TypeError):
            return None
    return wrapper

@maybeify
def maybe_float(val: str | int | float | None) -> float | None:
    """Convert value to float, returning None if conversion fails."""
    return float(val)

@maybeify
def maybe_int(val: str | int | float | None) -> int | None:
    """Convert value to int (via float first), returning None if conversion fails."""
    return int(float(val))


def extract_collector_number_int(collector_number: str | int | float | None) -> int | None:
    """Extract the integer part of a collector number."""
    if collector_number is None:
        return None
    # Implement magic.extract_collector_number_int in Python
    # Extract numeric characters using regex, similar to the database function
    numeric_part = re.sub(r"[^0-9]", "", str(collector_number))
    if numeric_part:
        try:
            int_val = int(numeric_part)
            # PostgreSQL integer range is -2^31 to 2^31-1
            if -2**31 <= int_val <= 2**31-1:
                return int_val
        except (ValueError, OverflowError):
            pass
    return None  # Field will be null by default
