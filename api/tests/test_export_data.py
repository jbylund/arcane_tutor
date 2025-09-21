"""Tests for data export functionality."""

import unittest
from unittest.mock import MagicMock, Mock, patch

import pytest

from api.api_resource import APIResource


class TestExportData(unittest.TestCase):
    """Test cases for export data functionality."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource()
        # Mock the database connection pool to avoid real DB calls
        self.api_resource._conn_pool = MagicMock()

    def test_get_tags_method_exists(self) -> None:
        """Test that get_tags method exists and is callable."""
        assert hasattr(self.api_resource, "get_tags")
        assert callable(self.api_resource.get_tags)

    def test_get_tags_to_csv_method_exists(self) -> None:
        """Test that get_tags_to_csv method exists and is callable."""
        assert hasattr(self.api_resource, "get_tags_to_csv")
        assert callable(self.api_resource.get_tags_to_csv)

    def test_get_tag_relationships_method_exists(self) -> None:
        """Test that get_tag_relationships method exists and is callable."""
        assert hasattr(self.api_resource, "get_tag_relationships")
        assert callable(self.api_resource.get_tag_relationships)

    def test_get_tag_relationships_to_csv_method_exists(self) -> None:
        """Test that get_tag_relationships_to_csv method exists and is callable."""
        assert hasattr(self.api_resource, "get_tag_relationships_to_csv")
        assert callable(self.api_resource.get_tag_relationships_to_csv)

    def test_get_tags_to_csv_requires_falcon_response(self) -> None:
        """Test that get_tags_to_csv requires falcon_response parameter."""
        with pytest.raises(ValueError, match="falcon_response is required"):
            self.api_resource.get_tags_to_csv()

    def test_get_tag_relationships_to_csv_requires_falcon_response(self) -> None:
        """Test that get_tag_relationships_to_csv requires falcon_response parameter."""
        with pytest.raises(ValueError, match="falcon_response is required"):
            self.api_resource.get_tag_relationships_to_csv()

    def test_new_methods_in_action_map(self) -> None:
        """Test that new methods are available via action_map for routing."""
        action_map = self.api_resource.action_map

        # Test that all export methods are in the action map
        assert "get_tags" in action_map
        assert "get_tags_to_csv" in action_map
        assert "get_tag_relationships" in action_map
        assert "get_tag_relationships_to_csv" in action_map

    @patch.object(APIResource, "get_tags")
    def test_get_tags_to_csv_handles_empty_data(self, mock_get_tags: Mock) -> None:
        """Test that get_tags_to_csv handles empty data correctly."""
        # Mock empty data
        mock_get_tags.return_value = []

        # Mock Falcon response
        mock_response = MagicMock()

        # Call the method
        self.api_resource.get_tags_to_csv(falcon_response=mock_response)

        # Verify response is set correctly for empty data
        assert mock_response.content_type == "text/csv"
        assert mock_response.body == b""

    @patch.object(APIResource, "get_tag_relationships")
    def test_get_tag_relationships_to_csv_handles_empty_data(self, mock_get_relationships: Mock) -> None:
        """Test that get_tag_relationships_to_csv handles empty data correctly."""
        # Mock empty data
        mock_get_relationships.return_value = []

        # Mock Falcon response
        mock_response = MagicMock()

        # Call the method
        self.api_resource.get_tag_relationships_to_csv(falcon_response=mock_response)

        # Verify response is set correctly for empty data
        assert mock_response.content_type == "text/csv"
        assert mock_response.body == b""

    @patch.object(APIResource, "get_tags")
    def test_get_tags_to_csv_with_data(self, mock_get_tags: Mock) -> None:
        """Test that get_tags_to_csv generates CSV correctly with data."""
        # Mock data
        mock_get_tags.return_value = [
            {"tag": "creature"},
            {"tag": "instant"},
        ]

        # Mock Falcon response
        mock_response = MagicMock()

        # Call the method
        self.api_resource.get_tags_to_csv(falcon_response=mock_response)

        # Verify response is set correctly
        assert mock_response.content_type == "text/csv"
        # The body should contain CSV data (converted to bytes)
        assert isinstance(mock_response.body, bytes)
        # Convert back to string to check content
        csv_content = mock_response.body.decode("utf-8")
        assert "tag" in csv_content  # Header
        assert "creature" in csv_content
        assert "instant" in csv_content
