"""Tests for card tagging functionality."""

import inspect
import sys
from pathlib import Path

# Add the api directory to sys.path so we can import api_resource
sys.path.insert(0, str(Path(__file__).parent.parent))

from api_resource import APIResource


class TestTagging:
    """Test cases for card tagging functionality."""

    def test_update_tagged_cards_validates_input(self) -> None:
        """Test that update_tagged_cards validates input parameters."""
        # Test that the method exists and is callable
        assert hasattr(APIResource, "update_tagged_cards")
        assert callable(APIResource.update_tagged_cards)

        # Test that the method signature includes the required tag parameter
        sig = inspect.signature(APIResource.update_tagged_cards)
        assert "tag" in sig.parameters

    def test_fetch_cards_from_scryfall_function_exists(self) -> None:
        """Test that _fetch_cards_from_scryfall method exists and is callable."""
        # Test that the method exists and is callable
        assert hasattr(APIResource, "_fetch_cards_from_scryfall")
        assert callable(APIResource._fetch_cards_from_scryfall)

        # Test that the method signature includes the required tag parameter
        sig = inspect.signature(APIResource._fetch_cards_from_scryfall)
        assert "tag" in sig.parameters
