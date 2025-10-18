"""Module for converting and saving card data to JSON files organized by set."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

import orjson

logger = logging.getLogger(__name__)


class SetConverter:
    """Converter class for processing and saving card data by set.

    This class handles converting card data to JSON format and organizing
    it into files per set.
    """

    def __init__(self: SetConverter, output_dir: str | pathlib.Path = "./data/sets") -> None:
        """Initialize the SetConverter.

        Args:
        ----
            output_dir: Directory where set JSON files will be saved

        """
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"SetConverter initialized with output directory: {self.output_dir}")

    def save_set(
        self: SetConverter,
        set_code: str,
        cards: list[dict[str, Any]],
        set_info: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Save cards from a set to JSON files.

        Creates a directory for the set and saves:
        - All card data in {set_code}.json
        - Set metadata in metadata.json (if provided)

        Args:
        ----
            set_code: The three-letter set code (e.g., "DOM")
            cards: List of card dictionaries
            set_info: Optional dictionary with set metadata

        Returns:
        -------
            Dictionary with paths to created files

        """
        # Create set directory
        set_dir = self.output_dir / set_code.upper()
        set_dir.mkdir(parents=True, exist_ok=True)

        created_files = {}

        # Save all cards to a single JSON file
        cards_file = set_dir / f"{set_code.upper()}.json"
        logger.info(f"Writing {len(cards)} cards to {cards_file}")

        with cards_file.open("wb") as f:
            f.write(orjson.dumps(cards, option=orjson.OPT_INDENT_2))

        created_files["cards"] = str(cards_file)
        logger.info(f"Successfully saved {len(cards)} cards to {cards_file}")

        # Save metadata if provided
        if set_info:
            metadata_file = set_dir / "metadata.json"
            logger.info(f"Writing set metadata to {metadata_file}")

            with metadata_file.open("wb") as f:
                f.write(orjson.dumps(set_info, option=orjson.OPT_INDENT_2))

            created_files["metadata"] = str(metadata_file)
            logger.info(f"Successfully saved set metadata to {metadata_file}")

        return created_files

    def save_multiple_sets(
        self: SetConverter,
        sets_data: dict[str, tuple[list[dict[str, Any]], dict[str, Any] | None]],
    ) -> dict[str, dict[str, str]]:
        """Save multiple sets at once.

        Args:
        ----
            sets_data: Dictionary mapping set codes to (cards, set_info) tuples

        Returns:
        -------
            Dictionary mapping set codes to their created file paths

        """
        results = {}

        for set_code, (cards, set_info) in sets_data.items():
            try:
                created_files = self.save_set(set_code, cards, set_info)
                results[set_code] = created_files
                logger.info(f"Successfully saved set {set_code}")
            except (OSError, TypeError, ValueError) as e:
                logger.error(f"Error saving set {set_code}: {e}")
                results[set_code] = {"error": str(e)}

        return results

    def load_set(self: SetConverter, set_code: str) -> list[dict[str, Any]]:
        """Load cards from a previously saved set.

        Args:
        ----
            set_code: The three-letter set code (e.g., "DOM")

        Returns:
        -------
            List of card dictionaries

        Raises:
        ------
            FileNotFoundError: If the set file doesn't exist
            ValueError: If the JSON is invalid

        """
        set_dir = self.output_dir / set_code.upper()
        cards_file = set_dir / f"{set_code.upper()}.json"

        if not cards_file.exists():
            msg = f"Set file not found: {cards_file}"
            raise FileNotFoundError(msg)

        logger.info(f"Loading cards from {cards_file}")

        with cards_file.open("rb") as f:
            cards = orjson.loads(f.read())

        logger.info(f"Successfully loaded {len(cards)} cards from {cards_file}")
        return cards

    def list_available_sets(self: SetConverter) -> list[str]:
        """List all set codes that have been saved.

        Returns:
        -------
            List of set codes (directory names)

        """
        if not self.output_dir.exists():
            return []

        # Get all directories in output_dir
        set_dirs = [d.name for d in self.output_dir.iterdir() if d.is_dir()]
        logger.info(f"Found {len(set_dirs)} saved sets")
        return sorted(set_dirs)
