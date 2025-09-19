#!/usr/bin/env python3
"""Script to find missing cards by comparing Scryfall API with local database.

This script generates queries for different combinations of colors, CMCs, and creature types,
searches both Scryfall and the local database, and identifies cards present in Scryfall
but missing from the local database.
"""

import itertools
import logging
import time

import requests

# Constants
NOT_FOUND_STATUS = 404


def setup_logging() -> None:
    """Set up logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def generate_search_queries() -> list[str]:
    """Generate search queries for finding missing cards.

    Returns:
        List of search query strings.
    """
    colors = "wubrg"
    cmcs = list(range(10))

    queries = []

    for color, cmc in itertools.product(colors, cmcs):
        # Base query with filters
        query_parts = [
            f"color:{color}",
            f"cmc={cmc}",
        ]

        query = " ".join(query_parts)
        queries.append(query)

    return queries


def search_scryfall(query: str, session: requests.Session) -> set[str]:
    """Search Scryfall API for cards matching the query.

    Args:
        query: The search query string.
        session: Requests session for API calls.

    Returns:
        Set of card names found in Scryfall.
    """
    base_url = "https://api.scryfall.com/cards/search"
    # x
    extra_params = [
            "-is:dfc",
            "-is:adventure",
            "-is:split",
            "game:paper",
            "(f:m or f:l or f:c or f:v)",
    ]
    extra_params_str = " ".join(extra_params)
    full_query = f"({query}) {extra_params_str}"

    params = {"q": full_query, "format": "json"}
    card_names = set()

    try:
        while True:
            # Rate limiting - Scryfall allows 10 requests per second
            time.sleep(0.2)

            response = session.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if "data" not in data:
                break

            # Extract card names from current page
            page_card_names = {card.get("name") for card in data["data"] if card.get("name")}
            card_names.update(page_card_names)

            # Check if there are more pages
            if not data.get("has_more", False):
                break

            # Get next page URL
            next_page = data.get("next_page")
            if not next_page:
                break

            # Update base_url and clear params for next page
            base_url = next_page
            params = {}

    except requests.RequestException as e:
        if hasattr(e, "response") and e.response.status_code == NOT_FOUND_STATUS:
            # No results found for this query
            return card_names
        logging.error(f"Error searching Scryfall for query '{query}': {e}")
        raise

    return card_names


def search_local_database(query: str, api_base_url: str, session: requests.Session) -> set[str]:
    """Search local database for cards matching the query.

    Args:
        query: The search query string.
        api_base_url: Base URL for the local API.
        session: Requests session for API calls.

    Returns:
        Set of card names found in local database.
    """
    api_base_url = api_base_url.rstrip("/")
    try:
        response = session.get(
            f"{api_base_url}/search",
            params={"q": query, "limit": 2000},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        cards = data["cards"]
        return {card.get("name") for card in cards if card.get("name")}

    except Exception as e:
        logging.error(f"Error searching local database for query '{query}': {e}", exc_info=True)
        return set()


def import_missing_card(card_name: str, api_base_url: str, session: requests.Session) -> bool:
    """Import a missing card using the local API endpoint.

    Args:
        card_name: Name of the card to import.
        api_base_url: Base URL for the local API.
        session: Requests session for API calls.

    Returns:
        True if import was successful, False otherwise.
    """
    try:
        response = session.post(
            f"{api_base_url}/import_card_by_name",
            params={"card_name": card_name},
            timeout=30,
        )
        response.raise_for_status()
        return True

    except requests.RequestException as e:
        logging.error(f"Error importing card '{card_name}': {e}")
        return False


def main() -> None:
    """Main function to find and import missing cards."""
    setup_logging()

    # Configuration
    local_api_base = "http://127.0.0.1:18080/"  # Could be localhost:8080 for local testing

    # Create a session for HTTP requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": "ScryfallosCardFinder/1.0",
    })

    logging.info("Starting missing card detection process...")

    # Generate all search queries
    queries = generate_search_queries()
    logging.info(f"Generated {len(queries)} search queries")

    total_missing = 0
    total_imported = 0

    for i, query in enumerate(queries):
        logging.info(f"Processing query {i+1}/{len(queries)}: {query}")

        try:
            # Search both APIs
            local_cards = search_local_database(query, local_api_base, session)
            if not local_cards:
                logging.warning("Got back an empty response for: %s", query)
                continue
            scryfall_cards = search_scryfall(query, session)

            # Find missing cards
            missing_cards = scryfall_cards - local_cards

            if missing_cards:
                logging.info(f"Found {len(missing_cards)} missing cards for query: {query}")
                total_missing += len(missing_cards)

                # Import missing cards
                for card_name in missing_cards:
                    logging.info(f"Importing missing card: {card_name}")
                    if import_missing_card(card_name, local_api_base, session):
                        total_imported += 1
                        logging.info(f"Successfully imported: {card_name}")
                    else:
                        logging.error(f"Failed to import: {card_name}")

            else:
                logging.info(f"No missing cards found for query: {query}")

        except (requests.RequestException, ValueError, KeyError) as e:
            logging.error(f"Error processing query '{query}': {e}")
            break

    logging.info(f"Process complete. Total missing cards found: {total_missing}")
    logging.info(f"Total cards successfully imported: {total_imported}")


if __name__ == "__main__":
    main()
