"""Integration tests using testcontainers with real PostgreSQL database."""

from __future__ import annotations

import json
import multiprocessing
import os
import pathlib
import time
from typing import TYPE_CHECKING

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from api.api_resource import APIResource

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import Any

def get_sample_cards() -> list[dict[str, Any]]:
    test_dir = pathlib.Path(__file__).parent
    api_dir = test_dir.parent
    project_dir = api_dir.parent
    docs_dir = project_dir / "docs"
    sample_data_dir = docs_dir / "sample_data"

    sample_cards = []
    for data_file in sample_data_dir.glob("*.json"):
        with data_file.open("r", encoding="utf-8") as f:
            sample_cards.append(json.load(f))
    return sample_cards

class TestContainerIntegration:
    """Integration tests using testcontainers with real PostgreSQL."""

    @pytest.fixture(scope="class")
    def postgres_container(self: TestContainerIntegration) -> Generator[PostgresContainer]:
        """Create and manage PostgreSQL test container."""
        container = PostgresContainer(
            image="postgres:18",
            username="testuser",
            password="testpass",  # noqa: S106
            dbname="testdb",
        ).with_bind_ports(5432, 5433)  # Bind internal 5432 to host 5433

        with container as postgres:
            # Wait for database to be ready with proper health check
            self._wait_for_database_ready(postgres)
            yield postgres

    def _wait_for_database_ready(self: TestContainerIntegration, postgres_container: PostgresContainer, timeout: int = 30) -> None:
        """Wait for the database to be ready by running a simple query."""
        host = postgres_container.get_container_host_ip()
        port = postgres_container.get_exposed_port(5432)  # This should return 5433 due to bind_ports

        connection_params = {
            "host": host,
            "port": port,
            "dbname": "testdb",
            "user": "testuser",
            "password": "testpass",
        }

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with psycopg.connect(**connection_params) as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT 1")
                        cursor.fetchone()
                    return  # Database is ready
            except (psycopg.Error, OSError):  # Catch specific database and connection errors
                time.sleep(0.5)  # Wait before retrying
                continue

        msg = f"Database not ready within {timeout} seconds"
        raise RuntimeError(msg)


    @pytest.fixture(scope="class")
    def test_db_environment(self: TestContainerIntegration, postgres_container: PostgresContainer) -> Generator[None]:
        """Set up and restore environment variables for test database connection."""
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

            yield  # Test runs here with environment configured

        finally:
            # Restore original environment variables
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    @pytest.fixture(scope="class")
    def api_resource(self: TestContainerIntegration, test_db_environment: None) -> Generator[APIResource]:  # noqa: ARG002
        """Create APIResource instance, set up database schema and test data, then yield the configured instance."""
        # Create APIResource instance
        schema_setup_event = multiprocessing.Event()
        api = APIResource(schema_setup_event=schema_setup_event)

        # Set up the schema using real migrations
        api.setup_schema()

        sample_cards = get_sample_cards()

        api._load_cards_with_staging(sample_cards)
        # Yield the fully configured APIResource for tests to use
        yield api

        # Clean up connection pool
        if hasattr(api, "_conn_pool"):
            api._conn_pool.close()



    def test_database_ready(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test that database is ready and migrations table exists."""
        result = api_resource.db_ready()
        assert result is True

    def test_query_parsing_with_database(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test query parsing and execution against real database."""
        # Test a simple search query
        result = api_resource.search(
            q="type:creature",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Serra Angel and Llanowar Elves
        # this may need to be updated if we add more to sample data
        cards = result["cards"]
        assert sorted(card["name"] for card in cards) == ["Llanowar Elves", "Serra Angel"]

    def test_card_search_by_name(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test searching for cards by name."""
        result = api_resource.search(
            q='name:"Lightning Bolt"',
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        cards = result["cards"]
        assert len(cards) == 1

        card = cards[0]
        assert card["name"] == "Lightning Bolt"

    def test_color_search(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test searching for cards by color."""
        result = api_resource.search(
            q="c:red",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Lightning Bolt (red card)
        cards = result["cards"]
        assert len(cards) == 1
        assert cards[0]["name"] == "Lightning Bolt"

    def test_cmc_search(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test searching for cards by converted mana cost."""
        result = api_resource.search(
            q="cmc=0",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Black Lotus (CMC 0)
        cards = result["cards"]
        found_names = sorted(card["name"] for card in cards)
        assert found_names == ["Black Lotus", "Plains", "Stomping Ground"]

    def test_power_toughness_search(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test searching for creatures by power and toughness."""
        result = api_resource.search(
            q="power=4 toughness=4",
            limit=10,
        )

        assert isinstance(result, dict)
        assert "cards" in result

        # Should find Serra Angel (4/4 creature)
        cards = result["cards"]
        assert len(cards) == 1
        assert cards[0]["name"] == "Serra Angel"

    @pytest.mark.xfail(reason="No tags in sample data")
    def test_get_all_tags_with_real_db(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test getting all tags from real database."""
        tags = api_resource._get_all_tags()

        expected_tags = {"flying", "vigilance", "burn", "mana-acceleration"}
        assert tags == expected_tags

    def test_database_operations_isolation(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test that database operations are properly isolated."""
        # This test verifies that we're working with the test database
        # and not affecting the main application database

        # Count cards in test database using a query that matches all cards
        result = api_resource.search(q="cmc<999", limit=100)
        sample_cards = get_sample_cards()

        # Should only have our test cards
        cards = result["cards"]
        assert len(cards) == len(sample_cards)
        card_names = {card["name"] for card in cards}
        expected_names = {card["name"] for card in sample_cards}
        assert card_names == expected_names

    def test_get_pid(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test basic API functionality with real database."""
        pid = api_resource.get_pid()
        assert isinstance(pid, int)
        assert pid > 0

    def test_import_card_by_name_integration(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test importing a card by name using the real Scryfall API and database."""
        card_name = "Beast Within"

        # Import the card using the import_card_by_name method
        import_result = api_resource.import_card_by_name(card_name=card_name)

        # Check that the import was successful
        assert import_result["status"] == "success"
        items_loaded = import_result["items_loaded"]
        expected_items_loaded = {"artists": 7, "illustrations": 7, "illustration_artists": 7, "cards": 1, "card_sets": 32, "card_printings": 35, "card_faces": 1, "card_face_printings": 35}
        for key in items_loaded.keys() | expected_items_loaded.keys():
            assert expected_items_loaded[key] <= items_loaded[key]

        # Now test that we can search for it by name
        search_result = api_resource.search(q=f"name:{card_name}", limit=10)
        found_cards = search_result["cards"]

        assert len(found_cards) >= 1, f"Card '{card_name}' should be findable after import"

        # Find the exact match
        imported_card = found_cards[0]

        # Verify key properties of the imported card
        assert imported_card["name"] == card_name

        # Check that it has mana cost information (this should be present from Scryfall data)
        observed_mana_cost = imported_card["mana_cost"]
        assert observed_mana_cost == "{2}{G}", f"Beast Within should cost {{2}}{{G}}, got: {observed_mana_cost}"

    def test_import_card_and_search_by_set(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test importing a card from Scryfall and then searching by set code to verify set is populated."""
        # Choose a card that shouldn't already exist in the test database
        card_name = "Mox Ruby"  # A well-known card from Alpha/Beta

        # Import the card using the import_card_by_name method
        import_result = api_resource.import_card_by_name(card_name=card_name)

        # Check that the import was successful (or already exists, which is also fine for this test)
        assert import_result["status"] in ["success"]

        # Now test that we can search for the card using set search
        for set_code in ["lea", "leb", "2ed"]:
            set_search_result = api_resource.search(q=f"ruby set:{set_code}", limit=100)
            found_cards = set_search_result["cards"]
            assert len(found_cards) >= 1, f"Should find at least one card with set:{set_code}"
            assert "Mox Ruby" in [card["name"] for card in found_cards], f"Should find Mox Ruby in set:{set_code}"

    def test_artist_search_integration(self: TestContainerIntegration, api_resource: APIResource) -> None:
        """Test end-to-end artist search functionality with real database."""
        # Import Brainstorm card which has "Willian Murai" as artist
        import_result = api_resource.import_card_by_name(card_name="Brainstorm")

        # Check if import was successful
        if import_result.get("status") != "success":
            pytest.skip(f"Card import failed: {import_result.get('message', 'Unknown error')}")

        # Test artist search by full name
        result = api_resource.search(q='artist:"Willian Murai"')
        cards = result["cards"]
        assert len(cards) >= 1, "Should find at least one card by Willian Murai"

        # Find Brainstorm specifically and verify artist field
        brainstorm_found = False
        for card in cards:
            if card["name"] == "Brainstorm":
                brainstorm_found = True
                assert card.get("artist") == "Willian Murai", f"Brainstorm should have 'Willian Murai' as artist, got: {card.get('artist')}"
                break

        assert brainstorm_found, "Brainstorm should be found by artist search"

        # Test artist search by partial name (case insensitive)
        result_partial = api_resource.search(q="artist:murai")
        cards_partial = result_partial["cards"]
        assert len(cards_partial) >= 1, "Should find cards by partial artist name search"

        # Verify Brainstorm is found in partial search
        brainstorm_in_partial = any(card["name"] == "Brainstorm" for card in cards_partial)
        assert brainstorm_in_partial, "Brainstorm should be found by partial artist search"

        # Test shorthand artist search
        result_shorthand = api_resource.search(q="a:murai")
        cards_shorthand = result_shorthand["cards"]
        assert len(cards_shorthand) >= 1, "Should find cards using shorthand 'a:' for artist"

        # Verify Brainstorm is found in shorthand search
        brainstorm_in_shorthand = any(card["name"] == "Brainstorm" for card in cards_shorthand)
        assert brainstorm_in_shorthand, "Brainstorm should be found by shorthand artist search"

        # Test combined artist search with other attributes (Brainstorm has cmc=1)
        result_combined = api_resource.search(q="cmc=1 artist:murai")
        cards_combined = result_combined["cards"]
        assert len(cards_combined) >= 1, "Should find cards matching both CMC and artist criteria"

        # Verify Brainstorm is found in combined search and matches both criteria
        brainstorm_in_combined = False
        for card in cards_combined:
            if card["name"] == "Brainstorm":
                brainstorm_in_combined = True
                assert card.get("cmc") == 1, "Brainstorm should have CMC = 1"
                assert card.get("artist") == "Willian Murai", "Brainstorm should have correct artist"
                break

        assert brainstorm_in_combined, "Brainstorm should be found by combined search"
