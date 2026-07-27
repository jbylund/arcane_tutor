"""Shared helpers for the API test suite."""

from __future__ import annotations


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
