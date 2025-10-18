"""Tests for the set_converter module."""

import pathlib
import shutil
import tempfile
import unittest

import orjson
import pytest

from gatherer_import.set_converter import SetConverter


class TestSetConverter(unittest.TestCase):
    """Test cases for the SetConverter class."""

    def setUp(self) -> None:
        """Set up test fixtures with temporary directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.converter = SetConverter(output_dir=self.temp_dir)

    def tearDown(self) -> None:
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self) -> None:
        """Test that the converter initializes correctly."""
        assert self.converter.output_dir == pathlib.Path(self.temp_dir)
        assert self.converter.output_dir.exists()

    def test_save_set(self) -> None:
        """Test saving a set to JSON files."""
        cards = [
            {"name": "Lightning Bolt", "set": "DOM", "cmc": 1},
            {"name": "Counterspell", "set": "DOM", "cmc": 2},
        ]

        set_info = {
            "code": "DOM",
            "name": "Dominaria",
            "card_count": 2,
        }

        result = self.converter.save_set("DOM", cards, set_info)

        assert "cards" in result
        assert "metadata" in result

        # Verify files were created
        cards_file = pathlib.Path(result["cards"])
        assert cards_file.exists()

        metadata_file = pathlib.Path(result["metadata"])
        assert metadata_file.exists()

        # Verify content
        with cards_file.open("rb") as f:
            saved_cards = orjson.loads(f.read())
            assert len(saved_cards) == 2
            assert saved_cards[0]["name"] == "Lightning Bolt"

    def test_save_set_without_metadata(self) -> None:
        """Test saving a set without metadata."""
        cards = [{"name": "Card 1"}]

        result = self.converter.save_set("TST", cards)

        assert "cards" in result
        assert "metadata" not in result

        cards_file = pathlib.Path(result["cards"])
        assert cards_file.exists()

    def test_save_multiple_sets(self) -> None:
        """Test saving multiple sets at once."""
        sets_data = {
            "DOM": (
                [{"name": "Card 1"}],
                {"code": "DOM", "name": "Dominaria"},
            ),
            "WAR": (
                [{"name": "Card 2"}],
                {"code": "WAR", "name": "War of the Spark"},
            ),
        }

        results = self.converter.save_multiple_sets(sets_data)

        assert len(results) == 2
        assert "DOM" in results
        assert "WAR" in results
        assert "error" not in results["DOM"]
        assert "error" not in results["WAR"]

    def test_load_set(self) -> None:
        """Test loading a previously saved set."""
        # First save a set
        cards = [
            {"name": "Card 1", "cmc": 1},
            {"name": "Card 2", "cmc": 2},
        ]

        self.converter.save_set("DOM", cards)

        # Then load it
        loaded_cards = self.converter.load_set("DOM")

        assert len(loaded_cards) == 2
        assert loaded_cards[0]["name"] == "Card 1"
        assert loaded_cards[1]["name"] == "Card 2"

    def test_load_set_not_found(self) -> None:
        """Test loading a non-existent set."""
        with pytest.raises(FileNotFoundError):
            self.converter.load_set("NOTEXIST")

    def test_list_available_sets(self) -> None:
        """Test listing available sets."""
        # Save some sets
        self.converter.save_set("DOM", [{"name": "Card 1"}])
        self.converter.save_set("WAR", [{"name": "Card 2"}])

        available = self.converter.list_available_sets()

        assert len(available) == 2
        assert "DOM" in available
        assert "WAR" in available

    def test_list_available_sets_empty(self) -> None:
        """Test listing available sets when none exist."""
        # Create converter with non-existent directory
        shutil.rmtree(self.temp_dir)

        available = self.converter.list_available_sets()

        assert available == []

    def test_save_set_creates_uppercase_directory(self) -> None:
        """Test that set directories are created in uppercase."""
        cards = [{"name": "Card 1"}]

        self.converter.save_set("dom", cards)

        set_dir = pathlib.Path(self.temp_dir) / "DOM"
        assert set_dir.exists()
        assert set_dir.is_dir()

    def test_save_set_overwrites_existing(self) -> None:
        """Test that saving a set overwrites existing files."""
        # Save initial set
        cards_v1 = [{"name": "Card 1"}]
        self.converter.save_set("DOM", cards_v1)

        # Save updated set
        cards_v2 = [{"name": "Card 1"}, {"name": "Card 2"}]
        self.converter.save_set("DOM", cards_v2)

        # Load and verify
        loaded_cards = self.converter.load_set("DOM")
        assert len(loaded_cards) == 2


if __name__ == "__main__":
    unittest.main()
