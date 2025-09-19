#!/usr/bin/env python3
"""Tests for the compare_tag_counts script."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the parent directory to the path so we can import from scripts
sys.path.insert(0, str(Path(__file__).parent))

from compare_tag_counts import TagComparator


class TestTagComparator(unittest.TestCase):
    """Test cases for TagComparator functionality."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.comparator = TagComparator()

    @patch("compare_tag_counts.APIResource")
    def test_get_all_tags(self, mock_api_resource_class: MagicMock) -> None:
        """Test fetching all tags from Scryfall."""
        # Mock the APIResource instance and its method
        mock_api_resource = MagicMock()
        mock_api_resource_class.return_value = mock_api_resource
        mock_api_resource.discover_tags_from_scryfall.return_value = ["flying", "trample", "haste"]

        # Create a new comparator to use the mocked APIResource
        comparator = TagComparator()
        tags = comparator.get_all_tags()

        assert tags == ["flying", "trample", "haste"]
        mock_api_resource.discover_tags_from_scryfall.assert_called_once()

    @patch("compare_tag_counts.APIResource")
    def test_get_scryfall_card_count(self, mock_api_resource_class: MagicMock) -> None:
        """Test getting card count from Scryfall API."""
        # Mock the APIResource instance and its method
        mock_api_resource = MagicMock()
        mock_api_resource_class.return_value = mock_api_resource
        mock_api_resource._fetch_cards_from_scryfall.return_value = ["Card 1", "Card 2", "Card 3"]

        comparator = TagComparator()
        count = comparator.get_scryfall_card_count("flying")

        assert count == 3
        mock_api_resource._fetch_cards_from_scryfall.assert_called_once_with(tag="flying")

    @patch("compare_tag_counts.APIResource")
    def test_get_scryfall_card_count_error_handling(self, mock_api_resource_class: MagicMock) -> None:
        """Test error handling when fetching card count from Scryfall."""
        # Mock the APIResource instance to raise an exception
        mock_api_resource = MagicMock()
        mock_api_resource_class.return_value = mock_api_resource
        mock_api_resource._fetch_cards_from_scryfall.side_effect = Exception("API Error")

        comparator = TagComparator()
        count = comparator.get_scryfall_card_count("invalid_tag")

        assert count == 0  # Should return 0 on error

    def test_compare_tag_counts_sorting(self) -> None:
        """Test that tag comparisons are sorted by missing count descending."""
        comparator = TagComparator()

        # Mock the methods to return predictable values
        with patch.object(comparator, "get_scryfall_card_count") as mock_scryfall, \
             patch.object(comparator, "get_local_card_count") as mock_local:

            # Set up mock return values
            def scryfall_side_effect(tag: str) -> int:
                counts = {"tag1": 10, "tag2": 20, "tag3": 15}
                return counts.get(tag, 0)

            def local_side_effect(tag: str) -> int:
                counts = {"tag1": 8, "tag2": 20, "tag3": 10}  # tag2 has no missing, tag3 has 5 missing, tag1 has 2 missing
                return counts.get(tag, 0)

            mock_scryfall.side_effect = scryfall_side_effect
            mock_local.side_effect = local_side_effect

            comparisons = comparator.compare_tag_counts(["tag1", "tag2", "tag3"])

            # Should be sorted by missing count descending: tag3 (5), tag1 (2), tag2 (0)
            assert len(comparisons) == 3
            assert comparisons[0]["tag"] == "tag3"
            assert comparisons[0]["missing_count"] == 5
            assert comparisons[1]["tag"] == "tag1"
            assert comparisons[1]["missing_count"] == 2
            assert comparisons[2]["tag"] == "tag2"
            assert comparisons[2]["missing_count"] == 0

    def test_compare_tag_counts_coverage_calculation(self) -> None:
        """Test that coverage percentage is calculated correctly."""
        comparator = TagComparator()

        with patch.object(comparator, "get_scryfall_card_count", return_value=100), \
             patch.object(comparator, "get_local_card_count", return_value=75):

            comparisons = comparator.compare_tag_counts(["test_tag"])

            assert len(comparisons) == 1
            assert comparisons[0]["coverage_percent"] == 75.0
            assert comparisons[0]["missing_count"] == 25

    def test_compare_tag_counts_zero_scryfall_count(self) -> None:
        """Test coverage calculation when Scryfall count is zero."""
        comparator = TagComparator()

        with patch.object(comparator, "get_scryfall_card_count", return_value=0), \
             patch.object(comparator, "get_local_card_count", return_value=0):

            comparisons = comparator.compare_tag_counts(["test_tag"])

            assert len(comparisons) == 1
            assert comparisons[0]["coverage_percent"] == 100.0
            assert comparisons[0]["missing_count"] == 0

    @patch("requests.Session")
    def test_import_tags_for_cards_success(self, mock_session_class: MagicMock) -> None:
        """Test successful tag import."""
        # Mock the session and response
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_response = MagicMock()
        mock_response.json.return_value = {"tag": "flying", "cards_updated": 42}
        mock_session.get.return_value = mock_response

        comparator = TagComparator()
        result = comparator.import_tags_for_cards("flying")

        assert result["tag"] == "flying"
        assert result["cards_updated"] == 42
        mock_session.get.assert_called_once()

    @patch("requests.Session")
    def test_import_tags_for_cards_error(self, mock_session_class: MagicMock) -> None:
        """Test error handling during tag import."""
        # Mock the session to raise an exception
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = Exception("Network error")

        comparator = TagComparator()
        result = comparator.import_tags_for_cards("flying")

        assert "error" in result
        assert result["tag"] == "flying"

    def test_refresh_top_n_tags_zero_n(self) -> None:
        """Test that zero or negative N returns empty results."""
        comparator = TagComparator()
        comparisons = [{"tag": "test", "missing_count": 5}]

        results = comparator.refresh_top_n_tags(comparisons, 0)
        assert len(results) == 0

        results = comparator.refresh_top_n_tags(comparisons, -1)
        assert len(results) == 0

    def test_refresh_top_n_tags_skips_zero_missing(self) -> None:
        """Test that tags with zero missing cards are skipped."""
        comparator = TagComparator()
        comparisons = [
            {"tag": "tag1", "missing_count": 0},
            {"tag": "tag2", "missing_count": 5},
        ]

        with patch.object(comparator, "import_tags_for_cards") as mock_import:
            results = comparator.refresh_top_n_tags(comparisons, 2)

            # Should only import tag2, not tag1 (which has 0 missing)
            assert len(results) == 1
            mock_import.assert_called_once_with("tag2")


if __name__ == "__main__":
    unittest.main()
