"""Test cases for prefer order functionality."""

import unittest
from unittest.mock import MagicMock, patch

from api.api_resource import APIResource
from api.enums import PreferOrder


class TestPreferOrder(unittest.TestCase):
    """Test cases for prefer order parameter."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource()

    def test_prefer_order_enum_values(self) -> None:
        """Test that PreferOrder enum has all expected values."""
        assert PreferOrder.DEFAULT == "default"
        assert PreferOrder.OLDEST == "oldest"
        assert PreferOrder.NEWEST == "newest"
        assert PreferOrder.USD_LOW == "usd_low"
        assert PreferOrder.USD_HIGH == "usd_high"
        assert PreferOrder.PROMO == "promo"

    def test_search_accepts_prefer_parameter(self) -> None:
        """Test that search method accepts prefer parameter."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool, \
             patch.object(self.api_resource, "_setup_complete") as mock_setup:
            mock_setup.return_value = True
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [
                {"total_cards_count": 0, "name": None},
            ]
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Test that search accepts prefer parameter without error
            result = self.api_resource.search(
                query="cmc=3",
                orderby="cmc",
                direction="asc",
                unique="card",
                prefer=PreferOrder.OLDEST,
            )
            assert result is not None
            assert "cards" in result

    def test_search_prefer_parameter_in_sql_query(self) -> None:
        """Test that prefer parameter affects SQL query generation."""
        with patch.object(self.api_resource, "_conn_pool") as mock_pool, \
             patch.object(self.api_resource, "_setup_complete") as mock_setup:
            mock_setup.return_value = True
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [
                {"total_cards_count": 0, "name": None},
            ]
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Test with oldest prefer
            result = self.api_resource.search(
                query="cmc=3",
                prefer=PreferOrder.OLDEST,
            )
            # Check that released_at is in the SQL query
            assert "released_at" in result["compiled"]

            # Test with newest prefer
            result = self.api_resource.search(
                query="cmc=3",
                prefer=PreferOrder.NEWEST,
            )
            assert "released_at" in result["compiled"]

            # Test with usd-low prefer
            result = self.api_resource.search(
                query="cmc=3",
                prefer=PreferOrder.USD_LOW,
            )
            assert "price_usd" in result["compiled"]

            # Test with usd-high prefer
            result = self.api_resource.search(
                query="cmc=3",
                prefer=PreferOrder.USD_HIGH,
            )
            assert "price_usd" in result["compiled"]

            # Test with default prefer
            result = self.api_resource.search(
                query="cmc=3",
                prefer=PreferOrder.DEFAULT,
            )
            assert "prefer_score" in result["compiled"]
