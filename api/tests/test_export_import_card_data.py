"""Tests for card data export/import functionality."""

import csv
import inspect
import pathlib
import tempfile
from unittest import mock

import pytest

from api.api_resource import APIResource


class TestExportImportCardData:
    """Test suite for export_card_data and import_card_data methods."""

    def test_export_card_data_method_exists(self) -> None:
        """Test that export_card_data method exists and is callable."""
        api_resource = APIResource()
        assert hasattr(api_resource, "export_card_data")
        assert callable(api_resource.export_card_data)

    def test_import_card_data_method_exists(self) -> None:
        """Test that import_card_data method exists and is callable."""
        api_resource = APIResource()
        assert hasattr(api_resource, "import_card_data")
        assert callable(api_resource.import_card_data)

    def test_export_helper_methods_exist(self) -> None:
        """Test that helper methods for export exist."""
        api_resource = APIResource()
        assert hasattr(api_resource, "_export_cards_table")
        assert hasattr(api_resource, "_export_tags_table")
        assert hasattr(api_resource, "_export_tag_relationships_table")

    def test_import_helper_methods_exist(self) -> None:
        """Test that helper methods for import exist."""
        api_resource = APIResource()
        assert hasattr(api_resource, "_find_import_directory")
        assert hasattr(api_resource, "_validate_import_files")
        assert hasattr(api_resource, "_perform_import")

    def test_action_map_includes_export_import_endpoints(self) -> None:
        """Test that the action map includes the new export/import endpoints."""
        api_resource = APIResource()
        assert "export_card_data" in api_resource.action_map
        assert "import_card_data" in api_resource.action_map

    @mock.patch("api.api_resource.pathlib.Path")
    def test_export_card_data_creates_directory(self, mock_path: mock.Mock) -> None:
        """Test that export_card_data creates the proper directory structure."""
        # Setup mocks
        mock_export_dir = mock.Mock()
        mock_path.return_value.__truediv__.return_value = mock_export_dir

        # Create API resource and mock its connection pool
        api_resource = APIResource()
        mock_conn = mock.Mock()
        mock_cursor = mock.Mock()
        api_resource._conn_pool = mock.Mock()

        # Mock the context managers properly
        api_resource._conn_pool.connection.return_value = mock.MagicMock()
        api_resource._conn_pool.connection.return_value.__enter__ = mock.Mock(return_value=mock_conn)
        api_resource._conn_pool.connection.return_value.__exit__ = mock.Mock(return_value=None)

        mock_conn.cursor.return_value = mock.MagicMock()
        mock_conn.cursor.return_value.__enter__ = mock.Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = mock.Mock(return_value=None)

        # Mock helper methods to return empty results
        with mock.patch.object(api_resource, "_export_cards_table", return_value={"file": "cards.csv", "count": 0}), \
             mock.patch.object(api_resource, "_export_tags_table", return_value={"file": "tags.csv", "count": 0}), \
             mock.patch.object(api_resource, "_export_tag_relationships_table", return_value={"file": "relations.csv", "count": 0}):

            result = api_resource.export_card_data()

        # Verify directory creation was called
        mock_export_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        assert result["status"] == "success"

    @mock.patch("api.api_resource.pathlib.Path")
    def test_find_import_directory_with_timestamp(self, mock_path: mock.Mock) -> None:
        """Test _find_import_directory with specific timestamp."""
        api_resource = APIResource()

        # Mock the exports directory and specific timestamp directory
        mock_exports_dir = mock.Mock()
        mock_timestamp_dir = mock.Mock()
        mock_timestamp_dir.exists.return_value = True
        mock_exports_dir.exists.return_value = True
        mock_exports_dir.__truediv__ = mock.Mock(return_value=mock_timestamp_dir)
        mock_path.return_value = mock_exports_dir

        import_dir, timestamp = api_resource._find_import_directory("20241001_120000")

        assert timestamp == "20241001_120000"
        assert import_dir == mock_timestamp_dir

    @mock.patch("api.api_resource.pathlib.Path")
    def test_find_import_directory_no_exports_dir(self, mock_path: mock.Mock) -> None:
        """Test _find_import_directory when exports directory doesn't exist."""
        api_resource = APIResource()

        mock_exports_dir = mock.Mock()
        mock_exports_dir.exists.return_value = False
        mock_path.return_value = mock_exports_dir

        with pytest.raises(ValueError, match="No exports directory found"):
            api_resource._find_import_directory(None)

    @mock.patch("api.api_resource.pathlib.Path")
    def test_find_import_directory_timestamp_not_found(self, mock_path: mock.Mock) -> None:
        """Test _find_import_directory when specific timestamp directory doesn't exist."""
        api_resource = APIResource()

        mock_exports_dir = mock.Mock()
        mock_timestamp_dir = mock.Mock()
        mock_timestamp_dir.exists.return_value = False
        mock_exports_dir.exists.return_value = True
        mock_exports_dir.__truediv__ = mock.Mock(return_value=mock_timestamp_dir)
        mock_path.return_value = mock_exports_dir

        with pytest.raises(ValueError, match=r"Export directory for timestamp.*not found"):
            api_resource._find_import_directory("20241001_120000")

    @mock.patch("api.api_resource.pathlib.Path")
    def test_find_import_directory_latest(self, mock_path: mock.Mock) -> None:
        """Test _find_import_directory finds latest when no timestamp specified."""
        api_resource = APIResource()

        # Mock directory structure
        mock_exports_dir = mock.Mock()
        mock_exports_dir.exists.return_value = True

        # Create mock directories with timestamps as names
        mock_dir1 = mock.Mock()
        mock_dir1.name = "20241001_120000"
        mock_dir1.is_dir.return_value = True

        mock_dir2 = mock.Mock()
        mock_dir2.name = "20241001_130000"  # Later timestamp
        mock_dir2.is_dir.return_value = True

        mock_exports_dir.iterdir.return_value = [mock_dir1, mock_dir2]
        mock_path.return_value = mock_exports_dir

        import_dir, timestamp = api_resource._find_import_directory(None)

        assert timestamp == "20241001_130000"  # Should pick the latest
        assert import_dir == mock_dir2

    def test_validate_import_files_all_present(self) -> None:
        """Test _validate_import_files when all required files are present."""
        api_resource = APIResource()

        with tempfile.TemporaryDirectory() as temp_dir:
            import_dir = pathlib.Path(temp_dir)

            # Create required files
            (import_dir / "cards.csv").touch()
            (import_dir / "tags.csv").touch()
            (import_dir / "tag_relationships.csv").touch()

            # Should not raise any exception
            api_resource._validate_import_files(import_dir)

    def test_validate_import_files_missing_files(self) -> None:
        """Test _validate_import_files when some files are missing."""
        api_resource = APIResource()

        with tempfile.TemporaryDirectory() as temp_dir:
            import_dir = pathlib.Path(temp_dir)

            # Create only one file
            (import_dir / "cards.csv").touch()

            with pytest.raises(ValueError, match=r"Missing required files: tags\.csv, tag_relationships\.csv"):
                api_resource._validate_import_files(import_dir)

    def test_export_cards_table(self) -> None:
        """Test _export_cards_table helper method."""
        api_resource = APIResource()

        # Mock cursor with sample data
        mock_cursor = mock.Mock()
        mock_cursor.fetchmany.side_effect = [
            [("Lightning Bolt", 1, "{R}", '{"R": 1}', '{"name": "Lightning Bolt"}', '["Instant"]',
              "[]", '{"R": true}', '{"R": true}', "{}", "Deal 3 damage", None, None, None, None, None, "{}")],
            [],  # Empty result to end the loop
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = pathlib.Path(temp_dir)
            result = api_resource._export_cards_table(mock_cursor, export_dir)

            assert result["count"] == 1
            assert "cards.csv" in result["file"]

            # Verify CSV file was created with header
            cards_file = export_dir / "cards.csv"
            assert cards_file.exists()

            with cards_file.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                assert "card_name" in header
                assert "cmc" in header

    def test_export_tags_table(self) -> None:
        """Test _export_tags_table helper method."""
        api_resource = APIResource()

        # Mock cursor with sample data
        mock_cursor = mock.Mock()
        mock_cursor.fetchmany.side_effect = [
            [("haste",), ("flying",)],
            [],  # Empty result to end the loop
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = pathlib.Path(temp_dir)
            result = api_resource._export_tags_table(mock_cursor, export_dir)

            assert result["count"] == 2
            assert "tags.csv" in result["file"]

            # Verify CSV file was created
            tags_file = export_dir / "tags.csv"
            assert tags_file.exists()

    def test_export_tag_relationships_table(self) -> None:
        """Test _export_tag_relationships_table helper method."""
        api_resource = APIResource()

        # Mock cursor with sample data
        mock_cursor = mock.Mock()
        mock_cursor.fetchmany.side_effect = [
            [("haste", "keyword"), ("flying", "keyword")],
            [],  # Empty result to end the loop
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = pathlib.Path(temp_dir)
            result = api_resource._export_tag_relationships_table(mock_cursor, export_dir)

            assert result["count"] == 2
            assert "tag_relationships.csv" in result["file"]

            # Verify CSV file was created
            relationships_file = export_dir / "tag_relationships.csv"
            assert relationships_file.exists()

    def test_perform_import(self) -> None:
        """Test _perform_import helper method."""
        api_resource = APIResource()

        # Mock cursor
        mock_cursor = mock.Mock()
        mock_cursor.fetchone.side_effect = [
            {"count": 5},  # tags count
            {"count": 3},  # relationships count
            {"count": 100},  # cards count
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            import_dir = pathlib.Path(temp_dir)

            # Create CSV files with headers
            with (import_dir / "tags.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["tag"])
                writer.writerow(["haste"])

            with (import_dir / "tag_relationships.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["child_tag", "parent_tag"])
                writer.writerow(["haste", "keyword"])

            with (import_dir / "cards.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "card_name", "cmc", "mana_cost_text", "mana_cost_jsonb", "raw_card_blob",
                    "card_types", "card_subtypes", "card_colors", "card_color_identity",
                    "card_keywords", "oracle_text", "edhrec_rank", "creature_power",
                    "creature_power_text", "creature_toughness", "creature_toughness_text",
                    "card_oracle_tags",
                ])

            result = api_resource._perform_import(mock_cursor, import_dir)

            assert result["tags"] == 5
            assert result["tag_relationships"] == 3
            assert result["cards"] == 100

            # Verify truncate operations were called
            assert mock_cursor.execute.call_count >= 6  # 3 truncates + 3 counts

    def test_export_import_integration_structure(self) -> None:
        """Test that export/import methods have compatible interfaces."""
        api_resource = APIResource()

        # Check method signatures are compatible
        export_sig = inspect.signature(api_resource.export_card_data)
        import_sig = inspect.signature(api_resource.import_card_data)

        # Export should accept no parameters (beyond self and **_)
        export_params = [p for name, p in export_sig.parameters.items() if name not in ("self", "_")]
        assert len(export_params) == 0

        # Import should accept optional timestamp parameter
        import_params = {name: p for name, p in import_sig.parameters.items() if name not in ("self", "_")}
        assert "timestamp" in import_params
        assert import_params["timestamp"].default is None
