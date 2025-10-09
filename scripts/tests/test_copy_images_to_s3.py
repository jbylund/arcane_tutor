"""Tests for the copy_images_to_s3 script."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests
from botocore.exceptions import ClientError

from scripts.copy_images_to_s3 import (
    build_scryfall_image_url,
    calculate_medium_width,
    check_s3_file_exists,
    download_image,
)


def test_calculate_medium_width() -> None:
    """Test medium width calculation using sqrt(220 * full_width)."""
    # For full_width = 745, medium should be sqrt(220 * 745) = sqrt(163900) ≈ 405
    result = calculate_medium_width(745)
    assert result == 404  # int(sqrt(163900))

    # Test with different width
    result = calculate_medium_width(500)
    assert result == 331  # int(sqrt(220 * 500))


def test_build_scryfall_image_url() -> None:
    """Test building Scryfall image URLs."""
    uuid = "a7af8350-9a51-437c-a55e-19f3e07acfa9"

    # Test PNG URL
    url = build_scryfall_image_url(uuid, "png")
    assert url == "https://cards.scryfall.io/png/front/a/7/a7af8350-9a51-437c-a55e-19f3e07acfa9.jpg"

    # Test large URL
    url = build_scryfall_image_url(uuid, "large")
    assert url == "https://cards.scryfall.io/large/front/a/7/a7af8350-9a51-437c-a55e-19f3e07acfa9.jpg"

    # Test normal URL
    url = build_scryfall_image_url(uuid, "normal")
    assert url == "https://cards.scryfall.io/normal/front/a/7/a7af8350-9a51-437c-a55e-19f3e07acfa9.jpg"


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


def test_check_s3_file_exists_true() -> None:
    """Test checking for existing S3 file."""
    mock_s3_client = MagicMock()
    mock_s3_client.head_object.return_value = {}

    result = check_s3_file_exists(mock_s3_client, "test-bucket", "test-key")

    assert result is True
    mock_s3_client.head_object.assert_called_once_with(Bucket="test-bucket", Key="test-key")


def test_check_s3_file_exists_false() -> None:
    """Test checking for non-existing S3 file."""
    mock_s3_client = MagicMock()
    error_response = {"Error": {"Code": "404"}}
    mock_s3_client.head_object.side_effect = ClientError(error_response, "HeadObject")

    result = check_s3_file_exists(mock_s3_client, "test-bucket", "test-key")

    assert result is False
    mock_s3_client.head_object.assert_called_once_with(Bucket="test-bucket", Key="test-key")


def test_check_s3_file_exists_error() -> None:
    """Test S3 check with different error."""
    mock_s3_client = MagicMock()
    error_response = {"Error": {"Code": "403"}}
    mock_s3_client.head_object.side_effect = ClientError(error_response, "HeadObject")

    result = check_s3_file_exists(mock_s3_client, "test-bucket", "test-key")

    # Should return False on errors
    assert result is False
