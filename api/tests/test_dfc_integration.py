"""Integration tests for double-faced card (DFC) support."""

from __future__ import annotations

import pytest

from api.api_resource import APIResource


class TestDoubleFacedCardsIntegration:
    """Integration tests for double-faced card search functionality."""

    @pytest.fixture
    def api_resource(self: TestDoubleFacedCardsIntegration) -> APIResource:
        """Create an APIResource instance for testing."""
        return APIResource()

    def test_is_dfc_tag_added_to_double_faced_cards(self: TestDoubleFacedCardsIntegration, api_resource: APIResource) -> None:
        """Test that is:dfc tag is properly added to double-faced cards."""
        # Create a test double-faced card
        test_dfc = {
            "id": "test-dfc-id-001",
            "name": "Test DFC // Test Back",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Human // Creature — Horror",
            "colors": [],
            "color_identity": ["U"],
            "keywords": [],
            "card_faces": [
                {
                    "name": "Test DFC",
                    "type_line": "Creature — Human",
                    "colors": ["U"],
                    "keywords": [],
                    "mana_cost": "{1}{U}",
                    "oracle_text": "Transform ability",
                    "power": "1",
                    "toughness": "1",
                },
                {
                    "name": "Test Back",
                    "type_line": "Creature — Horror",
                    "colors": ["U"],
                    "keywords": ["Flying"],
                    "oracle_text": "Flying",
                    "power": "2",
                    "toughness": "2",
                },
            ],
            "prices": {"usd": "1.00"},
            "set": "test",
            "rarity": "common",
            "collector_number": "1",
            "image_uris": {
                "normal": "https://cards.scryfall.io/normal/front/t/e/test-dfc-id-001.jpg",
            },
        }

        result = api_resource._preprocess_card(test_dfc)

        assert result is not None
        assert result["card_is_tags"].get("dfc") is True

    def test_modal_dfc_has_multiple_types(self: TestDoubleFacedCardsIntegration, api_resource: APIResource) -> None:
        """Test that modal DFCs union types from both faces (e.g., Creature and Sorcery)."""
        # Create a modal DFC like Augmenter Pugilist // Echoing Equation
        modal_dfc = {
            "id": "test-modal-dfc-001",
            "name": "Test Creature // Test Sorcery",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Troll // Sorcery",
            "colors": [],
            "color_identity": ["G", "U"],
            "keywords": [],
            "card_faces": [
                {
                    "name": "Test Creature",
                    "type_line": "Creature — Troll",
                    "colors": ["G"],
                    "keywords": [],
                    "mana_cost": "{2}{G}",
                    "oracle_text": "Creature ability",
                    "power": "3",
                    "toughness": "3",
                },
                {
                    "name": "Test Sorcery",
                    "type_line": "Sorcery",
                    "colors": ["U"],
                    "keywords": [],
                    "mana_cost": "{3}{U}",
                    "oracle_text": "Sorcery effect",
                },
            ],
            "prices": {"usd": "1.00"},
            "set": "test",
            "rarity": "rare",
            "collector_number": "2",
            "cmc": 3,
            "image_uris": {
                "normal": "https://cards.scryfall.io/normal/front/t/e/test-modal-dfc-001.jpg",
            },
        }

        result = api_resource._preprocess_card(modal_dfc)

        assert result is not None
        # Should have both types from different faces
        assert "Creature" in result["card_types"]
        assert "Sorcery" in result["card_types"]
        # Should have colors from both faces
        assert result["card_colors"] == {"G": True, "U": True}
        # Should have is:dfc tag
        assert result["card_is_tags"].get("dfc") is True

    def test_dfc_unions_subtypes_from_both_faces(self: TestDoubleFacedCardsIntegration, api_resource: APIResource) -> None:
        """Test that DFCs union subtypes from both faces (e.g., Human and Insect)."""
        dfc_with_different_subtypes = {
            "id": "test-dfc-subtypes-001",
            "name": "Test Human // Test Insect",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Human // Creature — Insect",
            "colors": [],
            "color_identity": ["U"],
            "keywords": [],
            "card_faces": [
                {
                    "name": "Test Human",
                    "type_line": "Creature — Human Wizard",
                    "colors": ["U"],
                    "keywords": [],
                    "mana_cost": "{U}",
                    "oracle_text": "Ability",
                },
                {
                    "name": "Test Insect",
                    "type_line": "Creature — Human Insect",
                    "colors": ["U"],
                    "keywords": ["Flying"],
                    "oracle_text": "Flying",
                },
            ],
            "prices": {"usd": "1.00"},
            "set": "test",
            "rarity": "common",
            "collector_number": "3",
            "image_uris": {
                "normal": "https://cards.scryfall.io/normal/front/t/e/test-dfc-subtypes-001.jpg",
            },
        }

        result = api_resource._preprocess_card(dfc_with_different_subtypes)

        assert result is not None
        # Should have all unique subtypes from all faces
        assert "Human" in result["card_subtypes"]
        assert "Wizard" in result["card_subtypes"]
        assert "Insect" in result["card_subtypes"]

    def test_dfc_unions_keywords_from_both_faces(self: TestDoubleFacedCardsIntegration, api_resource: APIResource) -> None:
        """Test that DFCs union keywords from all faces."""
        dfc_with_keywords = {
            "id": "test-dfc-keywords-001",
            "name": "Test // Test Back",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Horror // Creature — Horror",
            "colors": [],
            "color_identity": ["B"],
            "keywords": [],
            "card_faces": [
                {
                    "name": "Test",
                    "type_line": "Creature — Horror",
                    "colors": ["B"],
                    "keywords": ["Deathtouch"],
                    "mana_cost": "{1}{B}",
                    "oracle_text": "Deathtouch",
                },
                {
                    "name": "Test Back",
                    "type_line": "Creature — Horror",
                    "colors": ["B"],
                    "keywords": ["Flying", "Lifelink"],
                    "oracle_text": "Flying, Lifelink",
                },
            ],
            "prices": {"usd": "1.00"},
            "set": "test",
            "rarity": "uncommon",
            "collector_number": "4",
            "image_uris": {
                "normal": "https://cards.scryfall.io/normal/front/t/e/test-dfc-keywords-001.jpg",
            },
        }

        result = api_resource._preprocess_card(dfc_with_keywords)

        assert result is not None
        # Should have all keywords from all faces
        assert result["card_keywords"] == {"Deathtouch": True, "Flying": True, "Lifelink": True}

    def test_dfc_uses_front_face_power_toughness(self: TestDoubleFacedCardsIntegration, api_resource: APIResource) -> None:
        """Test that DFCs use the front face power/toughness."""
        dfc = {
            "id": "test-dfc-pt-001",
            "name": "Small // Large",
            "legalities": {"standard": "legal"},
            "games": ["paper"],
            "type_line": "Creature — Beast // Creature — Beast",
            "colors": [],
            "color_identity": ["G"],
            "keywords": [],
            "card_faces": [
                {
                    "name": "Small",
                    "type_line": "Creature — Beast",
                    "colors": ["G"],
                    "keywords": [],
                    "mana_cost": "{1}{G}",
                    "oracle_text": "Transform",
                    "power": "2",
                    "toughness": "2",
                },
                {
                    "name": "Large",
                    "type_line": "Creature — Beast",
                    "colors": ["G"],
                    "keywords": [],
                    "oracle_text": "Bigger",
                    "power": "5",
                    "toughness": "5",
                },
            ],
            "prices": {"usd": "1.00"},
            "set": "test",
            "rarity": "common",
            "collector_number": "5",
            "image_uris": {
                "normal": "https://cards.scryfall.io/normal/front/t/e/test-dfc-pt-001.jpg",
            },
        }

        result = api_resource._preprocess_card(dfc)

        assert result is not None
        # Should use front face stats
        assert result["creature_power"] == 2
        assert result["creature_toughness"] == 2
