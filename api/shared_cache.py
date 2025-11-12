"""Shared cache implementation for multi-process caching.

This module provides a drop-in replacement for cachetools.LRUCache that can be
shared between multiple processes using the lru-dict package with multiprocessing.Manager.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, MutableMapping
from multiprocessing.managers import SyncManager
from typing import Any

from lru import LRU


class _LRUWrapper:
    """Wrapper class for LRU that can be pickled and registered with multiprocessing.Manager.

    This wrapper creates the actual LRU instance in the manager process, avoiding
    pickling issues with the C extension module.
    """

    def __init__(self: _LRUWrapper, maxsize: int) -> None:
        """Initialize the wrapper with a maxsize, creating the LRU instance.

        Args:
            maxsize: Maximum number of items the cache can hold.
        """
        # Import here so it happens in the manager process
        from lru import LRU  # noqa: PLC0415

        self._lru = LRU(maxsize)

    def __getitem__(self: _LRUWrapper, key: Any) -> Any:
        """Get an item from the cache."""
        return self._lru[key]

    def __setitem__(self: _LRUWrapper, key: Any, value: Any) -> None:
        """Set an item in the cache."""
        self._lru[key] = value

    def __delitem__(self: _LRUWrapper, key: Any) -> None:
        """Delete an item from the cache."""
        del self._lru[key]

    def __contains__(self: _LRUWrapper, key: Any) -> bool:
        """Check if a key is in the cache."""
        return key in self._lru

    def __len__(self: _LRUWrapper) -> int:
        """Get the number of items in the cache."""
        return len(self._lru)

    def get(self: _LRUWrapper, key: Any, default: Any = None) -> Any:
        """Get an item from the cache with a default value."""
        return self._lru.get(key, default)

    def clear(self: _LRUWrapper) -> None:
        """Remove all items from the cache."""
        self._lru.clear()

    def pop(self: _LRUWrapper, key: Any, default: Any = None) -> Any:
        """Remove and return an item from the cache."""
        return self._lru.pop(key, default)

    def keys(self: _LRUWrapper) -> list[Any]:
        """Get a list of all keys in the cache."""
        return list(self._lru.keys())

    def values(self: _LRUWrapper) -> list[Any]:
        """Get a list of all values in the cache."""
        return list(self._lru.values())

    def items(self: _LRUWrapper) -> list[tuple[Any, Any]]:
        """Get a list of all (key, value) pairs in the cache."""
        return list(self._lru.items())


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
            # Register _LRUWrapper with SyncManager if not already registered
            SyncManager.register("LRU", _LRUWrapper, exposed=self._exposed_methods)

            # Create a new manager
            manager = SyncManager()
            manager.start()
            self._owned_manager = True
        else:
            # If manager is provided, check if it's started and if LRU is registered
            manager_started = hasattr(manager, "_process") and manager._process is not None  # type: ignore[attr-defined]

            if not manager_started:
                # Manager not started yet - we can register and start it
                manager_class = type(manager)
                if not hasattr(manager_class, "_registry") or "LRU" not in manager_class._registry:  # type: ignore[attr-defined]
                    manager_class.register("LRU", _LRUWrapper, exposed=self._exposed_methods)
                manager.start()
            else:
                # Manager already started - check if LRU is registered
                manager_class = type(manager)
                if not hasattr(manager_class, "_registry") or "LRU" not in manager_class._registry:  # type: ignore[attr-defined]
                    msg = "Manager is already started but LRU is not registered. Use SharedLRUCache.register_with_manager() before creating the manager."
                    raise ValueError(msg)

            self._owned_manager = False

        self._manager = manager

        # Create the shared LRU instance via the wrapper
        # Note: The manager needs to have LRU registered before we can call it
        try:
            self._lru = manager.LRU(maxsize)  # type: ignore[attr-defined]
        except (AttributeError, KeyError) as e:
            # If manager doesn't have LRU, we need a manager that was started with it registered
            # This happens when a manager is passed in that wasn't set up correctly
            msg = "Manager must have LRU registered. Use SharedLRUCache.register_with_manager() first."
            raise ValueError(msg) from e

        # Get a lock from the manager for thread/process safety
        self._lock = manager.RLock()

    @staticmethod
    def register_with_manager(manager_class: type[SyncManager] = SyncManager) -> None:
        """Register the LRU wrapper class with a manager class.

        This is a convenience method to register _LRUWrapper with a custom manager class before
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
            manager_class.register("LRU", _LRUWrapper, exposed=SharedLRUCache._exposed_methods)

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

    def __getstate__(self: SharedLRUCache) -> dict[str, Any]:
        """Support pickling for multiprocessing.

        Returns:
            State dictionary containing only picklable attributes.
        """
        # Only pickle the proxy objects and maxsize, not the manager itself
        return {
            "_lru": self._lru,
            "_lock": self._lock,
            "maxsize": self.maxsize,
            "_owned_manager": False,  # Don't try to manage manager lifecycle after unpickling
        }

    def __setstate__(self: SharedLRUCache, state: dict[str, Any]) -> None:
        """Support unpickling for multiprocessing.

        Args:
            state: State dictionary from __getstate__.
        """
        self._lru = state["_lru"]
        self._lock = state["_lock"]
        self.maxsize = state["maxsize"]
        self._owned_manager = state["_owned_manager"]
        self._manager = None  # Manager reference is not needed after unpickling

    def __del__(self: SharedLRUCache) -> None:
        """Clean up the manager if we own it."""
        if hasattr(self, "_owned_manager") and self._owned_manager and hasattr(self, "_manager"):
            with contextlib.suppress(Exception):
                self._manager.shutdown()
