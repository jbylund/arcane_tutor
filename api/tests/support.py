"""Shared helpers for the API test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from api.api_resource import APIResource


def stub_conn_pools(api_resource: APIResource) -> MagicMock:
    """Close the real pools opened during construction and replace both with one mock.

    APIResource.__init__ and the AdminResource it constructs each open a real
    psycopg_pool.ConnectionPool immediately (min_size=1, open=True). A test that doesn't need a
    real database must close both before dropping the reference, or the sockets and the pool's
    background thread outlive the test. Returns one mock shared by both attributes, matching how
    a single `_conn_pool` mock covered every code path before AdminResource had its own pool.

    Args:
        api_resource: An already-constructed APIResource whose pools should be replaced.

    Returns:
        The MagicMock now assigned to both `api_resource._conn_pool` and `api_resource.admin._conn_pool`.
    """
    api_resource._conn_pool.close()
    api_resource.admin._conn_pool.close()
    mock_pool = MagicMock()
    api_resource._conn_pool = mock_pool
    api_resource.admin._conn_pool = mock_pool
    return mock_pool


def override_attr(obj: object, name: str, value: object) -> None:
    """Replace an existing attribute on an instance, failing if it is not already there.

    Plain `obj.attr = value` silently *creates* the attribute when the name is wrong or the method
    has moved elsewhere. For suppression overrides like `_import_recent` that failure is invisible:
    the real method stays in place, the work it was suppressing starts happening for real, and the
    tests still pass. This raises instead.

    Prefer pytest's `monkeypatch.setattr`, which does the same check, wherever the fixture is
    available — this exists for `setup_method` and other contexts where it is not.

    Args:
        obj: Instance to modify.
        name: Attribute that must already exist on obj.
        value: Replacement value.

    Raises:
        AttributeError: If obj has no such attribute.
    """
    if not hasattr(obj, name):
        msg = f"{type(obj).__name__} has no attribute {name!r} to override; has it moved?"
        raise AttributeError(msg)
    setattr(obj, name, value)
