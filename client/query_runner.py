#!/usr/bin/env python3
"""Client script to generate random queries and run them against the API.

This script continuously generates random card search queries and executes them
against the Scryfall OS API to help identify which database indexes are being used
and which queries perform well or poorly.

Queries come from `client.query_sampler`, the same universe the cost-model benches
draw from. Without `--corpus` the sampler uses its built-in fallback vocabulary,
which is what the container gets; point `--corpus` at a printing-corpus JSONL to
draw values from real data instead.
"""

import argparse
import logging
import os
import pathlib
import random
import time

import requests

from client.query_sampler import MODES, QuerySampler

logger = logging.getLogger(__name__)
# Constants
DEFAULT_API_URL = "http://apiservice:8080"
DEFAULT_QUERY_DELAY = 1.0  # Delay between queries in seconds
DEFAULT_BATCH_SIZE = 50  # Number of queries before reporting stats
DEFAULT_MODE = "realistic"  # Load generation wants plausible traffic, not flat exploration
RESULT_LIMIT = 100  # Page size requested per query


def setup_logging() -> None:
    """Set up logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def run_query(api_url: str, query: str, session: requests.Session, **params: str | int) -> dict:
    """Run a single search query against the API.

    Args:
        api_url: Base URL for the API.
        query: The search query string.
        session: Requests session for API calls.
        **params: Result-shaping parameters (unique, orderby, prefer, direction, offset) as
            returned by `QuerySampler.params`. Passed through to the endpoint as given.

    Returns:
        Dictionary with query results and timing information.
    """
    before = time.monotonic()
    result = {
        "query": query,
    }

    try:
        response = session.get(
            f"{api_url}/search",
            params={"q": query, "limit": RESULT_LIMIT, **params},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        result["success"] = True
        card_count = len(data.get("cards", []))
        result["card_count"] = card_count
        result["execute_ms"] = (data.get("inner_timings") or {}).get("execute_query")
    except requests.RequestException as oops:
        result["success"] = False
        result["error"] = str(oops)
    finally:
        elapsed = time.monotonic() - before
        elapsed_ms = 1000 * elapsed
        result["elapsed_ms"] = elapsed_ms

    if result["success"]:
        execute_ms = result.get("execute_ms")
        execute_str = f" | DB execute: {execute_ms:.1f}ms" if execute_ms is not None else ""
        logging.info(
            "Query: '%s' %s | HTTP: %.1fms%s | Cards: %d",
            query,
            " ".join(f"{k}={v}" for k, v in params.items()),
            elapsed_ms,
            execute_str,
            card_count,
        )
    else:
        logging.error(
            "Query failed: '%s' | Duration: %.1fms | Error: %s",
            query,
            elapsed_ms,
            result["error"],
        )

    return result


def print_statistics(results: list[dict]) -> None:
    """Print statistics about the query results.

    Args:
        results: List of query result dictionaries.
    """
    if not results:
        return

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    total_queries = len(results)
    success_rate = (len(successful) / total_queries * 100) if total_queries > 0 else 0

    if successful:
        durations = [r["elapsed_ms"] for r in successful]
        avg_duration = sum(durations) / len(durations)
        min_duration = min(durations)
        max_duration = max(durations)

        total_cards = sum(r["card_count"] for r in successful)

        execute_times = [r["execute_ms"] for r in successful if r.get("execute_ms") is not None]
        execute_str = ""
        if execute_times:
            execute_str = f"\n  Avg DB execute: {sum(execute_times) / len(execute_times):.1f}ms | Max: {max(execute_times):.1f}ms"

        logger.info("=" * 60)
        logger.info("Statistics for %d queries:", total_queries)
        logger.info("  Success rate: %.1f%%", success_rate)
        logger.info("  Successful queries: %d", len(successful))
        logger.info("  Failed queries: %d", len(failed))
        logger.info("  Total cards returned: %d", total_cards)
        logger.info(
            "  Avg HTTP duration: %.1fms | Min: %.1fms | Max: %.1fms%s", avg_duration, min_duration, max_duration, execute_str
        )
        logger.info("=" * 60)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments, falling back to environment variables then module defaults."""
    parser = argparse.ArgumentParser(description="Run search queries against the Sylvan Librarian API.")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("API_URL", DEFAULT_API_URL),
        help=f"Base URL for the API (default: {DEFAULT_API_URL}).",
    )
    parser.add_argument(
        "--query-delay",
        type=float,
        default=float(os.environ.get("QUERY_DELAY", DEFAULT_QUERY_DELAY)),
        help=f"Seconds to wait between queries (default: {DEFAULT_QUERY_DELAY}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("BATCH_SIZE", DEFAULT_BATCH_SIZE)),
        help=f"Queries per stats report (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--corpus",
        type=pathlib.Path,
        default=os.environ.get("CORPUS") or None,
        help="Printing-corpus JSONL to draw query values from (default: the sampler's built-in vocabulary).",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=os.environ.get("QUERY_MODE", DEFAULT_MODE),
        help=f"Sampler weighting (default: {DEFAULT_MODE}).",
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed the query stream for a reproducible run.")
    return parser.parse_args()


def main() -> None:
    """Main function to continuously run random queries."""
    setup_logging()

    args = parse_args()
    api_url = args.api_url
    query_delay = args.query_delay
    batch_size = args.batch_size

    logger.info("Starting query runner against API: %s", api_url)
    logger.info("Query delay: %ss", query_delay)
    logger.info("Batch size: %d", batch_size)
    logger.info("Sampler: mode=%s corpus=%s seed=%s", args.mode, args.corpus or "built-in", args.seed)

    sampler = QuerySampler(corpus=args.corpus, mode=args.mode)
    rng = random.Random(args.seed)

    # Create a session for HTTP requests
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "ScryfallosQueryRunner/1.0",
        },
    )

    results = []
    query_count = 0

    try:
        while True:
            # Shaped rather than flat: ORs, nested parens and negations are separate engine paths,
            # and load generation is where they should be getting exercised.
            query = sampler.structured_query(rng)["query"]

            # Run the query
            result = run_query(api_url, query, session, **sampler.params(rng))
            results.append(result)
            query_count += 1

            # Print statistics after each batch
            if query_count % batch_size == 0:
                print_statistics(results)
                results = []

            # Delay before next query
            time.sleep(query_delay)

    except KeyboardInterrupt:
        logger.info("Shutting down query runner...")
        if results:
            print_statistics(results)


if __name__ == "__main__":
    main()
