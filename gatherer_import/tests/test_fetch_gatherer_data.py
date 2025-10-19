"""Tests for the fetch_gatherer_data module."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from gatherer_import.fetch_gatherer_data import GathererFetcher


class TestExtractItemsFromResponse:
    """Tests for the _extract_items_from_response method."""

    def test_extract_items_from_valid_response(self) -> None:
        """Test extracting items from a valid Gatherer response."""
        fetcher = GathererFetcher()

        # Create a mock response with embedded JSON array
        mock_items = [{"id": 1, "name": "Item 1"}, {"id": 2, "name": "Item 2"}]
        # Double-encode the items as they would be in the actual response
        items_json = json.dumps(mock_items).replace('"', r'\"')
        mock_html = f'some html before,\\"items\\":{items_json} some html after'

        result = fetcher._extract_items_from_response(mock_html)

        assert result == mock_items
        assert len(result) == 2
        assert result[0]["name"] == "Item 1"

    def test_extract_items_empty_array(self) -> None:
        """Test extracting an empty items array."""
        fetcher = GathererFetcher()

        mock_html = r'some html before,\"items\":[] some html after'

        result = fetcher._extract_items_from_response(mock_html)

        assert result == []

    def test_extract_items_nested_objects(self) -> None:
        """Test extracting items with nested objects."""
        fetcher = GathererFetcher()

        mock_items = [
            {"id": 1, "details": {"name": "Card 1", "type": "Creature"}},
            {"id": 2, "details": {"name": "Card 2", "type": "Instant"}},
        ]
        items_json = json.dumps(mock_items).replace('"', r'\"')
        mock_html = f'prefix,\\"items\\":{items_json} suffix'

        result = fetcher._extract_items_from_response(mock_html)

        assert result == mock_items
        assert result[0]["details"]["name"] == "Card 1"

    def test_extract_items_no_items_key(self) -> None:
        """Test that ValueError is raised when items key is not found."""
        fetcher = GathererFetcher()

        mock_html = "some html without items key"

        with pytest.raises(ValueError, match="No items array found"):
            fetcher._extract_items_from_response(mock_html)

    def test_extract_items_malformed_array(self) -> None:
        """Test that ValueError is raised when array end cannot be found."""
        fetcher = GathererFetcher()

        # Missing closing bracket
        mock_html = r'prefix,\"items\":[{"id": 1} some html after'

        with pytest.raises(ValueError, match="Could not find end of items array"):
            fetcher._extract_items_from_response(mock_html)

    def test_extract_items_with_arrays_in_items(self) -> None:
        """Test extracting items that contain arrays."""
        fetcher = GathererFetcher()

        mock_items = [
            {"id": 1, "tags": ["tag1", "tag2"]},
            {"id": 2, "tags": ["tag3"]},
        ]
        items_json = json.dumps(mock_items).replace('"', r'\"')
        mock_html = f'prefix,\\"items\\":{items_json} suffix'

        result = fetcher._extract_items_from_response(mock_html)

        assert result == mock_items
        assert result[0]["tags"] == ["tag1", "tag2"]


class TestFetchSet:
    """Tests for the fetch_set method."""

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_set_single_page(self, mock_session_class: Any) -> None:
        """Test fetching a set with cards on a single page."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock response
        mock_cards = [{"id": 1, "name": "Card 1"}, {"id": 2, "name": "Card 2"}]
        items_json = json.dumps(mock_cards).replace('"', r'\"')
        mock_html = f'html,\\"items\\":{items_json} html'

        mock_response = Mock()
        mock_response.text = mock_html
        mock_response.raise_for_status = Mock()

        # First call returns data, second raises 404
        mock_session.get.side_effect = [
            mock_response,
            Mock(raise_for_status=Mock(side_effect=requests.HTTPError(response=Mock(status_code=404)))),
        ]

        fetcher = GathererFetcher()
        result = fetcher.fetch_set("TEST")

        assert len(result) == 2
        assert result[0]["name"] == "Card 1"
        assert result[1]["name"] == "Card 2"

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_set_multiple_pages(self, mock_session_class: Any) -> None:
        """Test fetching a set with cards across multiple pages."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock responses for multiple pages
        page1_cards = [{"id": 1, "name": "Card 1"}]
        page2_cards = [{"id": 2, "name": "Card 2"}]

        page1_json = json.dumps(page1_cards).replace('"', r'\"')
        page2_json = json.dumps(page2_cards).replace('"', r'\"')

        mock_response1 = Mock()
        mock_response1.text = f'html,\\"items\\":{page1_json} html'
        mock_response1.raise_for_status = Mock()

        mock_response2 = Mock()
        mock_response2.text = f'html,\\"items\\":{page2_json} html'
        mock_response2.raise_for_status = Mock()

        # Return two pages of data, then 404
        mock_session.get.side_effect = [
            mock_response1,
            mock_response2,
            Mock(raise_for_status=Mock(side_effect=requests.HTTPError(response=Mock(status_code=404)))),
        ]

        fetcher = GathererFetcher()
        result = fetcher.fetch_set("TEST")

        assert len(result) == 2
        assert result[0]["name"] == "Card 1"
        assert result[1]["name"] == "Card 2"

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_set_http_error_non_404(self, mock_session_class: Any) -> None:
        """Test that non-404 HTTP errors are re-raised."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock a 500 error
        mock_session.get.return_value.raise_for_status = Mock(
            side_effect=requests.HTTPError(response=Mock(status_code=500)),
        )

        fetcher = GathererFetcher()

        with pytest.raises(requests.HTTPError):
            fetcher.fetch_set("TEST")


class TestFetchAllSets:
    """Tests for the fetch_all_sets method."""

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_all_sets_single_page(self, mock_session_class: Any) -> None:
        """Test fetching all sets from a single page."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_sets = [
            {"setCode": "SET1", "name": "Set 1"},
            {"setCode": "SET2", "name": "Set 2"},
        ]
        items_json = json.dumps(mock_sets).replace('"', r'\"')
        mock_html = f'html,\\"items\\":{items_json} html'

        mock_response = Mock()
        mock_response.text = mock_html
        mock_response.raise_for_status = Mock()

        # First call returns data, second raises 404
        mock_session.get.side_effect = [
            mock_response,
            Mock(raise_for_status=Mock(side_effect=requests.HTTPError(response=Mock(status_code=404)))),
        ]

        fetcher = GathererFetcher()
        result = fetcher.fetch_all_sets()

        assert len(result) == 2
        assert result == ["SET1", "SET2"]

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_all_sets_multiple_pages(self, mock_session_class: Any) -> None:
        """Test fetching all sets across multiple pages."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        page1_sets = [{"setCode": "SET1", "name": "Set 1"}]
        page2_sets = [{"setCode": "SET2", "name": "Set 2"}]

        page1_json = json.dumps(page1_sets).replace('"', r'\"')
        page2_json = json.dumps(page2_sets).replace('"', r'\"')

        mock_response1 = Mock()
        mock_response1.text = f'html,\\"items\\":{page1_json} html'
        mock_response1.raise_for_status = Mock()

        mock_response2 = Mock()
        mock_response2.text = f'html,\\"items\\":{page2_json} html'
        mock_response2.raise_for_status = Mock()

        mock_session.get.side_effect = [
            mock_response1,
            mock_response2,
            Mock(raise_for_status=Mock(side_effect=requests.HTTPError(response=Mock(status_code=404)))),
        ]

        fetcher = GathererFetcher()
        result = fetcher.fetch_all_sets()

        assert len(result) == 2
        assert result == ["SET1", "SET2"]

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_all_sets_stops_on_empty_page(self, mock_session_class: Any) -> None:
        """Test that fetching stops when an empty page is encountered."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        page1_sets = [{"setCode": "SET1", "name": "Set 1"}]
        page1_json = json.dumps(page1_sets).replace('"', r'\"')

        mock_response1 = Mock()
        mock_response1.text = f'html,\\"items\\":{page1_json} html'
        mock_response1.raise_for_status = Mock()

        # Second page has empty array
        mock_response2 = Mock()
        mock_response2.text = r'html,\"items\":[] html'
        mock_response2.raise_for_status = Mock()

        mock_session.get.side_effect = [mock_response1, mock_response2]

        fetcher = GathererFetcher()
        result = fetcher.fetch_all_sets()

        assert len(result) == 1
        assert result == ["SET1"]

    @patch("gatherer_import.fetch_gatherer_data.requests.Session")
    def test_fetch_all_sets_stops_on_missing_items(self, mock_session_class: Any) -> None:
        """Test that fetching stops when items key is missing."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        page1_sets = [{"setCode": "SET1", "name": "Set 1"}]
        page1_json = json.dumps(page1_sets).replace('"', r'\"')

        mock_response1 = Mock()
        mock_response1.text = f'html,\\"items\\":{page1_json} html'
        mock_response1.raise_for_status = Mock()

        # Second page has no items key
        mock_response2 = Mock()
        mock_response2.text = "html without items key"
        mock_response2.raise_for_status = Mock()

        mock_session.get.side_effect = [mock_response1, mock_response2]

        fetcher = GathererFetcher()
        result = fetcher.fetch_all_sets()

        assert len(result) == 1
        assert result == ["SET1"]


class TestSaveSetToJson:
    """Tests for the save_set_to_json method."""

    @patch("gatherer_import.fetch_gatherer_data.GathererFetcher.fetch_set")
    def test_save_set_to_json(self, mock_fetch_set: Any, tmp_path: Path) -> None:
        """Test saving a set to a JSON file."""
        mock_cards = [{"id": 1, "name": "Card 1"}, {"id": 2, "name": "Card 2"}]
        mock_fetch_set.return_value = mock_cards

        fetcher = GathererFetcher()
        output_file = fetcher.save_set_to_json("TEST", output_dir=str(tmp_path))

        assert output_file.exists()
        assert output_file.name == "TEST.json"

        # Verify file contents
        with output_file.open() as f:
            saved_data = json.load(f)

        assert saved_data == mock_cards

    @patch("gatherer_import.fetch_gatherer_data.GathererFetcher.fetch_set")
    def test_save_set_creates_directory(self, mock_fetch_set: Any, tmp_path: Path) -> None:
        """Test that save_set_to_json creates the output directory if it doesn't exist."""
        mock_cards = [{"id": 1, "name": "Card 1"}]
        mock_fetch_set.return_value = mock_cards

        output_dir = tmp_path / "nested" / "directories"

        fetcher = GathererFetcher()
        output_file = fetcher.save_set_to_json("TEST", output_dir=str(output_dir))

        assert output_dir.exists()
        assert output_file.exists()
