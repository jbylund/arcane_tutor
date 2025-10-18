"""Command-line interface for the Gatherer import module."""

from __future__ import annotations

import argparse
import logging
import sys

from gatherer_import.fetcher import GathererFetcher
from gatherer_import.set_converter import SetConverter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def fetch_and_save_set(set_code: str, output_dir: str, *, include_extras: bool = False) -> bool:
    """Fetch and save a single set.

    Args:
    ----
        set_code: The three-letter set code
        output_dir: Directory to save the JSON files
        include_extras: Whether to include extra cards (tokens, emblems, etc.)

    Returns:
    -------
        True if successful, False otherwise

    """
    try:
        logger.info(f"Processing set: {set_code}")

        # Initialize fetcher and converter
        fetcher = GathererFetcher()
        converter = SetConverter(output_dir=output_dir)

        # Fetch set info and cards
        set_info = fetcher.fetch_set_info(set_code)
        cards = fetcher.fetch_set(set_code, include_extras=include_extras)

        if not cards:
            logger.warning(f"No cards found for set {set_code}")
            return False

        # Save to JSON
        created_files = converter.save_set(set_code, cards, set_info)

        logger.info(f"Successfully saved {len(cards)} cards from set {set_code}")
        logger.info(f"Created files: {list(created_files.values())}")

        return True

    except (ValueError, ConnectionError, OSError) as e:
        logger.error(f"Error processing set {set_code}: {e}")
        return False


def fetch_all_sets(output_dir: str, *, include_extras: bool = False) -> dict[str, bool]:
    """Fetch and save all available sets.

    Args:
    ----
        output_dir: Directory to save the JSON files
        include_extras: Whether to include extra cards (tokens, emblems, etc.)

    Returns:
    -------
        Dictionary mapping set codes to success status

    """
    try:
        logger.info("Fetching list of all sets...")

        fetcher = GathererFetcher()
        all_sets = fetcher.fetch_all_sets()

        logger.info(f"Found {len(all_sets)} sets to process")

        results = {}

        for i, set_info in enumerate(all_sets, 1):
            set_code = set_info["code"]
            logger.info(f"Processing set {i}/{len(all_sets)}: {set_code} - {set_info.get('name', 'Unknown')}")

            success = fetch_and_save_set(set_code, output_dir, include_extras=include_extras)
            results[set_code] = success

        # Log summary
        successful = sum(1 for success in results.values() if success)
        logger.info(f"Completed: {successful}/{len(results)} sets processed successfully")

        return results

    except (ConnectionError, OSError) as e:
        logger.error(f"Error fetching all sets: {e}")
        return {}


def main() -> int:
    """Main entry point for the CLI.

    Returns:
    -------
        Exit code (0 for success, 1 for failure)

    """
    parser = argparse.ArgumentParser(
        description="Import Magic: The Gathering card data from Gatherer/Scryfall and save as JSON per set",
    )

    parser.add_argument(
        "--set",
        "-s",
        action="append",
        dest="sets",
        help="Set code to fetch (can be specified multiple times). Example: -s DOM -s WAR",
    )

    parser.add_argument(
        "--all-sets",
        action="store_true",
        help="Fetch all available sets (warning: this will take a long time)",
    )

    parser.add_argument(
        "--output",
        "-o",
        default="./data/sets",
        help="Output directory for JSON files (default: ./data/sets)",
    )

    parser.add_argument(
        "--include-extras",
        action="store_true",
        help="Include extra cards (tokens, emblems, etc.)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all previously saved sets",
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # List saved sets
    if args.list:
        converter = SetConverter(output_dir=args.output)
        saved_sets = converter.list_available_sets()

        if saved_sets:
            for _set_code in saved_sets:
                pass
        else:
            pass

        return 0

    # Validate arguments
    if not args.sets and not args.all_sets:
        parser.print_help()
        return 1

    # Process sets
    if args.all_sets:
        results = fetch_all_sets(args.output, include_extras=args.include_extras)
        failed = sum(1 for success in results.values() if not success)

        if failed > 0:
            logger.warning(f"{failed} sets failed to process")
            return 1

    elif args.sets:
        failed = 0
        for set_code in args.sets:
            success = fetch_and_save_set(set_code, args.output, include_extras=args.include_extras)
            if not success:
                failed += 1

        if failed > 0:
            logger.warning(f"{failed}/{len(args.sets)} sets failed to process")
            return 1

    logger.info("Import completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
