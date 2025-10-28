"""Tests for the random card endpoint."""

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import falcon
import pytest

from api.api_resource import APIResource


def create_test_card(
    card_id: str | None = None,
    name: str = "Test Card",
    **kwargs: Any,
) -> dict:
    """Create a minimal test card for testing."""
    if card_id is None:
        card_id = str(uuid.uuid4())

    card = {
        "id": card_id,
        "name": name,
        "legalities": {"standard": "legal"},
        "games": ["paper"],
        "type_line": "Creature — Test",
        "colors": ["R"],
        "color_identity": ["R"],
        "keywords": [],
        "prices": {"usd": "1.00"},
        "set": "test",
        "rarity": "common",
        "collector_number": "1",
        "image_uris": {
            "small": f"https://example.com/{card_id}.jpg",
            "normal": f"https://example.com/{card_id}.jpg",
        },
    }
    card.update(kwargs)
    return card


@pytest.fixture(name="patch_conn_pool")
def patch_conn_pool_fixture() -> MagicMock:
    """Patch connection pool."""
    mock_conn_pool = MagicMock()
    with patch("api.api_resource.db_utils.make_pool") as mock_pool:
        mock_pool.return_value = mock_conn_pool
        yield mock_conn_pool


class TestRandomEndpoint:
    """Test the /random endpoint functionality."""

    @pytest.fixture(autouse=True)
    def setUp(self, request: pytest.FixtureRequest, patch_conn_pool: MagicMock) -> None:
        """Set up test fixtures."""
        del patch_conn_pool
        self_reference = request.instance

        self_reference.mock_conn_pool = MagicMock()
        self_reference.api_resource = APIResource()
        self_reference.api_resource._conn_pool = self_reference.mock_conn_pool

    def test_random_search_returns_second_card_when_two_available(self) -> None:
        """Test that random_search returns the second card to avoid bias."""
        # Create two test cards with specific UUIDs

        card1 = {
            "card_artist": "Artist 1",
            "name": "Card One",
            "set_code": "TST",
            "cmc": 1,
            "collector_number": "1",
            "power": "1",
            "toughness": "1",
            "edhrec_rank": 100,
            "mana_cost": "{R}",
            "oracle_text": "Test card 1",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        card2 = {
            "card_artist": "Artist 2",
            "name": "Card Two",
            "set_code": "TST",
            "cmc": 2,
            "collector_number": "2",
            "power": "2",
            "toughness": "2",
            "edhrec_rank": 200,
            "mana_cost": "{U}",
            "oracle_text": "Test card 2",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        # Mock the cursor to return both cards
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [card1, card2]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value.__exit__.return_value = False

        self.mock_conn_pool.connection.return_value = mock_conn

        # Call random_search
        results = self.api_resource.random_search(num_cards=1)

        # Should return the second card, not the first
        assert len(results) == 1
        assert results[0]["name"] == "Card Two"

    def test_random_search_wraps_around_when_only_one_card_returned(self) -> None:
        """Test that random_search wraps around when hitting the end of UUID range."""
        card_at_end = {
            "card_artist": "Artist End",
            "name": "Card At End",
            "set_code": "TST",
            "cmc": 1,
            "collector_number": "1",
            "power": "1",
            "toughness": "1",
            "edhrec_rank": 100,
            "mana_cost": "{R}",
            "oracle_text": "Card near end",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        card_at_beginning = {
            "card_artist": "Artist Beginning",
            "name": "Card At Beginning",
            "set_code": "TST",
            "cmc": 2,
            "collector_number": "2",
            "power": "2",
            "toughness": "2",
            "edhrec_rank": 200,
            "mana_cost": "{U}",
            "oracle_text": "Card at beginning",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        # Mock the cursor to return one card for first query, then wrap around
        mock_cursor = MagicMock()
        # First call returns only one card (hit the end)
        # Second call (fallback) returns the first card from the beginning
        mock_cursor.fetchall.side_effect = [
            [card_at_end],  # Only one card found (at end of range)
            [card_at_beginning],  # Fallback query returns first card
        ]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value.__exit__.return_value = False

        self.mock_conn_pool.connection.return_value = mock_conn

        # Call random_search
        results = self.api_resource.random_search(num_cards=1)

        # Should wrap around and return the first card
        assert len(results) == 1
        assert results[0]["name"] == "Card At Beginning"

        # Verify that execute was called twice (main query + fallback)
        assert mock_cursor.execute.call_count == 2

    def test_random_search_handles_empty_database(self) -> None:
        """Test that random_search handles an empty database gracefully."""
        # Mock the cursor to return empty results
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value.__exit__.return_value = False

        self.mock_conn_pool.connection.return_value = mock_conn

        # Call random_search
        results = self.api_resource.random_search(num_cards=1)

        # Should return empty list for empty database
        assert len(results) == 0

    def test_random_search_multiple_cards(self) -> None:
        """Test that random_search can return multiple random cards."""
        # Create test cards
        card1 = {
            "card_artist": "Artist 1",
            "name": "Card One",
            "set_code": "TST",
            "cmc": 1,
            "collector_number": "1",
            "power": "1",
            "toughness": "1",
            "edhrec_rank": 100,
            "mana_cost": "{R}",
            "oracle_text": "Test card 1",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        card2 = {
            "card_artist": "Artist 2",
            "name": "Card Two",
            "set_code": "TST",
            "cmc": 2,
            "collector_number": "2",
            "power": "2",
            "toughness": "2",
            "edhrec_rank": 200,
            "mana_cost": "{U}",
            "oracle_text": "Test card 2",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        # Mock the cursor to return cards for each iteration
        mock_cursor = MagicMock()
        # Return two cards each time (so we take the second one)
        mock_cursor.fetchall.side_effect = [
            [card1, card2],  # First iteration
            [card2, card1],  # Second iteration
        ]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value.__exit__.return_value = False

        self.mock_conn_pool.connection.return_value = mock_conn

        # Call random_search with num_cards=2
        results = self.api_resource.random_search(num_cards=2)

        # Should return two cards
        assert len(results) == 2
        assert results[0]["name"] == "Card Two"
        assert results[1]["name"] == "Card One"

    def test_random_endpoint_returns_single_card(self) -> None:
        """Test that the /random endpoint returns a single card."""
        card = {
            "card_artist": "Artist",
            "name": "Random Card",
            "set_code": "TST",
            "cmc": 3,
            "collector_number": "3",
            "power": "3",
            "toughness": "3",
            "edhrec_rank": 300,
            "mana_cost": "{G}",
            "oracle_text": "Random test card",
            "set_name": "Test Set",
            "type_line": "Creature",
        }

        # Mock random_search to return a card
        with patch.object(self.api_resource, "random_search", return_value=[card]):
            with patch.object(self.api_resource, "import_data"):
                result = self.api_resource.random()

        # Should return the card directly (not in a list)
        assert isinstance(result, dict)
        assert result["name"] == "Random Card"

    def test_random_endpoint_raises_404_when_no_cards(self) -> None:
        """Test that the /random endpoint raises 404 when database is empty."""
        # Mock random_search to return empty list
        with patch.object(self.api_resource, "random_search", return_value=[]):
            with patch.object(self.api_resource, "import_data"):
                with pytest.raises(falcon.HTTPNotFound):
                    self.api_resource.random()
