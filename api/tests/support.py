"""Shared helpers for the API test suite."""

from __future__ import annotations

import multiprocessing
import time
from unittest.mock import MagicMock

from api.app_context import AppContext


def mock_app_context(**overrides: object) -> AppContext:
    """An AppContext with mock pools/engine, for injecting into APIResource(app_context=...).

    No real `psycopg_pool.ConnectionPool` is ever opened, so there is nothing to close afterward.
    `reader_pool`/`writer_pool` default to one shared `MagicMock()`, matching how a single
    `_conn_pool` mock covered every code path before AdminResource had its own pool. `last_import_time`
    defaults to now rather than `AppContext`'s own "nothing imported yet" default: `APIResource.__init__`
    calls `self.admin.import_data()`, and a stale `last_import_time` would make its fast path
    (`_import_recent`) miss and kick off a real Scryfall import during construction. Pass an
    explicit `reader_pool=`/`writer_pool=`/`last_import_time=` (or anything else `AppContext` takes)
    to override just that field.

    Args:
        **overrides: Any `AppContext.__init__` keyword to set explicitly instead of defaulting.

    Returns:
        A ready `AppContext`.
    """
    mock_pool = MagicMock()
    kwargs: dict[str, object] = {
        "reader_pool": mock_pool,
        "writer_pool": mock_pool,
        "engine": MagicMock(),
        "last_import_time": multiprocessing.Value("d", time.time(), lock=True),
    }
    kwargs.update(overrides)
    return AppContext(**kwargs)


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
