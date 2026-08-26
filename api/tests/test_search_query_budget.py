"""Integration tests for query budget enforcement in _search."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import falcon
import pytest

from api.parsing.query_budget import MAX_GROUP_DEPTH, MAX_QUERY_UTF8_BYTES, QUERY_TOO_LONG_MESSAGE

if TYPE_CHECKING:
    from api.api_resource import APIResource


@pytest.mark.usefixtures("engine_enabled")
class TestSearchQueryBudget:
    @pytest.fixture(autouse=True)
    def _api(self, stub_api_resource: APIResource) -> None:
        self.api_resource = stub_api_resource
        self.api_resource.app_context.engine = MagicMock()
        self.api_resource.app_context.engine.size.return_value = 87

    def test_rejects_oversized_query_without_engine_or_sql(self) -> None:
        query = "a" * (MAX_QUERY_UTF8_BYTES + 1)
        with (
            patch.object(self.api_resource, "_search_engine") as mock_engine,
            patch.object(self.api_resource, "_search_sql") as mock_sql,
            pytest.raises(falcon.HTTPBadRequest) as exc_info,
        ):
            self.api_resource._search(query=query, limit=10)
        assert exc_info.value.description == QUERY_TOO_LONG_MESSAGE
        mock_engine.assert_not_called()
        mock_sql.assert_not_called()

    def test_rejects_excessive_nesting_without_engine_or_sql(self) -> None:
        query = "(" * (MAX_GROUP_DEPTH + 1) + "name:a" + ")" * (MAX_GROUP_DEPTH + 1)
        with (
            patch.object(self.api_resource, "_search_engine") as mock_engine,
            patch.object(self.api_resource, "_search_sql") as mock_sql,
            pytest.raises(falcon.HTTPBadRequest) as exc_info,
        ):
            self.api_resource._search(query=query, limit=10)
        assert exc_info.value.description == QUERY_TOO_LONG_MESSAGE
        mock_engine.assert_not_called()
        mock_sql.assert_not_called()

    def test_stable_message_does_not_echo_hostile_query(self) -> None:
        hostile = "a" * (MAX_QUERY_UTF8_BYTES + 1)
        with pytest.raises(falcon.HTTPBadRequest) as exc_info:
            self.api_resource._search(query=hostile, limit=10)
        assert hostile not in exc_info.value.description
