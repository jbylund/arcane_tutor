"""Shared helpers for the API test suite."""

from __future__ import annotations

from unittest.mock import MagicMock


def mock_conn_pool_kwargs() -> tuple[MagicMock, dict[str, MagicMock]]:
    """A mock pool plus the APIResource(...) kwargs that inject it as both `_conn_pool`s.

    APIResource.__init__ takes `conn_pool` and `admin_conn_pool` precisely so a test can hand it a
    mock at construction time; passed the return value here as `**kwargs`, no real
    psycopg_pool.ConnectionPool is ever opened, so there is nothing to close afterward. One mock
    covers both attributes, matching how a single `_conn_pool` mock covered every code path before
    AdminResource had its own pool.

    Returns:
        The mock, and a `{"conn_pool": ..., "admin_conn_pool": ...}` dict to spread into the
        APIResource(...) call.
    """
    mock_pool = MagicMock()
    return mock_pool, {"conn_pool": mock_pool, "admin_conn_pool": mock_pool}


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
