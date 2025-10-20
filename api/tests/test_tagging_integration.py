"""Integration tests for tagging functionality."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from api.api_resource import APIResource


@pytest.fixture(scope="class")
def mock_db_pool() -> Generator[MagicMock]:
    """Mock database connection pool for the entire test class."""
    with patch("api.utils.db_utils.make_pool") as mock_make_pool:
        mock_pool = MagicMock()
        mock_make_pool.return_value = mock_pool
        yield mock_pool


@pytest.fixture(autouse=True)
def cleanup_api_resources() -> Generator[None]:
    """Automatically clean up any APIResource instances created during tests."""
    created_resources = []

    # Monkey patch APIResource.__init__ to track instances
    original_init = APIResource.__init__

    def tracking_init(self: APIResource, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)
        created_resources.append(self)

    APIResource.__init__ = tracking_init

    yield

    # Clean up all created resources
    for resource in created_resources:
        if hasattr(resource, "_conn_pool"):
            resource._conn_pool.close()

    # Restore original __init__
    APIResource.__init__ = original_init


@pytest.mark.usefixtures("mock_db_pool")
class TestTaggingIntegration:
    """Integration test cases for tag discovery and import."""

    @patch("requests.Session")
    def test_discover_tags_from_scryfall_parses_response(self, mock_session_class: MagicMock) -> None:
        """Test that tag discovery correctly parses Scryfall documentation."""
        # Mock the session and response
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = """
        <html>
            <body>
                <a href="/search?q=oracletag%3Aflying">Flying cards</a>
                <a href="/search?q=oracletag%3Atrample">Trample cards</a>
                <a href="/search?q=oracletag%3Ahaste">Haste cards</a>
                <a href="/search?q=oracletag%3Avigilance">Vigilance cards</a>
            </body>
        </html>
        """
        mock_session.get.return_value = mock_response

        api = APIResource()
        tags = api.discover_tags_from_scryfall()

        # Should extract tag names from the URLs
        expected_tags = ["flying", "haste", "trample", "vigilance"]
        assert sorted(tags) == sorted(expected_tags)

        # Should have made request to correct URL
        mock_session.get.assert_called_once_with(
            "https://scryfall.com/docs/tagger-tags",
            timeout=30,
        )

    @patch("api.api_resource.APIResource.discover_tags_from_scryfall")
    @patch("api.api_resource.APIResource.update_tagged_cards")
    @patch("api.api_resource.APIResource._get_all_tags")
    def test_discover_and_import_all_tags_with_mocked_data(
        self,
        mock_discover_tags: MagicMock,
        mock_update_tagged_cards: MagicMock,
        mock_get_all_tags: MagicMock,
    ) -> None:
        """Test bulk import with mocked data to avoid external requests."""
        tags = [
            "flying",
            "trample",
        ]
        mock_discover_tags.return_value = tags
        mock_get_all_tags.return_value = set(tags)

        api = APIResource()
        result = api.discover_and_import_all_tags(
            import_cards=True,
            import_hierarchy=False,
        )

        # Should have discovered tags
        assert result["success"] is True
        assert "card_taggings" in result
        assert "hierarchy" not in result

        # Should have called _get_all_tags to get existing tags
        mock_get_all_tags.assert_called_once()

        # Should have called update_tagged_cards for each tag
        assert mock_update_tagged_cards.call_count == 2
        mock_update_tagged_cards.assert_any_call(tag="flying")
        mock_update_tagged_cards.assert_any_call(tag="trample")

    def test_action_map_includes_new_endpoints(self) -> None:
        """Test that new endpoints are available in the action map."""
        api = APIResource()

        # Check that new endpoints are available
        assert "discover_and_import_all_tags" in api.action_map
        assert "update_tagged_cards" in api.action_map

        # Endpoints should be callable
        assert callable(api.action_map["discover_and_import_all_tags"])
        assert callable(api.action_map["update_tagged_cards"])

    @pytest.mark.skip(reason="Skipping live test for now")
    def test_get_tag_relationships_live(self) -> None:
        """Test that get_tag_relationships works with a live tag."""
        api = APIResource()
        res = api._get_tag_relationships(tag="cast-trigger")
        pairs = {(r["parent"]["slug"], r["child"]["slug"]) for r in res}
        assert pairs == {
            ("cast-trigger", "cast-trigger-other"),
            ("cast-trigger", "cast-trigger-self"),
            ("cast-trigger", "cast-trigger-you"),
            ("cast-trigger", "mana-gorger"),
            ("triggered-ability", "cast-trigger"),
        }
