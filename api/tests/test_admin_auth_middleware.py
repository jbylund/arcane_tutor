"""Tests for the Basic Auth gate on the admin mount.

These build a bare Falcon app around a trivial sink rather than the real APIResource, so a
failure here is never masked or caused by anything an admin handler does -- the whole point of
gating at the mount is that no handler-level behavior can affect whether the check runs.
"""

from __future__ import annotations

import base64

import falcon
import falcon.testing
import pytest

from api.admin_resource import ADMIN_MOUNT_PREFIX
from api.middlewares.admin_auth_middleware import AdminAuthMiddleware, _is_admin_path, _supplied_password
from api.settings import settings

TEST_PASSWORD = "correct-horse-battery-staple"


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


@pytest.fixture(name="admin_password")
def admin_password_fixture() -> str:
    """Set ADMIN_PASSWORD for the duration of one test, then restore it."""
    original = settings.admin_password
    settings.admin_password = TEST_PASSWORD
    yield TEST_PASSWORD
    settings.admin_password = original


@pytest.fixture(name="client")
def client_fixture(admin_password: str) -> falcon.testing.TestClient:
    """A TestClient wrapping only AdminAuthMiddleware and a sink that always succeeds.

    The sink's response is deliberately uninteresting: everything under test is whether the
    middleware lets the request reach it at all, not what it does once there.
    """
    app = falcon.App(middleware=[AdminAuthMiddleware()])
    app.add_sink(lambda req, resp: setattr(resp, "media", {"ok": True, "path": req.path}), prefix="/")
    return falcon.testing.TestClient(app)


class TestIsAdminPath:
    """Path matching must gate exactly the mount and its children -- no more, no less."""

    @pytest.mark.parametrize(
        argnames=["path"],
        argvalues=[
            (f"/{ADMIN_MOUNT_PREFIX}",),
            (f"/{ADMIN_MOUNT_PREFIX}/",),
            (f"/{ADMIN_MOUNT_PREFIX}/setup_schema",),
            (f"/{ADMIN_MOUNT_PREFIX}/nested/path",),
        ],
    )
    def test_matches_the_mount_and_its_children(self, path: str) -> None:
        assert _is_admin_path(path)

    @pytest.mark.parametrize(
        argnames=["path"],
        argvalues=[
            ("/",),
            ("/search",),
            (f"/{ADMIN_MOUNT_PREFIX}x",),
            (f"/{ADMIN_MOUNT_PREFIX}_evil",),
            ("/_administrator",),
        ],
    )
    def test_does_not_match_lookalikes(self, path: str) -> None:
        assert not _is_admin_path(path)


class TestSuppliedPassword:
    """Extracting the password half of a Basic credential."""

    def test_none_when_header_missing(self) -> None:
        assert _supplied_password(None) is None

    def test_none_for_non_basic_scheme(self) -> None:
        assert _supplied_password("Bearer sometoken") is None

    def test_none_for_malformed_base64(self) -> None:
        assert _supplied_password("Basic not-valid-base64!!!") is None

    def test_none_without_a_colon_separator(self) -> None:
        header = f"Basic {base64.b64encode(b'nocolonhere').decode()}"
        assert _supplied_password(header) is None

    def test_extracts_password_ignoring_username(self) -> None:
        assert _supplied_password(_basic_auth_header("anyuser", "secret")) == "secret"

    def test_scheme_match_is_case_insensitive(self) -> None:
        header = _basic_auth_header("anyuser", "secret").replace("Basic", "basic")
        assert _supplied_password(header) == "secret"

    def test_password_may_itself_contain_colons(self) -> None:
        assert _supplied_password(_basic_auth_header("user", "pa:ss:word")) == "pa:ss:word"


class TestAdminAuthMiddleware:
    """End-to-end behavior through a real Falcon dispatch cycle."""

    def test_rejects_with_no_credentials(self, client: falcon.testing.TestClient) -> None:
        result = client.simulate_get(f"/{ADMIN_MOUNT_PREFIX}/setup_schema")
        assert result.status == falcon.HTTP_401
        assert result.headers["WWW-Authenticate"] == 'Basic realm="admin"'

    def test_rejects_wrong_password(self, client: falcon.testing.TestClient) -> None:
        result = client.simulate_get(
            f"/{ADMIN_MOUNT_PREFIX}/setup_schema",
            headers={"Authorization": _basic_auth_header("admin", "wrong")},
        )
        assert result.status == falcon.HTTP_401

    def test_accepts_correct_password_regardless_of_username(
        self, client: falcon.testing.TestClient, admin_password: str
    ) -> None:
        result = client.simulate_get(
            f"/{ADMIN_MOUNT_PREFIX}/setup_schema",
            headers={"Authorization": _basic_auth_header("whoever", admin_password)},
        )
        assert result.status == falcon.HTTP_200
        assert result.json == {"ok": True, "path": f"/{ADMIN_MOUNT_PREFIX}/setup_schema"}

    def test_gates_every_http_method_not_just_get(
        self, client: falcon.testing.TestClient, admin_password: str
    ) -> None:
        assert client.simulate_post(f"/{ADMIN_MOUNT_PREFIX}/import_data").status == falcon.HTTP_401
        result = client.simulate_post(
            f"/{ADMIN_MOUNT_PREFIX}/import_data",
            headers={"Authorization": _basic_auth_header("whoever", admin_password)},
        )
        assert result.status == falcon.HTTP_200

    def test_public_routes_are_untouched(self, client: falcon.testing.TestClient) -> None:
        result = client.simulate_get("/search")
        assert result.status == falcon.HTTP_200
        assert "WWW-Authenticate" not in result.headers
        assert "Cache-Control" not in result.headers

    def test_admin_responses_are_marked_no_store_even_on_success(
        self, client: falcon.testing.TestClient, admin_password: str
    ) -> None:
        result = client.simulate_get(
            f"/{ADMIN_MOUNT_PREFIX}/setup_schema",
            headers={"Authorization": _basic_auth_header("whoever", admin_password)},
        )
        assert result.headers["Cache-Control"] == "no-store"

    def test_admin_rejections_are_also_marked_no_store(self, client: falcon.testing.TestClient) -> None:
        result = client.simulate_get(f"/{ADMIN_MOUNT_PREFIX}/setup_schema")
        assert result.headers["Cache-Control"] == "no-store"

    def test_unset_admin_password_rejects_everything(self, client: falcon.testing.TestClient) -> None:
        settings.admin_password = ""
        try:
            result = client.simulate_get(
                f"/{ADMIN_MOUNT_PREFIX}/setup_schema",
                headers={"Authorization": _basic_auth_header("whoever", "")},
            )
            assert result.status == falcon.HTTP_401
        finally:
            settings.admin_password = TEST_PASSWORD
