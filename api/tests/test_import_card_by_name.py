"""Tests for the import_card_by_name functionality."""

# ruff: noqa: PT011

import unittest
from unittest.mock import MagicMock, patch

import pytest
import requests

from api.api_resource import APIResource


class TestImportCardByName(unittest.TestCase):
    """Test cases for import_card_by_name functionality."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_import_card_by_name_validates_input(self) -> None:
        """Test that import_card_by_name validates card_name parameter."""
        with pytest.raises(ValueError) as context:
            self.api_resource.import_card_by_name(card_name="")

        assert str(context.value) == "card_name parameter is required"

        with pytest.raises(ValueError) as context:
            self.api_resource.import_card_by_name(card_name=None)

        assert str(context.value) == "card_name parameter is required"

    def test_import_card_by_name_function_exists(self) -> None:
        """Test that import_card_by_name method exists and is callable."""
        assert hasattr(self.api_resource, "import_card_by_name")
        assert callable(self.api_resource.import_card_by_name)

    def test_scryfall_search_function_exists(self) -> None:
        """Test that _scryfall_search method exists."""
        assert hasattr(self.api_resource, "_scryfall_search")
        assert callable(self.api_resource._scryfall_search)

    def test_insert_cards_to_database_function_exists(self) -> None:
        """Test that _insert_cards_to_database method exists."""
        assert hasattr(self.api_resource, "_insert_cards_to_database")
        assert callable(self.api_resource._insert_cards_to_database)

    @patch("requests.Session.get")
    def test_scryfall_search_returns_empty_for_404(self, mock_get: MagicMock) -> None:
        """Test that _scryfall_search returns empty list for 404 responses."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        result = self.api_resource._scryfall_search(query="name:'NonexistentCard'")

        assert result == []

    @patch("requests.Session.get")
    def test_scryfall_search_returns_data_for_success(self, mock_get: MagicMock) -> None:
        """Test that _scryfall_search returns card data for successful responses."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"name": "Lightning Bolt", "cmc": 1}],
            "has_more": False,
        }
        mock_get.return_value = mock_response

        result = self.api_resource._scryfall_search(query="name:'Lightning Bolt'")

        assert result == [{"name": "Lightning Bolt", "cmc": 1}]
        mock_get.assert_called_once_with(
            "https://api.scryfall.com/cards/search",
            params={"q": "name:'Lightning Bolt' game:paper (f:m or f:l or f:c or f:v)", "format": "json"},
            timeout=30,
        )

    @patch("requests.Session.get")
    def test_scryfall_search_raises_for_request_errors(self, mock_get: MagicMock) -> None:
        """Test that _scryfall_search raises exception for request errors."""
        mock_get.side_effect = requests.RequestException("Network error")

        with pytest.raises(ValueError, match="Failed to fetch data from Scryfall API"):
            self.api_resource._scryfall_search(query="name:'Lightning Bolt'")

    def test_import_card_by_name_returns_already_exists_for_existing_card(self) -> None:
        """Test that import_card_by_name returns already_exists status for existing cards."""
        # Mock database connection to return existing card
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"card_name": "Lightning Bolt"}
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        self.mock_conn_pool.connection.return_value.__enter__.return_value = mock_conn

        result = self.api_resource.import_card_by_name(card_name="Lightning Bolt")

        assert result["status"] == "already_exists"
        assert result["card_name"] == "Lightning Bolt"
        assert "already exists in database" in result["message"]

    @patch.object(APIResource, "_scryfall_search")
    def test_import_card_by_name_returns_not_found_for_missing_card(self, mock_search: MagicMock) -> None:
        """Test that import_card_by_name returns not_found status when card doesn't exist in Scryfall."""
        # Mock database connection to return no existing card
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        self.mock_conn_pool.connection.return_value.__enter__.return_value = mock_conn

        # Mock Scryfall API to return empty list (not found)
        mock_search.return_value = []

        result = self.api_resource.import_card_by_name(card_name="NonexistentCard")

        assert result["status"] == "not_found"
        assert result["card_name"] == "NonexistentCard"
        assert "not found in Scryfall API" in result["message"]

    @patch.object(APIResource, "_scryfall_search")
    def test_import_card_by_name_returns_error_for_scryfall_exceptions(self, mock_search: MagicMock) -> None:
        """Test that import_card_by_name returns error status for Scryfall API exceptions."""
        # Mock database connection to return no existing card
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        self.mock_conn_pool.connection.return_value.__enter__.return_value = mock_conn

        # Mock Scryfall API to raise exception
        mock_search.side_effect = ValueError("API Error")

        result = self.api_resource.import_card_by_name(card_name="TestCard")

        assert result["status"] == "error"
        assert result["card_name"] == "TestCard"
        assert "Error fetching card from Scryfall" in result["message"]

    @patch.object(APIResource, "_scryfall_search")
    @patch.object(APIResource, "_preprocess_card")
    def test_import_card_by_name_returns_filtered_out_for_invalid_cards(
        self, mock_preprocess: MagicMock, mock_search: MagicMock,
    ) -> None:
        """Test that import_card_by_name returns filtered_out status for cards filtered during preprocessing."""
        # Mock database connection to return no existing card
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        self.mock_conn_pool.connection.return_value.__enter__.return_value = mock_conn

        # Mock Scryfall API to return card data
        mock_search.return_value = [{"name": "TestCard", "legalities": {"standard": "not_legal"}}]

        # Mock preprocessing to return None (filtered out)
        mock_preprocess.return_value = None

        result = self.api_resource.import_card_by_name(card_name="TestCard")

        assert result["status"] == "filtered_out"
        assert result["card_name"] == "TestCard"
        assert "was filtered out during preprocessing" in result["message"]


if __name__ == "__main__":
    unittest.main()
