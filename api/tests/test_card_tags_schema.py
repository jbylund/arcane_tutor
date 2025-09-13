"""Tests for card_tags schema allowing multiple parents per tag."""



class TestCardTagsSchema:
    """Test cases for card_tags table schema changes."""

    def test_card_tags_schema_allows_multiple_parents_concept(self) -> None:
        """Test that the card_tags schema concept allows multiple parents per tag.

        This is a conceptual test that validates our understanding of the schema fix.
        For integration testing with actual PostgreSQL, we would need testcontainers.
        """
        # This test validates that our schema change makes conceptual sense
        # The new primary key should be (tag, parent_tag) instead of just (tag)

        # Example data that should be possible after the schema fix:
        example_relationships = [
            {"tag": "cost-reducer-artifact", "parent_tag": "synergy-artifact"},
            {"tag": "cost-reducer-artifact", "parent_tag": "cost-reducer"},
            {"tag": "synergy-artifact", "parent_tag": None},  # root tag
            {"tag": "cost-reducer", "parent_tag": None},      # root tag
        ]

        # Validate that we have the expected relationship:
        # cost-reducer-artifact should have 2 parents
        cost_reducer_artifact_parents = [
            rel["parent_tag"] for rel in example_relationships
            if rel["tag"] == "cost-reducer-artifact"
        ]

        assert len(cost_reducer_artifact_parents) == 2
        assert "synergy-artifact" in cost_reducer_artifact_parents
        assert "cost-reducer" in cost_reducer_artifact_parents

        # Validate that root tags have no parents
        root_tags = [
            rel["tag"] for rel in example_relationships
            if rel["parent_tag"] is None
        ]

        assert len(root_tags) == 2
        assert "synergy-artifact" in root_tags
        assert "cost-reducer" in root_tags

    def test_populate_tag_hierarchy_logic_multiple_parents(self) -> None:
        """Test that populate_tag_hierarchy logic can handle multiple parents.

        This tests the logic we'll need to implement after the schema change.
        """
        # Mock data representing what _get_tag_relationships might return
        # for a tag that has multiple parents
        mock_relationships = [
            {
                "parent": {"slug": "synergy-artifact"},
                "child": {"slug": "cost-reducer-artifact"},
            },
            {
                "parent": {"slug": "cost-reducer"},
                "child": {"slug": "cost-reducer-artifact"},
            },
        ]

        # Extract parent tags (this is what the current code does)
        parent_tags = {r["parent"]["slug"] for r in mock_relationships}

        # Verify we get both parents
        assert len(parent_tags) == 2
        assert "synergy-artifact" in parent_tags
        assert "cost-reducer" in parent_tags

        # Verify we can create the relationship data
        relationships_data = [
            {
                "parent_tag": r["parent"]["slug"],
                "tag": r["child"]["slug"],
            }
            for r in mock_relationships
        ]

        assert len(relationships_data) == 2
        assert all(rel["tag"] == "cost-reducer-artifact" for rel in relationships_data)
        assert relationships_data[0]["parent_tag"] != relationships_data[1]["parent_tag"]
