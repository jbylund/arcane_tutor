"""Tests for the fetcher module."""

import unittest
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from gatherer_import.fetcher import GathererFetcher


class TestGathererFetcher(unittest.TestCase):
    """Test cases for the GathererFetcher class."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.fetcher = GathererFetcher()

    def test_init(self) -> None:
        """Test that the fetcher initializes correctly."""
        assert self.fetcher.base_url == "https://api.scryfall.com"
        assert self.fetcher.session is not None
        assert "User-Agent" in self.fetcher.session.headers

    def test_custom_base_url(self) -> None:
        """Test that a custom base URL can be provided."""
        custom_url = "https://custom.api.com"
        fetcher = GathererFetcher(base_url=custom_url)
        assert fetcher.base_url == custom_url

    @patch("gatherer_import.fetcher.time.sleep")
    def test_rate_limit(self, mock_sleep: MagicMock) -> None:
        """Test that rate limiting is applied."""
        # First request should not sleep
        self.fetcher._rate_limit()
        mock_sleep.assert_not_called()

        # Second immediate request should sleep
        self.fetcher._rate_limit()
        mock_sleep.assert_called_once()

    @patch("gatherer_import.fetcher.requests.Session.get")
    def test_make_request_success(self, mock_get: MagicMock) -> None:
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"test": "data"}'
        mock_get.return_value = mock_response

        result = self.fetcher._make_request("https://example.com")

        assert result == {"test": "data"}
        mock_get.assert_called_once()

    @patch("gatherer_import.fetcher.requests.Session.get")
    def test_make_request_failure(self, mock_get: MagicMock) -> None:
        """Test failed API request."""
        mock_get.side_effect = requests.RequestException("Network error")

        with pytest.raises(requests.RequestException):
            self.fetcher._make_request("https://example.com")

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_set_info_success(self, mock_request: MagicMock) -> None:
        """Test fetching set information."""
        mock_request.return_value = {
            "code": "DOM",
            "name": "Dominaria",
            "card_count": 280,
        }

        result = self.fetcher.fetch_set_info("DOM")

        assert result["code"] == "DOM"
        assert result["name"] == "Dominaria"
        assert result["card_count"] == 280

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_set_info_not_found(self, mock_request: MagicMock) -> None:
        """Test fetching set information for non-existent set."""
        mock_error = requests.RequestException()
        mock_error.response = Mock()
        mock_error.response.status_code = 404
        mock_request.side_effect = mock_error

        with pytest.raises(ValueError, match=r"Set code.*not found"):
            self.fetcher.fetch_set_info("INVALID")

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_set_single_page(self, mock_request: MagicMock) -> None:
        """Test fetching cards from a set with single page."""
        mock_request.return_value = {
            "data": [
                {"name": "Card 1", "set": "DOM"},
                {"name": "Card 2", "set": "DOM"},
            ],
            "has_more": False,
        }

        result = self.fetcher.fetch_set("DOM")

        assert len(result) == 2
        assert result[0]["name"] == "Card 1"
        assert result[1]["name"] == "Card 2"

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_set_multiple_pages(self, mock_request: MagicMock) -> None:
        """Test fetching cards from a set with multiple pages."""
        # First page
        mock_request.side_effect = [
            {
                "data": [{"name": "Card 1"}],
                "has_more": True,
                "next_page": "https://api.scryfall.com/cards/search?page=2",
            },
            # Second page
            {
                "data": [{"name": "Card 2"}],
                "has_more": False,
            },
        ]

        result = self.fetcher.fetch_set("DOM")

        assert len(result) == 2
        assert mock_request.call_count == 2

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_set_not_found(self, mock_request: MagicMock) -> None:
        """Test fetching cards from non-existent set."""
        mock_error = requests.RequestException()
        mock_error.response = Mock()
        mock_error.response.status_code = 404
        mock_request.side_effect = mock_error

        result = self.fetcher.fetch_set("INVALID")

        assert result == []

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_all_sets(self, mock_request: MagicMock) -> None:
        """Test fetching all sets."""
        mock_request.return_value = {
            "data": [
                {"code": "DOM", "name": "Dominaria"},
                {"code": "WAR", "name": "War of the Spark"},
            ],
            "has_more": False,
        }

        result = self.fetcher.fetch_all_sets()

        assert len(result) == 2
        assert result[0]["code"] == "DOM"
        assert result[1]["code"] == "WAR"

    @patch.object(GathererFetcher, "_make_request")
    def test_fetch_set_with_extras(self, mock_request: MagicMock) -> None:
        """Test fetching cards with extras included."""
        mock_request.return_value = {
            "data": [
                {"name": "Card 1", "set": "DOM"},
                {"name": "Token 1", "set": "DOM", "layout": "token"},
            ],
            "has_more": False,
        }

        result = self.fetcher.fetch_set("DOM", include_extras=True)

        assert len(result) == 2


if __name__ == "__main__":
    unittest.main()
