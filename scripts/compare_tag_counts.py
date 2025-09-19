#!/usr/bin/env python3
"""Compare tag counts between scryfall.com and crestcourt.scryfall.com.

This script:
1. Fetches the list of tags from Scryfall
2. Gets the count of cards with that tag from both scryfall.com and local database
3. Orders tags by the number of missing tags on local database
4. Optionally imports tags for cards for the top N tags with most missing cards
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

import requests

# Add the parent directory to the path so we can import from api module
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.api_resource import APIResource

logger = logging.getLogger(__name__)


class TagComparator:
    """Compare tag counts between Scryfall and local database."""

    def __init__(self, crestcourt_base_url: str = "http://localhost:8080") -> None:
        """Initialize the TagComparator.

        Args:
            crestcourt_base_url: Base URL for the local Scryfall OS API
        """
        self.crestcourt_base_url = crestcourt_base_url.rstrip("/")
        self.api_resource = APIResource()
        self.session = requests.Session()

    def get_all_tags(self) -> list[str]:
        """Fetch all available tags from Scryfall.

        Returns:
            List of all available tag names
        """
        logger.info("Fetching all available tags from Scryfall...")
        return self.api_resource.discover_tags_from_scryfall()

    def get_scryfall_card_count(self, tag: str) -> int:
        """Get the number of cards with a specific tag from scryfall.com.

        Args:
            tag: The tag to count cards for

        Returns:
            Number of cards with the tag on scryfall.com
        """
        try:
            cards = self.api_resource._fetch_cards_from_scryfall(tag=tag)
            return len(cards)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get card count for tag '%s' from Scryfall: %s", tag, e)
            return 0

    def get_local_card_count(self, tag: str) -> int:
        """Get the number of cards with a specific tag from local database.

        Args:
            tag: The tag to count cards for

        Returns:
            Number of cards with the tag in local database
        """
        try:
            with self.api_resource._conn_pool.connection() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) as count
                    FROM magic.cards
                    WHERE card_oracle_tags ? %(tag)s
                    """,
                    {"tag": tag},
                )
                result = cursor.fetchone()
                return result["count"] if result else 0
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get card count for tag '%s' from local database: %s", tag, e)
            return 0

    def compare_tag_counts(self, tags: list[str]) -> list[dict[str, Any]]:
        """Compare card counts for all tags between Scryfall and local database.

        Args:
            tags: List of tags to compare

        Returns:
            List of dictionaries with tag comparison data, sorted by missing count descending
        """
        logger.info("Comparing card counts for %d tags...", len(tags))
        comparisons = []

        for i, tag in enumerate(tags, 1):
            if i % 10 == 0:
                logger.info("Processed %d/%d tags", i, len(tags))

            scryfall_count = self.get_scryfall_card_count(tag)
            local_count = self.get_local_card_count(tag)
            missing_count = max(0, scryfall_count - local_count)

            comparisons.append({
                "tag": tag,
                "scryfall_count": scryfall_count,
                "local_count": local_count,
                "missing_count": missing_count,
                "coverage_percent": (local_count / scryfall_count * 100) if scryfall_count > 0 else 100.0,
            })

            # Rate limiting to be respectful to APIs
            time.sleep(0.1)

        # Sort by missing count descending (most missing first)
        comparisons.sort(key=lambda x: x["missing_count"], reverse=True)
        return comparisons

    def import_tags_for_cards(self, tag: str) -> dict[str, Any]:
        """Import cards for a specific tag via the local API endpoint.

        Args:
            tag: The tag to import cards for

        Returns:
            Response data from the import operation
        """
        try:
            url = f"{self.crestcourt_base_url}/update_tagged_cards"
            response = self.session.get(url, params={"tag": tag}, timeout=60)
            response.raise_for_status()
            return response.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to import cards for tag '%s': %s", tag, e)
            return {"error": str(e), "tag": tag}

    def refresh_top_n_tags(self, comparisons: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        """Refresh cards for the top N tags with most missing cards.

        Args:
            comparisons: List of tag comparison data sorted by missing count
            n: Number of top tags to refresh

        Returns:
            List of import results for each tag
        """
        if n <= 0:
            return []

        top_tags = comparisons[:n]
        logger.info("Refreshing cards for top %d tags with most missing cards...", len(top_tags))

        results = []
        for i, tag_data in enumerate(top_tags, 1):
            tag = tag_data["tag"]
            missing_count = tag_data["missing_count"]

            if missing_count == 0:
                logger.info("Tag '%s' has no missing cards, skipping import", tag)
                continue

            logger.info(
                "Importing cards for tag %d/%d: '%s' (missing %d cards)",
                i, len(top_tags), tag, missing_count,
            )

            result = self.import_tags_for_cards(tag)
            result["original_missing_count"] = missing_count
            results.append(result)

            # Rate limiting between imports
            time.sleep(1)

        return results


def main() -> None:
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Compare tag counts and refresh top N tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Just compare counts, no imports
  %(prog)s --dry-run

  # Compare and import top 10 tags with most missing cards
  %(prog)s --top-n 10

  # Compare and import top 5 tags, use custom API URL
  %(prog)s --top-n 5 --api-url http://localhost:8080
        """,
    )

    parser.add_argument(
        "--top-n",
        type=int,
        default=0,
        help="Number of top tags to refresh (0 = dry run only)",
    )

    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8080",
        help="Base URL for the local Scryfall OS API (default: http://localhost:8080)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only compare counts, don't import any tags",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Set up logging
    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        # Initialize comparator
        comparator = TagComparator(crestcourt_base_url=args.api_url)

        # Get all tags
        tags = comparator.get_all_tags()

        # Compare counts
        comparisons = comparator.compare_tag_counts(tags)

        # Print summary

        for _tag_data in comparisons[:20]:
            pass

        # Calculate overall statistics
        total_scryfall = sum(c["scryfall_count"] for c in comparisons)
        total_local = sum(c["local_count"] for c in comparisons)
        sum(c["missing_count"] for c in comparisons)
        (total_local / total_scryfall * 100) if total_scryfall > 0 else 100.0


        # Import top N tags if requested
        top_n = args.top_n if not args.dry_run else 0

        if top_n > 0:
            results = comparator.refresh_top_n_tags(comparisons, top_n)

            for result in results:
                result.get("tag", "unknown")
                result.get("cards_updated", 0)
                result.get("original_missing_count", 0)

                if "error" in result:
                    pass
                else:
                    pass
        else:
            pass

    except Exception as e:
        logger.error("Script failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
