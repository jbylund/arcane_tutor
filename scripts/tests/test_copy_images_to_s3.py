"""Tests for the copy_images_to_s3 script."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from scripts.copy_images_to_s3 import (
    Image,
    download_image,
    fetch_cards_from_db,
)


def test_image_class_equality() -> None:
    """Test Image class equality."""
    img1 = Image(set_code="iko", collector_number="123", face_idx="1", size="280")
    img2 = Image(set_code="iko", collector_number="123", face_idx="1", size="280")
    img3 = Image(set_code="iko", collector_number="123", face_idx="2", size="280")
    img4 = Image(set_code="thb", collector_number="123", face_idx="1", size="280")

    # Same images should be equal
    assert img1 == img2

    # Different face_idx should not be equal
    assert img1 != img3

    # Different set_code should not be equal
    assert img1 != img4


def test_image_class_hash() -> None:
    """Test Image class hashing for use in sets."""
    img1 = Image(set_code="iko", collector_number="123", face_idx="1", size="280")
    img2 = Image(set_code="iko", collector_number="123", face_idx="1", size="280")
    img3 = Image(set_code="iko", collector_number="123", face_idx="2", size="280")

    # Same images should have same hash
    assert hash(img1) == hash(img2)

    # Can be used in sets
    image_set = {img1, img2, img3}
    assert len(image_set) == 2  # img1 and img2 are duplicates

    # Can check membership
    assert img1 in image_set
    assert img2 in image_set
    assert img3 in image_set


def test_image_class_immutability() -> None:
    """Test that Image class is immutable."""
    img = Image(set_code="iko", collector_number="123", face_idx="1", size="280")

    # Attempting to modify should raise an error
    with pytest.raises(AttributeError):
        img.set_code = "thb"  # type: ignore


def test_download_image_success() -> None:
    """Test successful image download."""
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "test.png"

        # Mock requests.get
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_content = Mock(return_value=[b"chunk1", b"chunk2"])

        with patch("scripts.copy_images_to_s3.requests.get", return_value=mock_response):
            result = download_image("https://example.com/image.png", output_path)

        assert result is True
        assert output_path.exists()

        # Check content was written
        content = output_path.read_bytes()
        assert content == b"chunk1chunk2"


def test_download_image_failure() -> None:
    """Test failed image download."""
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "test.png"

        # Mock requests.get to raise a RequestException
        with patch("scripts.copy_images_to_s3.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Network error")
            result = download_image("https://example.com/image.png", output_path)

        assert result is False
        assert not output_path.exists()


def test_fetch_cards_from_db() -> None:
    """Test fetching cards from database."""
    # Mock connection and cursor
    mock_conn = Mock()
    mock_cursor = Mock()
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

    # Mock query results
    mock_cursor.fetchall.return_value = [
        {
            "card_set_code": "iko",
            "collector_number": "123",
            "image_location_uuid": "a7af8350-9a51-437c-a55e-19f3e07acfa9",
        },
        {
            "card_set_code": "thb",
            "collector_number": "42a",
            "image_location_uuid": "b8bf9461-0b62-548d-b66f-20g4f08bdbga",
        },
    ]

    cards = fetch_cards_from_db(mock_conn, limit=10, set_code="iko")

    assert len(cards) == 2
    assert cards[0]["card_set_code"] == "iko"
    assert cards[0]["collector_number"] == "123"
    assert cards[1]["card_set_code"] == "thb"
