"""Card processing functions."""
from __future__ import annotations

import copy
import functools
import re
from typing import TYPE_CHECKING, Any

from api.parsing.scryfall_nodes import calculate_devotion, mana_cost_str_to_dict

if TYPE_CHECKING:
    from collections.abc import Callable


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


def merge_dfc_faces(card: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912, PLR0915, C901
    """Merge card faces for double-faced cards.

    This function extracts data from card_faces and merges it into the main card
    structure so that searches can find cards with properties on either face.

    Args:
        card: The card dictionary with card_faces.

    Returns:
        The card dictionary with merged face data.
    """
    if "card_faces" not in card:
        return card

    faces = card["card_faces"]

    # Union types and subtypes from both faces
    all_types = []
    all_subtypes = []
    all_keywords = []
    all_colors = set()
    powers = []
    toughnesses = []
    loyalties = []

    for face in faces:
        # Parse type line from each face
        face_type_line = face.get("type_line", "")
        if face_type_line:
            face_types, face_subtypes = parse_type_line(face_type_line)
            all_types.extend(face_types)
            all_subtypes.extend(face_subtypes)

        # Merge keywords
        face_keywords = face.get("keywords", [])
        all_keywords.extend(face_keywords)

        # Merge colors from each face
        face_colors = face.get("colors", [])
        all_colors.update(face_colors)

        # Collect power/toughness/loyalty values
        if "power" in face:
            powers.append(face["power"])
        if "toughness" in face:
            toughnesses.append(face["toughness"])
        if "loyalty" in face:
            loyalties.append(face["loyalty"])

    # Remove duplicates while preserving order
    seen_types = set()
    unique_types = []
    for t in all_types:
        if t not in seen_types:
            seen_types.add(t)
            unique_types.append(t)

    seen_subtypes = set()
    unique_subtypes = []
    for s in all_subtypes:
        if s not in seen_subtypes:
            seen_subtypes.add(s)
            unique_subtypes.append(s)

    seen_keywords = set()
    unique_keywords = []
    for k in all_keywords:
        if k not in seen_keywords:
            seen_keywords.add(k)
            unique_keywords.append(k)

    # Store merged type_line for parsing later
    # Combine all unique types and subtypes
    if unique_subtypes:
        card["type_line"] = f"{' '.join(unique_types)} \u2014 {' '.join(unique_subtypes)}"
    else:
        card["type_line"] = " ".join(unique_types)

    # Store merged keywords
    if not card.get("keywords"):
        card["keywords"] = unique_keywords
    else:
        # Merge with existing keywords
        existing = set(card["keywords"])
        existing.update(unique_keywords)
        card["keywords"] = list(existing)

    # Store power/toughness/loyalty values
    # For numeric fields, use the first face's value (will be converted to int later)
    # For text fields, store all values for display
    if powers:
        # Use first face's power for numeric comparison
        card["power"] = powers[0]
        # But store all values in text format for reference
        if len(powers) > 1:
            # Store a note that there are multiple values
            card["_dfc_powers"] = powers
    if toughnesses:
        card["toughness"] = toughnesses[0]
        if len(toughnesses) > 1:
            card["_dfc_toughnesses"] = toughnesses
    if loyalties:
        card["loyalty"] = loyalties[0]
        if len(loyalties) > 1:
            card["_dfc_loyalties"] = loyalties

    # Take mana_cost from first face if not present on card
    if not card.get("mana_cost") and faces:
        first_face_mana = faces[0].get("mana_cost")
        if first_face_mana:
            card["mana_cost"] = first_face_mana

    # Take oracle_text from first face if not present
    if not card.get("oracle_text") and faces:
        # Combine oracle text from all faces
        oracle_texts = [face.get("oracle_text", "") for face in faces if face.get("oracle_text")]
        if oracle_texts:
            card["oracle_text"] = "\n---\n".join(oracle_texts)

    # Set colors at top level if not present (from merged face colors)
    if not card.get("colors"):
        card["colors"] = list(all_colors)

    return card

def maybeify(func: Callable) -> Callable:
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


def rarity_text_to_int(rarity_text: str) -> int:
    """Convert rarity text to int."""
    rarity_map = {
        "common": 0,
        "uncommon": 1,
        "rare": 2,
        "mythic": 3,
        "special": 4,
        "bonus": 5,
    }
    return rarity_map.get(rarity_text.lower(), -1)


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


def preprocess_card(card: dict[str, Any]) -> None | dict[str, Any]:  # noqa: PLR0915, PLR0912
    """Preprocess a card to remove invalid cards and add necessary fields."""
    if set(card["legalities"].values()) == {"not_legal"}:
        return None
    if "paper" not in card["games"]:
        return None
    if card.get("set_type") == "funny":
        return None

    if "raw_card_blob" in card:
        return card

    # Store the original card data before modifications for raw_card_blob
    raw_card_data = copy.deepcopy(card)
    card["raw_card_blob"] = raw_card_data
    card["scryfall_id"] = card["id"]

    # Handle double-faced cards by merging face data
    has_card_faces = "card_faces" in card
    if has_card_faces:
        card = merge_dfc_faces(card)

    card_types, card_subtypes = parse_type_line(card["type_line"])
    card["card_types"] = card_types
    card["card_subtypes"] = card_subtypes

    card["creature_power"] = maybe_int(card.get("power"))
    card["creature_toughness"] = maybe_int(card.get("toughness"))
    card["planeswalker_loyalty"] = maybe_int(card.get("loyalty"))

    # objects of keys to true
    card["card_colors"] = dict.fromkeys(card["colors"], True)
    card["card_color_identity"] = dict.fromkeys(card["color_identity"], True)
    card["card_keywords"] = dict.fromkeys(card.get("keywords", []), True)
    card["produced_mana"] = dict.fromkeys(card.get("produced_mana", []), True)

    card["edhrec_rank"] = card.get("edhrec_rank")

    # Extract frame data - combine frame version and frame effects into single JSONB object
    frame_data = {}
    # Add frame version if present (titlecased for consistency)
    frame_version = card.get("frame")
    if frame_version:
        frame_data[frame_version.title()] = True
    # Add frame effects if present (titlecased for consistency)
    frame_effects = card.get("frame_effects", [])
    for effect in frame_effects:
        frame_data[effect.title()] = True
    card["card_frame_data"] = frame_data

    # Extract pricing data if available - ensure they are floats for jsonb_populate_record
    prices = card.get("prices", {})
    card["price_usd"] = maybe_float(prices.get("usd"))
    card["price_eur"] = maybe_float(prices.get("eur"))
    card["price_tix"] = maybe_float(prices.get("tix"))

    # Extract set code for dedicated column
    card["card_set_code"] = card.get("set")

    # Extract layout and border for dedicated columns (lowercased for case-insensitive search)
    if "layout" in card:
        card["card_layout"] = card["layout"].lower()
    if "border_color" in card:
        card["card_border"] = card["border_color"].lower()
    if "watermark" in card:
        card["card_watermark"] = card["watermark"].lower()

    mana_cost_text = card.get("mana_cost", "")
    card["mana_cost_jsonb"] = mana_cost_str_to_dict(mana_cost_text)
    card["devotion"] = calculate_devotion(mana_cost_text)

    # Map field names to match database column names for jsonb_populate_record
    card["card_name"] = card.get("name")
    card["mana_cost_text"] = card.get("mana_cost")
    card["creature_power_text"] = card.get("power")
    card["creature_toughness_text"] = card.get("toughness")
    card["planeswalker_loyalty_text"] = card.get("loyalty")
    card["card_artist"] = card.get("artist")

    # Handle CMC and edhrec_rank conversion using helper function
    card["cmc"] = maybe_int(card.get("cmc"))

    # Handle rarity conversion - implement in Python to avoid SQL boilerplate
    rarity_text = card.get("rarity", "").lower()
    if rarity_text:
        card["card_rarity_text"] = rarity_text
        card["card_rarity_int"] = rarity_text_to_int(rarity_text)

    # Handle collector number - implement extraction in Python to avoid SQL boilerplate
    collector_number = card.get("collector_number")
    card["collector_number"] = collector_number
    card["collector_number_int"] = extract_collector_number_int(collector_number)
    card["illustration_id"] = card.get("illustration_id")

    # Handle legalities and produced_mana defaults
    card.setdefault("card_legalities", card.get("legalities", {}))

    # Ensure all NOT NULL DEFAULT fields are set to avoid constraint violations
    for key in ["produced_mana", "card_oracle_tags", "card_is_tags"]:
        card.setdefault(key, {})

    # Add is:dfc tag for double-faced cards
    if has_card_faces:
        card["card_is_tags"]["dfc"] = True
        # Add layout-specific tag if available
        layout = card.get("layout", "").lower()
        if layout:
            # Add the layout as an is: tag (e.g., is:modal-dfc, is:transform)
            card["card_is_tags"][layout] = True

    return card
