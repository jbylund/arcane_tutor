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
import time
from dataclasses import dataclass

import requests

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

class ScryfallAPIComparator:
    """Compares search results between official Scryfall and local implementation."""

    def __init__(self) -> None:
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
                "q": query,
                "order": "edhrec",
                "dir": "asc",
                "page": 1,
            }

            logger.info(f"Searching official Scryfall: {query}")
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 404:
                # No results found
                return SearchResult(query=query, total_cards=0, card_names=[], success=True)
            if response.status_code != 200:
                return SearchResult(
                    query=query,
                    total_cards=0,
                    card_names=[],
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text}",
                )

            data = response.json()
            total_cards = data.get("total_cards", 0)
            card_names = [card.get("name", "") for card in data.get("data", [])]

            return SearchResult(
                query=query,
                total_cards=total_cards,
                card_names=card_names[:limit],
                success=True,
            )

        except Exception as e:
            logger.error(f"Error searching official Scryfall for '{query}': {e}")
            return SearchResult(
                query=query,
                total_cards=0,
                card_names=[],
                success=False,
                error_message=str(e),
            )

    def search_local_scryfall(self, query: str, limit: int = 100) -> SearchResult:
        """Search using local Scryfall OS implementation."""
        try:
            url = f"{self.local_base_url}/search"
            params = {
                "q": query,
                "orderby": "edhrec",
                "direction": "asc",
                "limit": limit,
            }

            logger.info(f"Searching local Scryfall: {query}")
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 502:
                # Server is down, mark as failed
                return SearchResult(
                    query=query,
                    total_cards=0,
                    card_names=[],
                    success=False,
                    error_message="Local API server unavailable (HTTP 502)",
                )
            if response.status_code != 200:
                return SearchResult(
                    query=query,
                    total_cards=0,
                    card_names=[],
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                )

            data = response.json()
            cards = data.get("data", [])
            total_cards = data.get("total_cards", len(cards))
            card_names = [card.get("name", "") for card in cards]

            return SearchResult(
                query=query,
                total_cards=total_cards,
                card_names=card_names,
                success=True,
            )

        except Exception as e:
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

    def compare_results(self, query: str) -> ComparisonResult:
        """Compare search results between official and local APIs."""
        official_result = self.search_official_scryfall(query)
        time.sleep(0.1)  # Rate limiting
        local_result = self.search_local_scryfall(query)

        # Calculate metrics
        result_count_diff = abs(official_result.total_cards - local_result.total_cards)
        position_correlation = self.calculate_position_correlation(
            official_result.card_names,
            local_result.card_names,
        )

        # Determine if there's a major discrepancy
        major_discrepancy = (
            not official_result.success or
            not local_result.success or
            result_count_diff > max(official_result.total_cards, local_result.total_cards) * 0.5 or
            position_correlation < 0.3
        )

        # Generate notes
        notes = []
        if not official_result.success:
            notes.append(f"Official API failed: {official_result.error_message}")
        if not local_result.success:
            notes.append(f"Local API failed: {local_result.error_message}")
        if result_count_diff > 0:
            notes.append(f"Result count difference: {result_count_diff}")
        if position_correlation < 0.5:
            notes.append(f"Low position correlation: {position_correlation:.2f}")

        return ComparisonResult(
            query=query,
            official_result=official_result,
            local_result=local_result,
            result_count_diff=result_count_diff,
            position_correlation=position_correlation,
            major_discrepancy=major_discrepancy,
            notes=notes,
        )

    def run_comparison_suite(self) -> list[ComparisonResult]:
        """Run a comprehensive suite of test queries."""
        test_queries = [
            # Basic searches
            "lightning",
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
            "k:flying",
            "k:trample",
            "keywords:vigilance",

            # Oracle tags (local extension)
            "ot:haste",
            "oracle_tags:dual-land",

            # Arithmetic expressions
            "cmc+1<power",
            "power>toughness",

            # Edge cases
            'name:"Lightning Bolt"',
            "cmc=0",
            "power<0",

            # Potential failures
            "invalid:syntax",
            "nonexistent:field",
        ]

        results = []
        for query in test_queries:
            logger.info(f"Comparing query: {query}")
            try:
                result = self.compare_results(query)
                results.append(result)
                time.sleep(0.2)  # Rate limiting
            except Exception as e:
                logger.error(f"Failed to compare query '{query}': {e}")

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
            report += f"- Position correlation: {result.position_correlation:.2f}\n"
            report += f"- Major discrepancy: {'Yes' if result.major_discrepancy else 'No'}\n"

            if result.notes:
                report += "- Notes:\n"
                for note in result.notes:
                    report += f"  - {note}\n"

        return report

def main() -> None:
    """Main function to run the comparison."""
    logger.info("Starting Scryfall API comparison")

    comparator = ScryfallAPIComparator()
    results = comparator.run_comparison_suite()

    # Generate and save report
    report = comparator.generate_report(results)

    with open("/tmp/scryfall_comparison_report.md", "w") as f:
        f.write(report)


    # Print summary
    major_discrepancies = [r for r in results if r.major_discrepancy]
    if major_discrepancies:
        for _result in major_discrepancies:
            pass
    else:
        pass

if __name__ == "__main__":
    main()
