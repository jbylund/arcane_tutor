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


class TestJSONExportImport(unittest.TestCase):
    """Test cases for JSON export/import functionality."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource()
        # Mock the database connection pool to avoid real DB calls
        self.api_resource._conn_pool = MagicMock()

    def test_json_export_methods_exist(self) -> None:
        """Test that JSON export methods exist and are callable."""
        methods = ["export_cards", "export_tags", "export_tag_hierarchy", "export_all"]
        for method in methods:
            assert hasattr(self.api_resource, method)
            assert callable(getattr(self.api_resource, method))

    def test_json_import_methods_exist(self) -> None:
        """Test that JSON import methods exist and are callable."""
        methods = ["import_cards", "import_tags", "import_tag_hierarchy", "import_all"]
        for method in methods:
            assert hasattr(self.api_resource, method)
            assert callable(getattr(self.api_resource, method))

    def test_new_json_methods_in_action_map(self) -> None:
        """Test that new JSON methods are available via action_map for routing."""
        action_map = self.api_resource.action_map

        # Test that all new JSON methods are in the action map
        json_methods = [
            "export_cards", "export_tags", "export_tag_hierarchy", "export_all",
            "import_cards", "import_tags", "import_tag_hierarchy", "import_all"
        ]
        for method in json_methods:
            assert method in action_map

    @patch.object(APIResource, "get_cards")
    def test_export_cards(self, mock_get_cards: Mock) -> None:
        """Test export_cards returns data from get_cards."""
        test_data = [{"card_name": "Lightning Bolt", "cmc": 1}]
        mock_get_cards.return_value = test_data

        result = self.api_resource.export_cards()
        assert result == test_data
        mock_get_cards.assert_called_once()

    @patch.object(APIResource, "get_tags")
    def test_export_tags(self, mock_get_tags: Mock) -> None:
        """Test export_tags returns data from get_tags."""
        test_data = [{"tag": "creature"}, {"tag": "instant"}]
        mock_get_tags.return_value = test_data

        result = self.api_resource.export_tags()
        assert result == test_data
        mock_get_tags.assert_called_once()

    @patch.object(APIResource, "get_tag_relationships")
    def test_export_tag_hierarchy(self, mock_get_relationships: Mock) -> None:
        """Test export_tag_hierarchy returns data from get_tag_relationships."""
        test_data = [{"child_tag": "creature", "parent_tag": "permanent"}]
        mock_get_relationships.return_value = test_data

        result = self.api_resource.export_tag_hierarchy()
        assert result == test_data
        mock_get_relationships.assert_called_once()

    @patch.object(APIResource, "export_cards")
    @patch.object(APIResource, "export_tags")
    @patch.object(APIResource, "export_tag_hierarchy")
    def test_export_all(self, mock_hierarchy: Mock, mock_tags: Mock, mock_cards: Mock) -> None:
        """Test export_all combines all exports."""
        mock_cards.return_value = [{"card_name": "Lightning Bolt"}]
        mock_tags.return_value = [{"tag": "instant"}]
        mock_hierarchy.return_value = [{"child_tag": "instant", "parent_tag": "spell"}]

        result = self.api_resource.export_all()

        expected = {
            "cards": [{"card_name": "Lightning Bolt"}],
            "tags": [{"tag": "instant"}],
            "tag_hierarchy": [{"child_tag": "instant", "parent_tag": "spell"}]
        }
        assert result == expected

    def test_import_cards_empty_data(self) -> None:
        """Test import_cards handles empty data."""
        result = self.api_resource.import_cards(cards_data=[])
        assert result["imported"] == 0
        assert "No cards data provided" in result["message"]

    def test_import_tags_empty_data(self) -> None:
        """Test import_tags handles empty data."""
        result = self.api_resource.import_tags(tags_data=[])
        assert result["imported"] == 0
        assert "No tags data provided" in result["message"]

    def test_import_tag_hierarchy_empty_data(self) -> None:
        """Test import_tag_hierarchy handles empty data."""
        result = self.api_resource.import_tag_hierarchy(tag_hierarchy_data=[])
        assert result["imported"] == 0
        assert "No tag hierarchy data provided" in result["message"]

    def test_import_all_empty_data(self) -> None:
        """Test import_all handles empty data."""
        result = self.api_resource.import_all()
        
        assert "cards" in result
        assert "tags" in result
        assert "tag_hierarchy" in result
        
        for key in ["cards", "tags", "tag_hierarchy"]:
            assert result[key]["imported"] == 0
            assert "No" in result[key]["message"]
