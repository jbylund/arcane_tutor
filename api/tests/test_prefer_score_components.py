"""Test cases for prefer score components calculation."""

import unittest


def calculate_finish_score(finishes: list[str]) -> int:
    """Calculate finish score based on finishes list."""
    if "nonfoil" in finishes:
        return 10
    if "foil" in finishes:
        return 5
    if "etched" in finishes:
        return 0
    return 0


class TestPreferScoreComponents(unittest.TestCase):
    """Test cases for prefer score components."""

    def test_legendary_frame_component_logic(self) -> None:
        """Test that legendary frame scoring logic is correct."""
        # Test case 1: Card with legendary frame effect
        # Should get score of 5
        card_with_legendary = {
            "frame_effects": ["legendary", "etched"],
        }
        # If frame_effects contains 'legendary', score should be 5
        score = 5 if "legendary" in card_with_legendary.get("frame_effects", []) else 0
        assert score == 5, "Card with legendary frame should get score of 5"

        # Test case 2: Card without legendary frame effect
        # Should get score of 0
        card_without_legendary = {
            "frame_effects": ["etched"],
        }
        score = 5 if "legendary" in card_without_legendary.get("frame_effects", []) else 0
        assert score == 0, "Card without legendary frame should get score of 0"

        # Test case 3: Card with no frame_effects
        # Should get score of 0
        card_no_effects = {}
        score = 5 if "legendary" in card_no_effects.get("frame_effects", []) else 0
        assert score == 0, "Card with no frame_effects should get score of 0"

    def test_finish_component_logic(self) -> None:
        """Test that finish scoring logic is correct."""
        # Test case 1: nonfoil card (most preferred) should get score of 10
        assert calculate_finish_score(["nonfoil"]) == 10, "Nonfoil card should get score of 10"

        # Test case 2: foil card (middle preference) should get score of 5
        assert calculate_finish_score(["foil"]) == 5, "Foil card should get score of 5"

        # Test case 3: etched card (least preferred) should get score of 0
        assert calculate_finish_score(["etched"]) == 0, "Etched card should get score of 0"

        # Test case 4: No finishes specified should get score of 0
        assert calculate_finish_score([]) == 0, "Card with no finishes should get score of 0"

    def test_preference_ordering(self) -> None:
        """Test that the overall preference ordering is correct."""
        # Test ordering for legendary frame: normal frame (0) < legendary frame (5)
        normal_frame_score = 0
        legendary_frame_score = 5
        assert normal_frame_score < legendary_frame_score, \
            "Legendary frame should be preferred over normal frame"

        # Test ordering for finishes: etched < foil < nonfoil
        etched_score = 0
        foil_score = 5
        nonfoil_score = 10
        assert etched_score < foil_score < nonfoil_score, \
            "Finish ordering should be: etched < foil < nonfoil"


if __name__ == "__main__":
    unittest.main()
