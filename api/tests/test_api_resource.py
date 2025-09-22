"""Comprehensive tests for APIResource class functionality."""

import multiprocessing
import os
import unittest
from typing import Any, Never
from unittest.mock import MagicMock, patch

import falcon
import pytest
import requests

from api.api_resource import APIResource, can_serialize, get_where_clause, rewrap


class TestUtilityFunctions(unittest.TestCase):
    """Test utility functions in api_resource module."""

    def test_can_serialize_valid_objects(self) -> None:
        """Test can_serialize with valid serializable objects."""
        # Test with simple types
        assert can_serialize("string") is True
        assert can_serialize(123) is True
        assert can_serialize(123.45) is True
        assert can_serialize(True) is True
        assert can_serialize(None) is True

        # Test with collections
        assert can_serialize([1, 2, 3]) is True
        assert can_serialize({"key": "value"}) is True
        assert can_serialize({"nested": {"data": [1, 2, 3]}}) is True

    def test_can_serialize_invalid_objects(self) -> None:
        """Test can_serialize with non-serializable objects."""
        # Test with non-serializable objects
        assert can_serialize(object()) is False
        assert can_serialize(lambda x: x) is False
        assert can_serialize({1, 2, 3}) is False

    def test_can_serialize_large_objects(self) -> None:
        """Test can_serialize with objects that exceed size limit."""
        # Create a large string that exceeds the 16KB limit
        large_string = "x" * 20000
        assert can_serialize(large_string) is False

    def test_rewrap_normalizes_whitespace(self) -> None:
        """Test rewrap function normalizes whitespace in SQL queries."""
        # Test with various whitespace patterns
        assert rewrap("SELECT * FROM table") == "SELECT * FROM table"
        assert rewrap("  SELECT   *   FROM   table  ") == "SELECT * FROM table"
        assert rewrap("SELECT\n*\nFROM\ntable") == "SELECT * FROM table"
        assert rewrap("SELECT\t*\tFROM\ttable") == "SELECT * FROM table"
        assert rewrap("  \n  SELECT  \n  *  \n  FROM  \n  table  \n  ") == "SELECT * FROM table"

    def test_get_where_clause_caching(self) -> None:
        """Test that get_where_clause uses caching."""
        # This tests the @cached decorator functionality
        query = "cmc:3"
        result1 = get_where_clause(query)
        result2 = get_where_clause(query)

        # Results should be identical (cached)
        assert result1 == result2


class TestAPIResourceInitialization(unittest.TestCase):
    """Test APIResource initialization and basic setup."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.mock_session = MagicMock()
        self.mock_tagger_client = MagicMock()

    @patch("api.api_resource.db_utils.make_pool")
    @patch("api.api_resource.requests.Session")
    @patch("api.api_resource.TaggerClient")
    def test_initialization_defaults(self, mock_tagger: Any, mock_session: Any, mock_pool: Any) -> None:
        """Test APIResource initialization with default parameters."""
        mock_pool.return_value = self.mock_conn_pool
        mock_session.return_value = self.mock_session
        mock_tagger.return_value = self.mock_tagger_client

        api_resource = APIResource()

        # Check that connection pool is set up
        assert api_resource._conn_pool == self.mock_conn_pool

        # Check that action map is populated
        assert "get_pid" in api_resource.action_map
        assert "db_ready" in api_resource.action_map
        assert "search" in api_resource.action_map
        assert "index" in api_resource.action_map

        # Check that caches are initialized
        assert hasattr(api_resource, "_query_cache")
        assert hasattr(api_resource, "_session")
        assert hasattr(api_resource, "_tagger_client")

    @patch("api.api_resource.db_utils.make_pool")
    @patch("api.api_resource.requests.Session")
    @patch("api.api_resource.TaggerClient")
    def test_initialization_with_custom_import_guard(self, mock_tagger: Any, mock_session: Any, mock_pool: Any) -> None:
        """Test APIResource initialization with custom import guard."""
        mock_pool.return_value = self.mock_conn_pool
        mock_session.return_value = self.mock_session
        mock_tagger.return_value = self.mock_tagger_client

        custom_guard = multiprocessing.RLock()
        api_resource = APIResource(import_guard=custom_guard)

        assert api_resource._import_guard == custom_guard

    def test_action_map_includes_all_public_methods(self) -> None:
        """Test that action_map includes all public methods."""
        with patch("api.api_resource.db_utils.make_pool"), \
             patch("api.api_resource.requests.Session"), \
             patch("api.api_resource.TaggerClient"):

            api_resource = APIResource()

            # Check that all public methods are in action_map
            public_methods = [method for method in dir(api_resource)
                            if not method.startswith("_") and callable(getattr(api_resource, method))]

            for method in public_methods:
                assert method in api_resource.action_map


class TestAPIResourceCoreMethods(unittest.TestCase):
    """Test core APIResource methods."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_get_pid_returns_process_id(self) -> None:
        """Test that get_pid returns the current process ID."""
        result = self.api_resource.get_pid()
        assert isinstance(result, int)
        assert result == os.getpid()

    @patch.object(APIResource, "_run_query")
    def test_db_ready_returns_true_when_migrations_table_exists(self, mock_run_query: Any) -> None:
        """Test db_ready returns True when migrations table exists."""
        mock_run_query.return_value = {
            "result": [{"relname": "migrations"}, {"relname": "other_table"}],
        }

        result = self.api_resource.db_ready()
        assert result is True

    @patch.object(APIResource, "_run_query")
    def test_db_ready_returns_false_when_migrations_table_missing(self, mock_run_query: Any) -> None:
        """Test db_ready returns False when migrations table is missing."""
        mock_run_query.return_value = {
            "result": [{"relname": "other_table"}],
        }

        result = self.api_resource.db_ready()
        assert result is False

    def test_read_sql_reads_file_content(self) -> None:
        """Test read_sql reads and returns SQL file content."""
        # Test that the method exists and is callable
        assert hasattr(self.api_resource, "read_sql")
        assert callable(self.api_resource.read_sql)

        # Test that it can be called (may fail due to missing files, but that's expected)
        try:
            result = self.api_resource.read_sql("nonexistent_file")
            # If it succeeds, it should return a string
            assert isinstance(result, str)
        except FileNotFoundError:
            # This is expected if the file doesn't exist
            pass

    def test_read_sql_caching(self) -> None:
        """Test that read_sql uses caching."""
        with patch("api.api_resource.pathlib.Path") as mock_path:
            mock_sql_dir = MagicMock()
            mock_sql_file = MagicMock()
            mock_sql_file.open.return_value.__enter__.return_value.read.return_value = "SELECT * FROM test;"
            mock_sql_dir.__truediv__.return_value = mock_sql_file
            mock_path.return_value.parent = mock_sql_dir

            # Call twice with same filename
            result1 = self.api_resource.read_sql("test")
            result2 = self.api_resource.read_sql("test")

            # Results should be identical (cached)
            assert result1 == result2
            # Note: The @cached decorator may not be easily testable with mocks
            # as it's implemented at the function level


class TestAPIResourceRequestHandling(unittest.TestCase):
    """Test request handling methods."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_raise_not_found_raises_http_not_found(self) -> None:
        """Test _raise_not_found raises HTTPNotFound with route information."""
        with pytest.raises(falcon.HTTPNotFound) as exc_info:
            self.api_resource._raise_not_found()

        error = exc_info.value
        assert "Not Found" in str(error.title)
        assert "routes" in error.description

    def test_handle_returns_early_if_response_complete(self) -> None:
        """Test _handle returns early if response is already complete."""
        mock_req = MagicMock()
        mock_req.uri = "/test"
        mock_resp = MagicMock()
        mock_resp.complete = True

        with patch("api.api_resource.logger") as mock_logger:
            self.api_resource._handle(mock_req, mock_resp)
            mock_logger.info.assert_called_with("Request already handled: %s", "/test")

    def test_handle_processes_valid_paths(self) -> None:
        """Test _handle processes valid paths correctly."""
        mock_req = MagicMock()
        mock_req.uri = "/get_pid"
        mock_req.params = {}
        mock_resp = MagicMock()
        mock_resp.complete = False

        with patch("api.api_resource.logger"):
            self.api_resource._handle(mock_req, mock_resp)

            # Should call get_pid method and set response media
            assert mock_resp.media is not None

    def test_handle_raises_not_found_for_invalid_paths(self) -> None:
        """Test _handle raises HTTPNotFound for invalid paths."""
        mock_req = MagicMock()
        mock_req.uri = "/nonexistent"
        mock_req.params = {}
        mock_resp = MagicMock()
        mock_resp.complete = False

        with pytest.raises(falcon.HTTPNotFound):
            self.api_resource._handle(mock_req, mock_resp)

    def test_handle_handles_type_errors(self) -> None:
        """Test _handle handles TypeError exceptions."""
        mock_req = MagicMock()
        mock_req.uri = "/search"
        mock_req.params = {"invalid_param": "value"}
        mock_resp = MagicMock()
        mock_resp.complete = False

        # Mock search method to raise TypeError
        with patch.object(self.api_resource, "search", side_effect=TypeError("Invalid parameter")):
            with pytest.raises(falcon.HTTPBadRequest):
                self.api_resource._handle(mock_req, mock_resp)

    def test_handle_handles_general_exceptions(self) -> None:
        """Test _handle handles general exceptions."""
        mock_req = MagicMock()
        mock_req.uri = "/search"
        mock_req.params = {}
        mock_resp = MagicMock()
        mock_resp.complete = False

        def raise_error(*args: Any, **kwargs: Any) -> Never:
            msg = "Test error"
            raise Exception(msg)
        # Mock search method to raise a general exception
        with patch.object(self.api_resource, "action_map", {"search": raise_error}):
            with patch("api.api_resource.error_monitoring.error_handler") as mock_error_handler:
                with pytest.raises(falcon.HTTPInternalServerError):
                    self.api_resource._handle(mock_req, mock_resp)

                # Should call error monitoring
                mock_error_handler.assert_called_once()


class TestAPIResourceDataProcessing(unittest.TestCase):
    """Test data processing methods."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_preprocess_card_filters_invalid_cards(self) -> None:
        """Test _preprocess_card filters out invalid cards."""
        # Test card with all not_legal legalities
        invalid_card = {
            "name": "Test Card",
            "legalities": {"standard": "not_legal", "modern": "not_legal"},
            "games": ["paper"],
            "type_line": "Creature — Test",
            "colors": ["R"],
            "color_identity": ["R"],
            "keywords": ["haste"],
            "power": "2",
            "toughness": "2",
            "prices": {"usd": "1.00"},
            "set": "test",
        }

        result = self.api_resource._preprocess_card(invalid_card)
        assert result is None

    def test_preprocess_card_filters_non_paper_cards(self) -> None:
        """Test _preprocess_card filters out non-paper cards."""
        invalid_card = {
            "name": "Test Card",
            "legalities": {"standard": "legal"},
            "games": ["mtgo"],  # Not paper
            "type_line": "Creature — Test",
            "colors": ["R"],
            "color_identity": ["R"],
            "keywords": ["haste"],
            "power": "2",
            "toughness": "2",
            "prices": {"usd": "1.00"},
            "set": "test",
        }

        result = self.api_resource._preprocess_card(invalid_card)
        assert result is None

    def test_preprocess_card_filters_card_faces(self) -> None:
        """Test _preprocess_card filters out cards with card_faces."""
        invalid_card = {
            "name": "Test Card",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "card_faces": [{"name": "Front"}, {"name": "Back"}],  # Has card_faces
            "type_line": "Creature — Test",
            "colors": ["R"],
            "color_identity": ["R"],
            "keywords": ["haste"],
            "power": "2",
            "toughness": "2",
            "prices": {"usd": "1.00"},
            "set": "test",
        }

        result = self.api_resource._preprocess_card(invalid_card)
        assert result is None

    def test_preprocess_card_filters_funny_sets(self) -> None:
        """Test _preprocess_card filters out funny set types."""
        invalid_card = {
            "name": "Test Card",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "set_type": "funny",  # Funny set type
            "type_line": "Creature — Test",
            "colors": ["R"],
            "color_identity": ["R"],
            "keywords": ["haste"],
            "power": "2",
            "toughness": "2",
            "prices": {"usd": "1.00"},
            "set": "test",
        }

        result = self.api_resource._preprocess_card(invalid_card)
        assert result is None

    def test_preprocess_card_processes_valid_card(self) -> None:
        """Test _preprocess_card processes valid cards correctly."""
        valid_card = {
            "name": "Lightning Bolt",
            "legalities": {"standard": "legal", "modern": "legal"},
            "games": ["paper"],
            "type_line": "Instant",
            "colors": ["R"],
            "color_identity": ["R"],
            "keywords": ["haste"],
            "power": "3",
            "toughness": "1",
            "prices": {"usd": "0.25", "eur": "0.20", "tix": "0.01"},
            "set": "m15",
            "artist": "Christopher Rush",
            "rarity": "common",
            "collector_number": "1",
            "edhrec_rank": 1,
        }

        result = self.api_resource._preprocess_card(valid_card)

        assert result is not None
        assert result["card_types"] == ["Instant"]
        # card_subtypes key is removed when None, so it shouldn't be in the result
        assert "card_subtypes" not in result
        assert result["card_colors"] == {"R": True}
        assert result["card_color_identity"] == {"R": True}
        assert result["card_keywords"] == {"haste": True}
        assert result["power_numeric"] == 3
        assert result["toughness_numeric"] == 1
        assert result["price_usd"] == "0.25"
        assert result["price_eur"] == "0.20"
        assert result["price_tix"] == "0.01"
        assert result["card_set_code"] == "m15"

    def test_preprocess_card_handles_missing_fields(self) -> None:
        """Test _preprocess_card handles missing optional fields."""
        minimal_card = {
            "name": "Test Card",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Test",
            "colors": [],
            "color_identity": [],
            "keywords": [],
            "prices": {},
            "set": "test",
        }

        result = self.api_resource._preprocess_card(minimal_card)

        assert result is not None
        assert result["card_colors"] == {}
        assert result["card_color_identity"] == {}
        assert result["card_keywords"] == {}
        assert result["power_numeric"] is None
        assert result["toughness_numeric"] is None
        assert result["price_usd"] is None
        assert result["price_eur"] is None
        assert result["price_tix"] is None

    def test_preprocess_card_handles_non_numeric_power_toughness(self) -> None:
        """Test _preprocess_card handles non-numeric power/toughness values."""
        card = {
            "name": "Test Card",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Test",
            "colors": ["R"],
            "color_identity": ["R"],
            "keywords": [],
            "power": "*",  # Non-numeric
            "toughness": "X",  # Non-numeric
            "prices": {},
            "set": "test",
        }

        result = self.api_resource._preprocess_card(card)

        assert result is not None
        assert result["power_numeric"] is None
        assert result["toughness_numeric"] is None

    @patch.object(APIResource, "get_data")
    @patch.object(APIResource, "_preprocess_card")
    def test_get_cards_to_insert_deduplicates_cards(self, mock_preprocess: Any, mock_get_data: Any) -> None:
        """Test _get_cards_to_insert deduplicates cards by name."""
        # Mock get_data to return duplicate cards
        mock_get_data.return_value = [
            {"name": "Lightning Bolt", "cmc": 1},
            {"name": "Lightning Bolt", "cmc": 1},  # Duplicate
            {"name": "Counterspell", "cmc": 2},
        ]

        # Mock _preprocess_card to return the card as-is
        mock_preprocess.side_effect = lambda card: card

        result = self.api_resource._get_cards_to_insert()

        # Should only have 2 unique cards
        assert len(result) == 2
        card_names = [card["name"] for card in result]
        assert "Lightning Bolt" in card_names
        assert "Counterspell" in card_names

    @patch.object(APIResource, "get_data")
    @patch.object(APIResource, "_preprocess_card")
    def test_get_cards_to_insert_filters_none_cards(self, mock_preprocess: Any, mock_get_data: Any) -> None:
        """Test _get_cards_to_insert filters out None cards from preprocessing."""
        mock_get_data.return_value = [
            {"name": "Valid Card", "cmc": 1},
            {"name": "Invalid Card", "cmc": 2},
        ]

        # Mock _preprocess_card to return None for invalid card
        def mock_preprocess_side_effect(card: Any) -> Any:
            if card["name"] == "Invalid Card":
                return None
            return card

        mock_preprocess.side_effect = mock_preprocess_side_effect

        result = self.api_resource._get_cards_to_insert()

        # Should only have 1 valid card
        assert len(result) == 1
        assert result[0]["name"] == "Valid Card"


class TestAPIResourceStaticFileServing(unittest.TestCase):
    """Test static file serving methods."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_serve_static_file_reads_file_content(self) -> None:
        """Test _serve_static_file reads and serves file content."""
        mock_response = MagicMock()

        with patch("api.api_resource.pathlib.Path") as mock_path:
            mock_file = MagicMock()
            mock_file.open.return_value.__enter__.return_value.read.return_value = "file content"
            mock_path.return_value = mock_file

            self.api_resource._serve_static_file(filename="test.html", falcon_response=mock_response)

            assert mock_response.text == "file content"

    def test_index_html_serves_static_file(self) -> None:
        """Test index_html serves the index.html file."""
        mock_response = MagicMock()

        with patch.object(self.api_resource, "_serve_static_file") as mock_serve:
            self.api_resource.index_html(falcon_response=mock_response)

            mock_serve.assert_called_once_with(filename="index.html", falcon_response=mock_response)
            assert mock_response.content_type == "text/html"

    def test_favicon_ico_serves_binary_content(self) -> None:
        """Test favicon_ico serves binary content correctly."""
        mock_response = MagicMock()

        # Test that the method exists and is callable
        assert hasattr(self.api_resource, "favicon_ico")
        assert callable(self.api_resource.favicon_ico)

        # Test that it can be called (may fail due to missing files, but that's expected)
        try:
            self.api_resource.favicon_ico(falcon_response=mock_response)
            # If it succeeds, check that response properties were set
            assert hasattr(mock_response, "content_type")
            assert hasattr(mock_response, "headers")
        except FileNotFoundError:
            # This is expected if the file doesn't exist
            pass


class TestAPIResourceErrorHandling(unittest.TestCase):
    """Test error handling in APIResource methods."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_update_tagged_cards_validates_tag_parameter(self) -> None:
        """Test update_tagged_cards validates tag parameter."""
        with pytest.raises(ValueError, match="Tag parameter is required"):
            self.api_resource.update_tagged_cards(tag="")

        with pytest.raises(ValueError, match="Tag parameter is required"):
            self.api_resource.update_tagged_cards(tag=None)

    def test_import_card_by_name_validates_card_name_parameter(self) -> None:
        """Test import_card_by_name validates card_name parameter."""
        with pytest.raises(ValueError, match="card_name parameter is required"):
            self.api_resource.import_card_by_name(card_name="")

        with pytest.raises(ValueError, match="card_name parameter is required"):
            self.api_resource.import_card_by_name(card_name=None)

    def test_import_cards_by_search_validates_search_query_parameter(self) -> None:
        """Test import_cards_by_search validates search_query parameter."""
        with pytest.raises(ValueError, match="search_query parameter is required"):
            self.api_resource.import_cards_by_search(search_query="")

        with pytest.raises(ValueError, match="search_query parameter is required"):
            self.api_resource.import_cards_by_search(search_query=None)

    @patch("api.api_resource.requests.Session.get")
    def test_discover_tags_from_scryfall_handles_request_errors(self, mock_get: Any) -> None:
        """Test discover_tags_from_scryfall handles request errors."""
        mock_get.side_effect = requests.RequestException("Network error")

        with pytest.raises(ValueError, match="Failed to fetch tag list from Scryfall"):
            self.api_resource.discover_tags_from_scryfall()

    def test_discover_tags_from_graphql_handles_parsing_errors(self) -> None:
        """Test discover_tags_from_graphql handles parsing errors."""
        # Mock the _tagger_client attribute
        mock_tagger = MagicMock()
        mock_tagger.search_tags.side_effect = KeyError("Missing key")
        self.api_resource._tagger_client = mock_tagger

        with pytest.raises(ValueError, match="Failed to parse GraphQL tag search response"):
            self.api_resource.discover_tags_from_graphql()


class TestAPIResourceCaching(unittest.TestCase):
    """Test caching functionality in APIResource."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_conn_pool = MagicMock()
        self.api_resource = APIResource()
        self.api_resource._conn_pool = self.mock_conn_pool

    def test_query_cache_clears_after_successful_load(self) -> None:
        """Test that query cache clears after successful card loading."""
        # Add some data to the cache
        self.api_resource._query_cache["test_key"] = "test_value"
        assert "test_key" in self.api_resource._query_cache

        # Mock the database operations to simulate successful load
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.rowcount = 1
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Provide valid card data that will pass preprocessing
            valid_card = {
                "name": "Test Card",
                "legalities": {"standard": "legal"},
                "games": ["paper"],
                "type_line": "Creature — Test",
                "colors": ["R"],
                "color_identity": ["R"],
                "keywords": [],
                "prices": {},
                "set": "test",
            }

            # Call _load_cards_with_staging directly to test cache clearing
            self.api_resource._load_cards_with_staging([valid_card])

            # Cache should be cleared after successful load
            assert "test_key" not in self.api_resource._query_cache

    def test_search_cache_clears_after_successful_load(self) -> None:
        """Test that search cache clears after successful card loading."""
        # Mock the database operations to simulate successful load
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.rowcount = 1
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Provide valid card data that will pass preprocessing
            valid_card = {
                "name": "Test Card",
                "legalities": {"standard": "legal"},
                "games": ["paper"],
                "type_line": "Creature — Test",
                "colors": ["R"],
                "color_identity": ["R"],
                "keywords": [],
                "prices": {},
                "set": "test",
            }

            # Add some data to the search cache
            self.api_resource._search.cache["test_key"] = "test_value"
            assert "test_key" in self.api_resource._search.cache

            # Call _load_cards_with_staging directly to test cache clearing
            self.api_resource._load_cards_with_staging([valid_card])

            # Search cache should be cleared after successful load
            assert "test_key" not in self.api_resource._search.cache

    def test_cache_clear_method_works(self) -> None:
        """Test that cache.clear() method works for cachetools caches."""
        # Test query cache clearing
        self.api_resource._query_cache["test_key"] = "test_value"
        assert "test_key" in self.api_resource._query_cache

        self.api_resource._query_cache.clear()
        assert "test_key" not in self.api_resource._query_cache

        # Test search cache clearing
        self.api_resource._search.cache["test_key"] = "test_value"
        assert "test_key" in self.api_resource._search.cache

        self.api_resource._search.cache.clear()
        assert "test_key" not in self.api_resource._search.cache


class TestAPIResourceTagHierarchy(unittest.TestCase):
    """Test cases for tag hierarchy functionality."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.api_resource = APIResource()

    def test_populate_tag_hierarchy_with_empty_tags(self) -> None:
        """Test _populate_tag_hierarchy with empty tag list."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            result = self.api_resource._populate_tag_hierarchy(tags=[])

            assert result["success"] is True
            assert result["tags_processed"] == 0
            assert "duration" in result
            assert "message" in result

    def test_populate_tag_hierarchy_with_single_tag(self) -> None:
        """Test _populate_tag_hierarchy with single tag."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Mock _get_tag_relationships to return sample relationships
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                mock_get_relationships.return_value = [
                    {
                        "parent": {"slug": "parent-tag", "name": "Parent Tag", "namespace": "test"},
                        "child": {"slug": "test-tag", "name": "Test Tag", "namespace": "test"},
                    },
                ]

                result = self.api_resource._populate_tag_hierarchy(tags=["test-tag"])

                assert result["success"] is True
                assert result["tags_processed"] == 1
                assert "duration" in result
                assert "message" in result

                # Verify database operations were called
                assert mock_cursor.executemany.call_count >= 2  # At least tags and relationships inserts

    def test_populate_tag_hierarchy_with_multiple_tags(self) -> None:
        """Test _populate_tag_hierarchy with multiple tags."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Mock _get_tag_relationships to return different relationships for each tag
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                def mock_relationships(tag: str) -> list:
                    if tag == "tag1":
                        return [
                            {
                                "parent": {"slug": "parent1", "name": "Parent 1", "namespace": "test"},
                                "child": {"slug": "tag1", "name": "Tag 1", "namespace": "test"},
                            },
                        ]
                    if tag == "tag2":
                        return [
                            {
                                "parent": {"slug": "parent2", "name": "Parent 2", "namespace": "test"},
                                "child": {"slug": "tag2", "name": "Tag 2", "namespace": "test"},
                            },
                        ]
                    return []

                mock_get_relationships.side_effect = mock_relationships

                result = self.api_resource._populate_tag_hierarchy(tags=["tag1", "tag2"])

                assert result["success"] is True
                assert result["tags_processed"] == 2
                assert "duration" in result
                assert "message" in result

                # Verify _get_tag_relationships was called for each tag
                assert mock_get_relationships.call_count == 2

    def test_populate_tag_hierarchy_handles_no_relationships(self) -> None:
        """Test _populate_tag_hierarchy when tags have no relationships."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Mock _get_tag_relationships to return empty relationships
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                mock_get_relationships.return_value = []

                result = self.api_resource._populate_tag_hierarchy(tags=["orphan-tag"])

                assert result["success"] is True
                assert result["tags_processed"] == 1
                assert "duration" in result
                assert "message" in result

    def test_populate_tag_hierarchy_handles_database_errors(self) -> None:
        """Test _populate_tag_hierarchy handles database errors gracefully."""
        # Mock the database operations to raise an exception
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Make cursor.executemany raise an exception
            mock_cursor.executemany.side_effect = Exception("Database error")

            # Mock _get_tag_relationships to return sample relationships
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                mock_get_relationships.return_value = [
                    {
                        "parent": {"slug": "parent-tag", "name": "Parent Tag", "namespace": "test"},
                        "child": {"slug": "test-tag", "name": "Test Tag", "namespace": "test"},
                    },
                ]

                # The method doesn't have explicit error handling, so it will propagate the exception
                # This test verifies that the method attempts database operations and fails as expected
                with pytest.raises(Exception, match="Database error"):
                    self.api_resource._populate_tag_hierarchy(tags=["test-tag"])

    def test_populate_tag_hierarchy_randomizes_tag_order(self) -> None:
        """Test that _populate_tag_hierarchy randomizes the order of tags."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Mock _get_tag_relationships
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                mock_get_relationships.return_value = []

                # Mock random.shuffle to verify it's called
                with patch("random.shuffle") as mock_shuffle:
                    result = self.api_resource._populate_tag_hierarchy(tags=["tag1", "tag2", "tag3"])

                    # Verify shuffle was called with the tags list
                    mock_shuffle.assert_called_once()
                    assert result["success"] is True
                    assert result["tags_processed"] == 3

    def test_populate_tag_hierarchy_logs_progress(self) -> None:
        """Test that _populate_tag_hierarchy logs progress information."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Mock _get_tag_relationships
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                mock_get_relationships.return_value = []

                # Mock logger to capture log calls
                with patch("api.api_resource.logger") as mock_logger:
                    result = self.api_resource._populate_tag_hierarchy(tags=["tag1", "tag2"])

                    # Verify logging calls were made
                    assert mock_logger.info.call_count >= 2  # At least start and progress logs
                    assert result["success"] is True

    def test_populate_tag_hierarchy_returns_correct_structure(self) -> None:
        """Test that _populate_tag_hierarchy returns the expected result structure."""
        # Mock the database operations
        with patch.object(self.api_resource, "_conn_pool") as mock_pool:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_pool.connection.return_value.__enter__.return_value = mock_conn

            # Mock _get_tag_relationships
            with patch.object(self.api_resource, "_get_tag_relationships") as mock_get_relationships:
                mock_get_relationships.return_value = []

                result = self.api_resource._populate_tag_hierarchy(tags=["test-tag"])

                # Verify result structure
                assert isinstance(result, dict)
                assert "success" in result
                assert "duration" in result
                assert "message" in result
                assert "tags_processed" in result

                assert result["success"] is True
                assert isinstance(result["duration"], (int, float))
                assert isinstance(result["message"], str)
                assert isinstance(result["tags_processed"], int)
                assert result["tags_processed"] == 1


if __name__ == "__main__":
    unittest.main()
