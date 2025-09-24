"""Tests for screenshot functionality."""

import pytest
import requests
import requests_mock

from api.api_resource import APIResource


class TestScreenshotFunctionality:
    """Test class for screenshot-related API methods."""

    @pytest.fixture
    def api_resource(self) -> APIResource:
        """Create an APIResource instance for testing."""
        return APIResource()

    def test_take_screenshot_with_url(self, api_resource: APIResource) -> None:
        """Test taking screenshot with provided URL."""
        with requests_mock.Mocker() as m:
            # Mock a successful screenshot response
            m.get(
                "https://api.screenshotmachine.com/",
                content=b"fake_png_data",
                headers={"content-type": "image/png"},
            )

            result = api_resource.take_screenshot(url="https://example.com")

            assert result["status"] == "success"
            assert result["target_url"] == "https://example.com"
            assert result["screenshot_size_bytes"] == len(b"fake_png_data")
            assert result["content_type"] == "image/png"

    def test_take_screenshot_api_error(self, api_resource: APIResource) -> None:
        """Test handling of screenshot API errors."""
        with requests_mock.Mocker() as m:
            # Mock an error response from screenshot API
            m.get(
                "https://api.screenshotmachine.com/",
                text="Invalid API key",
                headers={"content-type": "text/plain"},
            )

            result = api_resource.take_screenshot(url="https://example.com")

            assert result["status"] == "error"
            assert "Screenshot service error" in result["message"]
            assert "Invalid API key" in result["message"]

    def test_take_screenshot_without_url_ip_failure(self, api_resource: APIResource) -> None:
        """Test screenshot without URL when IP retrieval fails."""
        with requests_mock.Mocker() as m:
            # Mock IP retrieval failure
            m.get("https://api.ipify.org/?format=json", exc=requests.exceptions.ConnectTimeout)

            result = api_resource.take_screenshot()

            assert result["status"] == "error"
            assert "Could not determine public IP for screenshot" in result["message"]

    def test_take_screenshot_without_url_success(self, api_resource: APIResource) -> None:
        """Test screenshot without URL using public IP."""
        with requests_mock.Mocker() as m:
            # Mock successful IP retrieval
            mock_ip_response = {"ip": "203.0.113.1"}
            m.get("https://api.ipify.org/?format=json", json=mock_ip_response)

            # Mock successful screenshot response
            m.get(
                "https://api.screenshotmachine.com/",
                content=b"fake_png_data",
                headers={"content-type": "image/png"},
            )

            result = api_resource.take_screenshot()

            assert result["status"] == "success"
            assert "203.0.113.1" in result["target_url"]
            assert "t%253Abeast" in result["target_url"]  # URL encoded query
            assert result["screenshot_size_bytes"] == len(b"fake_png_data")

    def test_take_screenshot_network_error(self, api_resource: APIResource) -> None:
        """Test handling of network errors during screenshot."""
        with requests_mock.Mocker() as m:
            # Mock network error for screenshot API
            m.get(
                "https://api.screenshotmachine.com/",
                exc=requests.exceptions.ConnectTimeout,
            )

            result = api_resource.take_screenshot(url="https://example.com")

            assert result["status"] == "error"
            assert "Failed to take screenshot" in result["message"]
            assert result["target_url"] == "https://example.com"

    def test_take_screenshot_custom_parameters(self, api_resource: APIResource) -> None:
        """Test screenshot with custom query parameters."""
        with requests_mock.Mocker() as m:
            # Mock successful IP retrieval
            mock_ip_response = {"ip": "203.0.113.1"}
            m.get("https://api.ipify.org/?format=json", json=mock_ip_response)

            # Mock successful screenshot response
            m.get(
                "https://api.screenshotmachine.com/",
                content=b"fake_png_data",
                headers={"content-type": "image/png"},
            )

            result = api_resource.take_screenshot(
                host="3000",
                query="c:blue",
                orderby="name",
                direction="desc",
            )

            assert result["status"] == "success"
            target_url = result["target_url"]
            assert ":3000" in target_url
            assert "c%253Ablue" in target_url  # URL encoded query
            assert "orderby%3Dname" in target_url
            assert "direction%3Ddesc" in target_url
