"""Integration tests using testcontainers with real PostgreSQL database."""

import os
import pathlib
import time
from collections.abc import Generator

import psycopg
import psycopg.rows
import pytest
from testcontainers.postgres import PostgresContainer

from api.api_resource import APIResource


class TestContainerIntegration:
    """Integration tests using testcontainers with real PostgreSQL."""

    @pytest.fixture(scope="class")
    def postgres_container(self) -> Generator[PostgresContainer, None, None]:
        """Create and manage PostgreSQL test container."""
        with PostgresContainer(
            image="postgres:15-alpine",
            username="testuser",
            password="testpass",  # noqa: S106
            dbname="testdb",
            port=5432,
        ) as postgres:
            # Wait for container to be ready
            time.sleep(2)
            yield postgres

    @pytest.fixture(scope="class")
    def db_connection(self, postgres_container: PostgresContainer) -> Generator[psycopg.Connection, None, None]:
        """Create database connection to test container."""
        # Get connection parameters from container
        host = postgres_container.get_container_host_ip()
        port = postgres_container.get_exposed_port(5432)

        connection_params = {
            "host": host,
            "port": port,
            "dbname": "testdb",
            "user": "testuser",
            "password": "testpass",
        }

        with psycopg.connect(**connection_params) as conn:
            conn.row_factory = psycopg.rows.dict_row
            yield conn

    @pytest.fixture(scope="class")
    def setup_test_database(self, db_connection: psycopg.Connection) -> None:
        """Set up test database schema and data."""
        # Load test schema
        test_dir = pathlib.Path(__file__).parent
        schema_file = test_dir / "fixtures" / "test_schema.sql"
        data_file = test_dir / "fixtures" / "test_data.sql"

        with db_connection.cursor() as cursor:
            # Execute schema
            cursor.execute(schema_file.read_text())
            # Execute test data
            cursor.execute(data_file.read_text())
            db_connection.commit()

    @pytest.fixture
    def api_resource_with_test_db(
        self,
        postgres_container: PostgresContainer,
        setup_test_database: None,  # noqa: ARG002
    ) -> Generator[APIResource, None, None]:
        """Create APIResource configured to use test database."""
        # Store original environment variables
        original_env = {
            key: os.environ.get(key)
            for key in ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
        }

        try:
            # Set environment variables for test database
            host = postgres_container.get_container_host_ip()
            port = postgres_container.get_exposed_port(5432)

            os.environ.update({
                "PGHOST": host,
                "PGPORT": str(port),
                "PGDATABASE": "testdb",
                "PGUSER": "testuser",
                "PGPASSWORD": "testpass",
            })

            # Create APIResource instance
            api = APIResource()
            yield api

            # Clean up connection pool
            if hasattr(api, "_conn_pool"):
                api._conn_pool.close()

        finally:
            # Restore original environment variables
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_database_ready(self, api_resource_with_test_db: APIResource) -> None:
        """Test that database is ready and migrations table exists."""
        result = api_resource_with_test_db.db_ready()
        assert result is True

    def test_query_parsing_with_database(self, api_resource_with_test_db: APIResource) -> None:
        """Test query parsing and execution against real database."""
        # Test a simple search query
        result = api_resource_with_test_db.search(
            q="type:creature",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Serra Angel (the only creature in test data)
        cards = result["cards"]
        assert len(cards) == 1
        assert cards[0]["name"] == "Serra Angel"

    def test_card_search_by_name(self, api_resource_with_test_db: APIResource) -> None:
        """Test searching for cards by name."""
        result = api_resource_with_test_db.search(
            q='name:"Lightning Bolt"',
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        cards = result["cards"]
        assert len(cards) == 1

        card = cards[0]
        assert card["name"] == "Lightning Bolt"

    def test_color_search(self, api_resource_with_test_db: APIResource) -> None:
        """Test searching for cards by color."""
        result = api_resource_with_test_db.search(
            q="c:red",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Lightning Bolt (red card)
        cards = result["cards"]
        assert len(cards) == 1
        assert cards[0]["name"] == "Lightning Bolt"

    def test_cmc_search(self, api_resource_with_test_db: APIResource) -> None:
        """Test searching for cards by converted mana cost."""
        result = api_resource_with_test_db.search(
            q="cmc=0",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Black Lotus (CMC 0)
        cards = result["cards"]
        assert len(cards) == 1
        assert cards[0]["name"] == "Black Lotus"

    def test_power_toughness_search(self, api_resource_with_test_db: APIResource) -> None:
        """Test searching for creatures by power and toughness."""
        result = api_resource_with_test_db.search(
            q="power=4 toughness=4",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Serra Angel (4/4 creature)
        cards = result["cards"]
        assert len(cards) == 1
        assert cards[0]["name"] == "Serra Angel"

    def test_get_all_tags_with_real_db(self, api_resource_with_test_db: APIResource) -> None:
        """Test getting all tags from real database."""
        tags = api_resource_with_test_db._get_all_tags()

        expected_tags = {"flying", "vigilance", "burn", "mana-acceleration"}
        assert tags == expected_tags

    def test_database_operations_isolation(self, api_resource_with_test_db: APIResource) -> None:
        """Test that database operations are properly isolated."""
        # This test verifies that we're working with the test database
        # and not affecting the main application database

        # Count cards in test database using a query that matches all cards
        result = api_resource_with_test_db.search(q="cmc>=0", limit=100)

        # Should only have our test cards
        cards = result["cards"]
        assert len(cards) == 3
        card_names = {card["name"] for card in cards}
        expected_names = {"Lightning Bolt", "Serra Angel", "Black Lotus"}
        assert card_names == expected_names

    def test_get_pid(self, api_resource_with_test_db: APIResource) -> None:
        """Test basic API functionality with real database."""
        pid = api_resource_with_test_db.get_pid()
        assert isinstance(pid, int)
        assert pid > 0
