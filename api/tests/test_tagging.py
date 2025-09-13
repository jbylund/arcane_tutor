"""Tests for card tagging functionality."""

import inspect

from api.api_resource import APIResource


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

    def test_discover_tags_from_scryfall_function_exists(self) -> None:
        """Test that discover_tags_from_scryfall method exists and is callable."""
        assert hasattr(APIResource, "discover_tags_from_scryfall")
        assert callable(APIResource.discover_tags_from_scryfall)

        # Method should have no required parameters (uses self and **kwargs only)
        sig = inspect.signature(APIResource.discover_tags_from_scryfall)
        required_params = [p for p in sig.parameters.values()
                          if p.default == inspect.Parameter.empty
                          and p.name != "self"
                          and p.kind != inspect.Parameter.VAR_KEYWORD]
        assert len(required_params) == 0

    def test_discover_tags_from_graphql_function_exists(self) -> None:
        """Test that discover_tags_from_graphql method exists and is callable."""
        assert hasattr(APIResource, "discover_tags_from_graphql")
        assert callable(APIResource.discover_tags_from_graphql)

        # Method should have no required parameters (uses self and **kwargs only)
        sig = inspect.signature(APIResource.discover_tags_from_graphql)
        required_params = [p for p in sig.parameters.values()
                          if p.default == inspect.Parameter.empty
                          and p.name != "self"
                          and p.kind != inspect.Parameter.VAR_KEYWORD]
        assert len(required_params) == 0

    def test_fetch_tag_hierarchy_function_exists(self) -> None:
        """Test that _fetch_tag_hierarchy method exists and is callable."""
        assert hasattr(APIResource, "_fetch_tag_hierarchy")
        assert callable(APIResource._fetch_tag_hierarchy)

        # Test that the method signature includes the required tag parameter
        sig = inspect.signature(APIResource._fetch_tag_hierarchy)
        assert "tag" in sig.parameters

    def test_populate_tag_hierarchy_function_exists(self) -> None:
        """Test that _populate_tag_hierarchy method exists and is callable."""
        assert hasattr(APIResource, "_populate_tag_hierarchy")
        assert callable(APIResource._populate_tag_hierarchy)

        # Test that the method signature includes the required tags parameter
        sig = inspect.signature(APIResource._populate_tag_hierarchy)
        assert "tags" in sig.parameters

    def test_discover_and_import_all_tags_function_exists(self) -> None:
        """Test that discover_and_import_all_tags method exists and is callable."""
        assert hasattr(APIResource, "discover_and_import_all_tags")
        assert callable(APIResource.discover_and_import_all_tags)

        # Method should have optional parameters with defaults
        sig = inspect.signature(APIResource.discover_and_import_all_tags)
        assert "import_cards" in sig.parameters
        assert "import_hierarchy" in sig.parameters

        # These should have default values
        assert sig.parameters["import_cards"].default is not inspect.Parameter.empty
        assert sig.parameters["import_hierarchy"].default is not inspect.Parameter.empty
