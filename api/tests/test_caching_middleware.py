"""Tests for status_is_cacheable and the one exception it carves out of the 4xx rule.

404-is-never-cached is covered end-to-end in test_admin_authenticated_not_found.py, alongside the
auth-listing feature that motivated it. This file covers the function in isolation, plus the one
behavior that isn't exercised elsewhere: a 400 (a query parameter that fails type coercion) is
cacheable, unlike every other 4xx.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import falcon
import falcon.testing
import pytest

from api.api_resource import APIResource
from api.middlewares.caching_middleware import CachingMiddleware, status_is_cacheable
from api.tests.support import mock_app_context


@pytest.mark.parametrize(
    argnames=["status", "expected"],
    argvalues=[
        ("200 OK", True),
        ("304 Not Modified", True),
        ("400 Bad Request", True),
        ("401 Unauthorized", False),
        ("403 Forbidden", False),
        ("404 Not Found", False),
        ("405 Method Not Allowed", False),
        ("429 Too Many Requests", False),
        ("500 Internal Server Error", False),
        ("503 Service Unavailable", False),
    ],
)
def test_status_is_cacheable(status: str, expected: bool) -> None:
    assert status_is_cacheable(status) is expected


class TestBadParamCachingEndToEnd:
    """A 400 from a query parameter that fails type coercion is deterministic and safe to cache."""

    @pytest.fixture(name="resource")
    def resource_fixture(self) -> APIResource:
        return APIResource(app_context=mock_app_context(reader_pool=MagicMock(), writer_pool=MagicMock()))

    def _client(self, resource: APIResource) -> falcon.testing.TestClient:
        app = falcon.App(middleware=[CachingMiddleware()])
        app.add_sink(resource._handle, prefix="/")
        return falcon.testing.TestClient(app)

    def test_repeated_bad_param_400_is_a_cache_hit(self, resource: APIResource) -> None:
        with patch("api.middlewares.caching_middleware.settings") as mock_settings:
            mock_settings.enable_cache = True
            client = self._client(resource)
            first = client.simulate_get("/search", params={"limit": "notanumber"})
            second = client.simulate_get("/search", params={"limit": "notanumber"})

        assert first.status == falcon.HTTP_400
        assert first.headers.get("X-Cache") == "miss"
        assert second.status == falcon.HTTP_400
        assert second.headers.get("X-Cache") == "hit"
