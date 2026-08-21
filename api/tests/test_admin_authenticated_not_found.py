"""An authenticated caller gets the full 404 route listing; an unauthenticated one doesn't.

Hiding admin routes from the public 404 listing (test_admin_mount.py) protects against a caller who
has not proven they hold the shared secret. It buys nothing once they have, so an authenticated
request gets the full listing instead -- and since the listing shown now depends on who's asking,
these also cover the caching hazard that creates: CachingMiddleware still caches 4xx responses (an
admin-authenticated 404 is itself a good candidate -- typo'd admin URLs repeat), but an
admin-authenticated 4xx and an anonymous 4xx to the same path go in separate cache slots, so
neither leaks into or gets masked by the other. 2xx/3xx traffic is untouched: nothing in this app
varies a successful response by auth state, so it keeps sharing the ordinary slot for everyone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import falcon
import falcon.testing
import pytest

from api.admin_resource import ADMIN_MOUNT_PREFIX
from api.api_resource import APIResource
from api.middlewares.admin_auth_middleware import AdminAuthMiddleware
from api.middlewares.caching_middleware import CachingMiddleware
from api.settings import settings
from api.tests.support import mock_app_context

if TYPE_CHECKING:
    from collections.abc import Generator

TEST_PASSWORD = "correct-horse-battery-staple"

_AUTH_HEADERS = {"Authorization": "Basic d2hvZXZlcjpjb3JyZWN0LWhvcnNlLWJhdHRlcnktc3RhcGxl"}  # whoever:TEST_PASSWORD


@pytest.fixture(name="admin_password")
def admin_password_fixture() -> str:
    """Set ADMIN_PASSWORD for the duration of one test, then restore it."""
    original = settings.admin_password
    settings.admin_password = TEST_PASSWORD
    yield TEST_PASSWORD
    settings.admin_password = original


@pytest.fixture(name="resource")
def resource_fixture() -> APIResource:
    app_context = mock_app_context(reader_pool=MagicMock(), writer_pool=MagicMock())
    return APIResource(app_context=app_context)


def _client(resource: APIResource, *, cache: bool = False) -> falcon.testing.TestClient:
    middleware = [AdminAuthMiddleware()]
    if cache:
        middleware.append(CachingMiddleware())
    app = falcon.App(middleware=middleware)
    app.add_sink(resource._handle, prefix="/")
    return falcon.testing.TestClient(app)


class TestAuthenticatedNotFoundListing:
    """What an unknown path returns depends on whether the caller already authenticated."""

    def test_unauthenticated_404_omits_admin_routes(self, resource: APIResource, admin_password: str) -> None:
        del admin_password
        result = _client(resource).simulate_get("/totally/bogus")
        assert result.status == falcon.HTTP_404
        assert "setup_schema" not in result.json["description"]["routes"]

    def test_authenticated_404_includes_admin_routes(self, resource: APIResource, admin_password: str) -> None:
        del admin_password
        result = _client(resource).simulate_get("/totally/bogus", headers=_AUTH_HEADERS)
        assert result.status == falcon.HTTP_404
        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in result.json["description"]["routes"]

    def test_wrong_credential_still_gets_the_public_listing(self, resource: APIResource, admin_password: str) -> None:
        del admin_password
        wrong = {"Authorization": "Basic d2hvZXZlcjp3cm9uZw=="}  # whoever:wrong
        result = _client(resource).simulate_get("/totally/bogus", headers=wrong)
        assert result.status == falcon.HTTP_404
        assert "setup_schema" not in result.json["description"]["routes"]

    def test_authenticated_404_under_the_mount_also_gets_the_full_listing(
        self, resource: APIResource, admin_password: str
    ) -> None:
        del admin_password
        result = _client(resource).simulate_get(f"/{ADMIN_MOUNT_PREFIX}/nope", headers=_AUTH_HEADERS)
        assert result.status == falcon.HTTP_404
        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in result.json["description"]["routes"]


class TestNotFoundCachingIsPartitionedByAuth:
    """4xx responses are still cached -- just never across the auth boundary."""

    @pytest.fixture(autouse=True)
    def _enable_cache(self) -> Generator[None]:
        with patch("api.middlewares.caching_middleware.settings") as mock_settings:
            mock_settings.enable_cache = True
            yield

    def test_repeated_anonymous_404_is_a_cache_hit(self, resource: APIResource, admin_password: str) -> None:
        del admin_password
        client = _client(resource, cache=True)
        first = client.simulate_get("/totally/bogus")
        second = client.simulate_get("/totally/bogus")

        assert first.headers.get("X-Cache") == "miss"
        assert second.headers.get("X-Cache") == "hit"
        assert "setup_schema" not in second.json["description"]["routes"]

    def test_repeated_authenticated_404_with_no_prior_anonymous_visit_recomputes_each_time(
        self, resource: APIResource, admin_password: str
    ) -> None:
        # The lookup only ever consults the admin-only slot when the ordinary slot already holds
        # *something* and it's a 4xx (see CachingMiddleware.process_request) -- an admin 404 is
        # never written to the ordinary slot, so with no anonymous caller in the picture the
        # ordinary slot stays empty and every repeat looks like a fresh miss. Content stays
        # correct either way; this documents the accepted cost (a cheap recompute, not a query).
        del admin_password
        client = _client(resource, cache=True)
        first = client.simulate_get("/totally/bogus", headers=_AUTH_HEADERS)
        second = client.simulate_get("/totally/bogus", headers=_AUTH_HEADERS)

        assert first.headers.get("X-Cache") == "miss"
        assert second.headers.get("X-Cache") == "miss"
        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in second.json["description"]["routes"]

    def test_authenticated_404_hits_the_admin_slot_once_an_anonymous_visit_populated_the_ordinary_one(
        self, resource: APIResource, admin_password: str
    ) -> None:
        # The one scenario the admin-only slot's second lookup exists for: an anonymous caller
        # cached a plain 404 at the ordinary key first, so the *next* authenticated request finds
        # something there, sees it's a 4xx, and checks (and populates, then hits) the admin slot
        # instead of trusting it.
        del admin_password
        client = _client(resource, cache=True)
        client.simulate_get("/totally/bogus")  # anonymous: populates the ordinary slot
        first_authed = client.simulate_get("/totally/bogus", headers=_AUTH_HEADERS)
        second_authed = client.simulate_get("/totally/bogus", headers=_AUTH_HEADERS)

        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in first_authed.json["description"]["routes"]
        assert second_authed.headers.get("X-Cache") == "hit"
        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in second_authed.json["description"]["routes"]

    def test_authenticated_404_is_not_served_to_a_later_unauthenticated_caller(
        self, resource: APIResource, admin_password: str
    ) -> None:
        del admin_password
        client = _client(resource, cache=True)
        authed = client.simulate_get("/totally/bogus", headers=_AUTH_HEADERS)
        unauthed = client.simulate_get("/totally/bogus")

        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in authed.json["description"]["routes"]
        assert "setup_schema" not in unauthed.json["description"]["routes"]
        assert unauthed.headers.get("X-Cache") != "hit"

    def test_unauthenticated_404_does_not_mask_a_later_authenticated_caller(
        self, resource: APIResource, admin_password: str
    ) -> None:
        del admin_password
        client = _client(resource, cache=True)
        unauthed = client.simulate_get("/totally/bogus")
        authed = client.simulate_get("/totally/bogus", headers=_AUTH_HEADERS)

        assert "setup_schema" not in unauthed.json["description"]["routes"]
        assert f"{ADMIN_MOUNT_PREFIX}/setup_schema" in authed.json["description"]["routes"]
        assert authed.headers.get("X-Cache") != "hit"

    def test_public_2xx_cache_is_shared_regardless_of_auth(self, resource: APIResource, admin_password: str) -> None:
        # The ordinary slot stays shared for successful responses: nothing in this app varies a
        # 2xx by auth state, so partitioning it would only fragment the cache real traffic uses.
        del admin_password
        client = _client(resource, cache=True)
        anon = client.simulate_get("/robots.txt")
        authed = client.simulate_get("/robots.txt", headers=_AUTH_HEADERS)

        assert anon.headers.get("X-Cache") == "miss"
        assert authed.headers.get("X-Cache") == "hit"
