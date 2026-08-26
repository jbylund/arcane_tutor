"""Tests for /search complexity middleware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import falcon
import falcon.testing
import pytest

from api.api_resource import APIResource
from api.middlewares.caching_middleware import CachingMiddleware
from api.middlewares.search_budget_middleware import SearchBudgetMiddleware
from api.parsing.query_budget import MAX_QUERY_UTF8_BYTES, QUERY_TOO_LONG_MESSAGE
from api.tests.support import mock_app_context


def _oversized_query() -> str:
    return "a" * (MAX_QUERY_UTF8_BYTES + 1)


@pytest.fixture(name="resource")
def resource_fixture() -> APIResource:
    return APIResource(app_context=mock_app_context(reader_pool=MagicMock(), writer_pool=MagicMock()))


def _client(resource: APIResource, *, cache: dict | None = None) -> falcon.testing.TestClient:
    app = falcon.App(
        middleware=[
            SearchBudgetMiddleware(),
            CachingMiddleware(cache=cache if cache is not None else {}),
        ],
    )
    app.add_sink(resource._handle, prefix="/")
    return falcon.testing.TestClient(app)


class TestSearchBudgetMiddleware:
    def test_rejects_oversized_q_before_handler(self, resource: APIResource) -> None:
        client = _client(resource)
        result = client.simulate_get("/search", params={"q": _oversized_query()})
        assert result.status == falcon.HTTP_400
        assert result.json["description"] == QUERY_TOO_LONG_MESSAGE

    def test_rejects_oversized_unused_query_alias(self, resource: APIResource) -> None:
        client = _client(resource)
        result = client.simulate_get(
            "/search",
            params={"q": "bolt", "query": _oversized_query()},
        )
        assert result.status == falcon.HTTP_400
        assert result.json["description"] == QUERY_TOO_LONG_MESSAGE

    def test_accepts_query_at_byte_limit(self, resource: APIResource) -> None:
        with patch.object(APIResource, "_search", return_value={"cards": [], "total_cards": 0, "query": "x"}):
            client = _client(resource)
            result = client.simulate_get("/search", params={"q": "a" * MAX_QUERY_UTF8_BYTES})
        assert result.status == falcon.HTTP_200

    def test_budget_rejection_never_reaches_search_handler(self, resource: APIResource) -> None:
        with patch.object(resource, "_search") as mock_search:
            client = _client(resource)
            result = client.simulate_get("/search", params={"q": _oversized_query()})
        assert result.status == falcon.HTTP_400
        mock_search.assert_not_called()
