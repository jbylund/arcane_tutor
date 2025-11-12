"""Shared cache implementation for multi-process caching.

This module provides a drop-in replacement for cachetools.LRUCache that can be
shared between multiple processes using the lru-dict package with multiprocessing.Manager.
"""

from __future__ import annotations

import multiprocessing
from collections.abc import MutableMapping
from multiprocessing.managers import SyncManager
from typing import Any, Iterator

from lru import LRU


class SharedLRUCache(MutableMapping):
    """Thread-safe and process-safe LRU cache using lru-dict and multiprocessing.Manager.

    This cache can be shared across multiple processes and provides LRU (Least Recently Used)
    eviction when the cache reaches its maximum size. It is designed to be a drop-in replacement
    for cachetools.LRUCache.

    The implementation uses the lru-dict package's LRU class, which is a C-based implementation
    with efficient LRU eviction. The LRU instance is managed by multiprocessing.Manager to enable
    sharing across processes.

    Attributes:
        maxsize: Maximum number of items the cache can hold.
    """

    # List of methods to expose via proxy
    _exposed_methods = (
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__contains__",
        "__len__",
        "get",
        "clear",
        "pop",
        "keys",
        "values",
        "items",
    )

    def __init__(self: SharedLRUCache, maxsize: int, manager: SyncManager | None = None) -> None:
        """Initialize the SharedLRUCache.

        Args:
            maxsize: Maximum number of items the cache can hold.
            manager: Optional multiprocessing.Manager instance with LRU registered. If None,
                    a new manager is created and LRU is registered automatically.
        """
        if maxsize <= 0:
            msg = "maxsize must be positive"
            raise ValueError(msg)

        self.maxsize = maxsize

        # Use provided manager or create a new one
        if manager is None:
            # Register LRU with SyncManager if not already registered
            SyncManager.register("LRU", LRU, exposed=self._exposed_methods)

            # Create a new manager
            manager = SyncManager()
            manager.start()
            self._owned_manager = True
        else:
            self._owned_manager = False

        self._manager = manager

        # Create the shared LRU instance
        # Note: The manager needs to have LRU registered before we can call it
        try:
            self._lru = manager.LRU(maxsize)  # type: ignore[attr-defined]
        except (AttributeError, KeyError):
            # If manager doesn't have LRU, we need a manager that was started with it registered
            # This happens when a manager is passed in that wasn't set up correctly
            msg = "Manager must have LRU registered. Use SharedLRUCache.register_with_manager() first."
            raise ValueError(msg) from None

        # Get a lock from the manager for thread/process safety
        self._lock = manager.RLock()

    @staticmethod
    def register_with_manager(manager_class: type[SyncManager] = SyncManager) -> None:
        """Register the LRU class with a manager class.

        This is a convenience method to register LRU with a custom manager class before
        instantiating it. This is useful when you want to use a custom manager with multiple
        worker processes.

        Args:
            manager_class: The manager class to register LRU with. Defaults to SyncManager.

        Example:
            >>> from multiprocessing.managers import SyncManager
            >>> SharedLRUCache.register_with_manager(SyncManager)
            >>> manager = SyncManager()
            >>> manager.start()
            >>> cache = SharedLRUCache(maxsize=100, manager=manager)
        """
        if not hasattr(manager_class, "_registry") or "LRU" not in manager_class._registry:  # type: ignore[attr-defined]
            manager_class.register("LRU", LRU, exposed=SharedLRUCache._exposed_methods)

    def __getitem__(self: SharedLRUCache, key: Any) -> Any:
        """Get an item from the cache.

        Args:
            key: The key to retrieve.

        Returns:
            The value associated with the key.

        Raises:
            KeyError: If the key is not in the cache.
        """
        with self._lock:
            return self._lru[key]

    def __setitem__(self: SharedLRUCache, key: Any, value: Any) -> None:
        """Set an item in the cache, evicting LRU items if necessary.

        Args:
            key: The key to set.
            value: The value to associate with the key.
        """
        with self._lock:
            self._lru[key] = value

    def __delitem__(self: SharedLRUCache, key: Any) -> None:
        """Delete an item from the cache.

        Args:
            key: The key to delete.

        Raises:
            KeyError: If the key is not in the cache.
        """
        with self._lock:
            del self._lru[key]

    def __contains__(self: SharedLRUCache, key: Any) -> bool:
        """Check if a key is in the cache.

        Args:
            key: The key to check.

        Returns:
            True if the key is in the cache, False otherwise.
        """
        with self._lock:
            return key in self._lru

    def __len__(self: SharedLRUCache) -> int:
        """Get the number of items in the cache.

        Returns:
            The number of items currently in the cache.
        """
        with self._lock:
            return len(self._lru)

    def __iter__(self: SharedLRUCache) -> Iterator[Any]:
        """Iterate over the keys in the cache.

        Returns:
            An iterator over the cache keys.
        """
        with self._lock:
            # Create a snapshot of keys to avoid issues with concurrent modifications
            return iter(list(self._lru.keys()))

    def get(self: SharedLRUCache, key: Any, default: Any = None) -> Any:
        """Get an item from the cache with a default value.

        Args:
            key: The key to retrieve.
            default: The value to return if the key is not found.

        Returns:
            The value associated with the key, or default if not found.
        """
        with self._lock:
            return self._lru.get(key, default)

    def clear(self: SharedLRUCache) -> None:
        """Remove all items from the cache."""
        with self._lock:
            self._lru.clear()

    def pop(self: SharedLRUCache, key: Any, default: Any = None) -> Any:
        """Remove and return an item from the cache.

        Args:
            key: The key to remove.
            default: The value to return if the key is not found.

        Returns:
            The value associated with the key, or default if not found.
        """
        with self._lock:
            return self._lru.pop(key, default)

    def keys(self: SharedLRUCache) -> list[Any]:
        """Get a list of all keys in the cache.

        Returns:
            A list of all keys in the cache.
        """
        with self._lock:
            return list(self._lru.keys())

    def values(self: SharedLRUCache) -> list[Any]:
        """Get a list of all values in the cache.

        Returns:
            A list of all values in the cache.
        """
        with self._lock:
            return list(self._lru.values())

    def items(self: SharedLRUCache) -> list[tuple[Any, Any]]:
        """Get a list of all (key, value) pairs in the cache.

        Returns:
            A list of all (key, value) tuples in the cache.
        """
        with self._lock:
            return list(self._lru.items())

    def __repr__(self: SharedLRUCache) -> str:
        """Return a string representation of the cache.

        Returns:
            A string representation showing the cache type and size.
        """
        with self._lock:
            return f"SharedLRUCache(maxsize={self.maxsize}, size={len(self._lru)})"

    def __del__(self: SharedLRUCache) -> None:
        """Clean up the manager if we own it."""
        if hasattr(self, "_owned_manager") and self._owned_manager and hasattr(self, "_manager"):
            try:
                self._manager.shutdown()
            except Exception:  # noqa: S110
                # Ignore errors during cleanup
                pass
