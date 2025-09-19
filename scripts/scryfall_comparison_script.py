#!/usr/bin/env python3
"""Scryfall API Comparison Script.

This script compares search results between:
1. Official Scryfall API (api.scryfall.com)
2. Scryfall OS API (scryfall.crestcourt.com)

It analyzes:
- Number of results returned
- Position correlation within results
- Failures or major discrepancies

Usage: python scryfall_comparison_script.py
"""

import logging
import tempfile
import time
from dataclasses import dataclass

import requests
import tenacity

# Constants for magic values
HTTP_NOT_FOUND = 404
CORRELATION_THRESHOLD_LOW = 0.3
CORRELATION_THRESHOLD_MEDIUM = 0.5
RESULT_DIFF_THRESHOLD = 0.5
MAX_DISPLAYED_CARDS = 5

retryer = tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=0.1, min=0.1, max=2) + tenacity.wait_random(0, 0.1),
    reraise=True,
    stop=tenacity.stop_after_attempt(7),
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

@dataclass
class SearchResult:
    """Container for search result data."""
    query: str
    total_cards: int
    card_names: list[str]
    success: bool
    error_message: str | None = None

@dataclass
class ComparisonResult:
    """Container for comparison analysis."""
    query: str
    official_result: SearchResult
    local_result: SearchResult
    result_count_diff: int
    position_correlation: float
    major_discrepancy: bool
    notes: list[str]
    local_only_cards: list[tuple[str, int]]  # (card_name, position)
    official_only_cards: list[tuple[str, int]]  # (card_name, position)
    local_total_cards: int

class ScryfallAPIComparator:
    """Compares search results between official Scryfall and local implementation."""

    def __init__(self) -> None:
        """Initialize the API comparator with default settings."""
        self.official_base_url = "https://api.scryfall.com"
        self.local_base_url = "https://scryfall.crestcourt.com"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ScryfallOSComparison/1.0",
        })

    def search_official_scryfall(self, query: str, limit: int = 100) -> SearchResult:
        """Search using official Scryfall API."""
        try:
            url = f"{self.official_base_url}/cards/search"
            params = {
                "q": f"({query}) -is:dfc -is:adventure -is:split game:paper (f:m or f:l or f:c or f:v)",
                "order": "edhrec",
                "dir": "asc",
                "page": 1,
            }

            logger.info(f"Searching official Scryfall: {query}")

            @retryer
            def get_response() -> requests.Response:
                """Get response with retry logic."""
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code not in [200, HTTP_NOT_FOUND]:
                    response.raise_for_status()
                return response

            response = get_response()

            if response.status_code == HTTP_NOT_FOUND:
                # No results found
                return SearchResult(query=query, total_cards=0, card_names=[], success=True)

            data = response.json()
            total_cards = data.get("total_cards", 0)
            card_names = [card.get("name", "") for card in data.get("data", [])]

            return SearchResult(
                query=query,
                total_cards=total_cards,
                card_names=card_names[:limit],
                success=True,
            )

        except (requests.RequestException, KeyError, ValueError) as e:
            logger.error(f"Error searching official Scryfall for '{query}': {e}")
            return SearchResult(
                query=query,
                total_cards=0,
                card_names=[],
                success=False,
                error_message=str(e),
            )

    def search_local_scryfall(self, query: str) -> SearchResult:
        """Search using local Scryfall OS implementation."""
        try:
            url = f"{self.local_base_url}/search"
            params = {
                "q": query,
                "orderby": "edhrec",
                "direction": "asc",
            }

            logger.info(f"Searching local Scryfall: {query}")

            @retryer
            def get_response() -> requests.Response:
                """Get response with retry logic."""
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response

            response = get_response()
            data = response.json()
            cards = data.pop("cards")
            total_cards = data["total_cards"]
            card_names = [card["name"] for card in cards]

            return SearchResult(
                query=query,
                total_cards=total_cards,
                card_names=card_names,
                success=True,
            )

        except (requests.RequestException, KeyError, ValueError) as e:
            logger.error(f"Error searching local Scryfall for '{query}': {e}")
            return SearchResult(
                query=query,
                total_cards=0,
                card_names=[],
                success=False,
                error_message=str(e),
            )

    def calculate_position_correlation(self, list1: list[str], list2: list[str]) -> float:
        """Calculate correlation between card positions in two result lists."""
        if not list1 or not list2:
            return 0.0

        # Find common cards and their positions
        common_cards = set(list1) & set(list2)
        if not common_cards:
            return 0.0

        correlations = []
        for card in common_cards:
            try:
                pos1 = list1.index(card)
                pos2 = list2.index(card)
                # Simple position similarity: 1.0 if same position, decreasing with distance
                max_pos = max(len(list1), len(list2))
                correlation = 1.0 - (abs(pos1 - pos2) / max_pos)
                correlations.append(correlation)
            except ValueError:
                continue

        return sum(correlations) / len(correlations) if correlations else 0.0

    def _find_unique_cards(self, official_cards: list[str], local_cards: list[str]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        """Find cards unique to each result set with their positions."""
        official_cards_set = set(official_cards)
        local_cards_set = set(local_cards)

        # Get local-only cards with their positions
        local_only_cards = []
        for i, card in enumerate(local_cards):
            if card not in official_cards_set:
                local_only_cards.append((card, i))
                if len(local_only_cards) >= MAX_DISPLAYED_CARDS:
                    break

        # Get official-only cards with their positions
        official_only_cards = []
        for i, card in enumerate(official_cards):
            if card not in local_cards_set:
                official_only_cards.append((card, i))
                if len(official_only_cards) >= MAX_DISPLAYED_CARDS:
                    break

        return local_only_cards, official_only_cards

    def _determine_major_discrepancy(self, official_result: SearchResult, local_result: SearchResult,
                                   result_count_diff: int, position_correlation: float) -> bool:
        """Determine if there's a major discrepancy between results."""
        return (
            not official_result.success or
            not local_result.success or
            result_count_diff > max(official_result.total_cards, local_result.total_cards) * RESULT_DIFF_THRESHOLD or
            position_correlation < CORRELATION_THRESHOLD_LOW
        )

    def _generate_notes(self, official_result: SearchResult, local_result: SearchResult,
                       result_count_diff: int, position_correlation: float) -> list[str]:
        """Generate notes about the comparison."""
        notes = []
        if not official_result.success:
            notes.append(f"Official API failed: {official_result.error_message}")
        if not local_result.success:
            notes.append(f"Local API failed: {local_result.error_message}")
        if result_count_diff > 0:
            notes.append(f"Result count difference: {result_count_diff}")
        if position_correlation < CORRELATION_THRESHOLD_MEDIUM:
            notes.append(f"Low position correlation: {position_correlation:.2f}")
        return notes

    def compare_results(self, query: str) -> ComparisonResult:
        """Compare search results between official and local APIs."""
        time.sleep(0.5)  # Rate limiting
        official_result = self.search_official_scryfall(query)
        local_result = self.search_local_scryfall(query)

        # Calculate metrics
        result_count_diff = abs(official_result.total_cards - local_result.total_cards)
        position_correlation = self.calculate_position_correlation(
            official_result.card_names,
            local_result.card_names,
        )

        # Find unique cards with their positions
        local_only_cards, official_only_cards = self._find_unique_cards(
            official_result.card_names, local_result.card_names,
        )

        # Determine if there's a major discrepancy
        major_discrepancy = self._determine_major_discrepancy(
            official_result, local_result, result_count_diff, position_correlation,
        )

        # Generate notes
        notes = self._generate_notes(
            official_result, local_result, result_count_diff, position_correlation,
        )

        return ComparisonResult(
            query=query,
            official_result=official_result,
            local_result=local_result,
            result_count_diff=result_count_diff,
            position_correlation=position_correlation,
            major_discrepancy=major_discrepancy,
            notes=notes,
            local_only_cards=local_only_cards,
            official_only_cards=official_only_cards,
            local_total_cards=local_result.total_cards,
        )

    def run_comparison_suite(self) -> list[ComparisonResult]:
        """Run a comprehensive suite of test queries."""
        test_queries = [
            # Basic searches
            "lightning",
            "llanowar",
            "t:beast",
            "c:g",
            "cmc=3",
            "power>3",

            # Color searches
            "id:g",
            "c:rg",
            "color:white",

            # Complex searches
            "t:beast id:g",
            "cmc<=3 power>=2",
            "o:flying t:angel",

            # Keyword searches
            "keyword:flying",
            "keyword:trample",
            "keyword:vigilance",

            "otag:dual-land",

            # Arithmetic expressions
            "cmc+1<power",
            "power>toughness",

            # Edge cases
            'name:"Lightning Bolt"',
            "cmc=0",
            "power<0",
        ]

        results = []
        for query in test_queries:
            logger.info(f"Comparing query: {query}")
            try:
                result = self.compare_results(query)
                results.append(result)
                time.sleep(0.2)  # Rate limiting
            except (requests.RequestException, ValueError) as e:
                logger.error(f"Failed to compare query '{query}': {e}")
            logger.info("")

        return results

    def generate_report(self, results: list[ComparisonResult]) -> str:
        """Generate a summary report of the comparison results."""
        total_queries = len(results)
        successful_official = sum(1 for r in results if r.official_result.success)
        successful_local = sum(1 for r in results if r.local_result.success)
        major_discrepancies = sum(1 for r in results if r.major_discrepancy)

        report = f"""
# Scryfall API Comparison Report

## Summary
- Total queries tested: {total_queries}
- Official API success rate: {successful_official}/{total_queries} ({successful_official/total_queries*100:.1f}%)
- Local API success rate: {successful_local}/{total_queries} ({successful_local/total_queries*100:.1f}%)
- Major discrepancies: {major_discrepancies}/{total_queries} ({major_discrepancies/total_queries*100:.1f}%)

## Detailed Results
"""

        for result in results:
            report += f"\n### Query: `{result.query}`\n"
            report += f"- Official results: {result.official_result.total_cards} cards\n"
            report += f"- Local results: {result.local_result.total_cards} cards\n"
            report += f"- Local total_cards: {result.local_total_cards} cards\n"
            report += f"- Position correlation: {result.position_correlation:.2f}\n"
            report += f"- Major discrepancy: {'Yes' if result.major_discrepancy else 'No'}\n"

            if result.notes:
                report += "- Notes:\n"
                for note in result.notes:
                    report += f"  - {note}\n"

            # Add unique cards information with positions
            if result.local_only_cards:
                report += "- Cards only in local results (first 5):\n"
                for card_name, position in result.local_only_cards:
                    report += f"  - {card_name} / {position}\n"
            if result.official_only_cards:
                report += "- Cards only in official results (first 5):\n"
                for card_name, position in result.official_only_cards:
                    report += f"  - {card_name} / {position}\n"

        return report

def main() -> None:
    """Main function to run the comparison."""
    logger.info("Starting Scryfall API comparison")

    comparator = ScryfallAPIComparator()
    results = comparator.run_comparison_suite()

    # Generate and save report
    report = comparator.generate_report(results)

    # Save report to temporary file (secure)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix="_scryfall_comparison_report.md",
            delete=False,
            prefix="scryfall_",
        ) as temp_file:
            temp_file.write(report)
            temp_file.flush()
            logger.info(f"Report saved to: {temp_file.name}")
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not save report to file: {e}")

    logger.info("Report content available in logs above")

    # Print summary
    major_discrepancies = [r for r in results if r.major_discrepancy]
    if major_discrepancies:
        logger.info(f"⚠️  Found {len(major_discrepancies)} queries with major discrepancies:")
        for result in major_discrepancies:
            logger.info(f"  - {result.query}: {', '.join(result.notes)}")
    else:
        logger.info("✅ No major discrepancies found!")

if __name__ == "__main__":
    main()
