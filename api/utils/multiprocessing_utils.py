"""Utilities for multiprocessing, including mock implementations for testing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


class MockLock:
    """Mock implementation of multiprocessing.Lock for testing."""

    def __enter__(self) -> MockLock:
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None) -> None:
        """Exit the context manager."""


class MockEvent:
    """Mock implementation of multiprocessing.Event for testing."""

    def __init__(self) -> None:
        """Initialize the mock event."""
        self._is_set = False

    def set(self) -> None:
        """Set the event."""
        self._is_set = True

    def clear(self) -> None:
        """Clear the event."""
        self._is_set = False

    def is_set(self) -> bool:
        """Return True if the event is set."""
        return self._is_set


DEFAULT_LOCK = MockLock()
DEFAULT_EVENT = MockEvent()
