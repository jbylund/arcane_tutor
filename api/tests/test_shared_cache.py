"""Tests for the shared cache implementation."""

from __future__ import annotations

import contextlib
import multiprocessing
import time
from collections.abc import MutableMapping

import pytest

from api.shared_cache import SharedLRUCache


def _worker_add_item(cache: SharedLRUCache, key: str, value: str) -> None:
    """Worker function to add item to cache."""
    cache[key] = value


def _worker_add_multiple_items(cache: SharedLRUCache, worker_id: int, num_items: int) -> None:
    """Worker function to add multiple items to cache."""
    for i in range(num_items):
        cache[f"worker_{worker_id}_item_{i}"] = f"value_{i}"


def _worker_reader_writer(cache: SharedLRUCache, process_id: int) -> None:
    """Worker that both reads and writes."""
    # Write some items
    for i in range(10):
        cache[f"proc_{process_id}_key_{i}"] = f"value_{i}"

    # Read some items (including from other processes)
    for i in range(10):
        with contextlib.suppress(Exception):
            _ = cache.get(f"proc_{process_id}_key_{i}")


class TestSharedLRUCacheBasicOperations:
    """Test basic dictionary operations of SharedLRUCache."""

    def test_initialization_with_valid_maxsize(self) -> None:
        """Test that cache initializes with valid maxsize."""
        cache = SharedLRUCache(maxsize=10)
        assert cache.maxsize == 10
        assert len(cache) == 0

    def test_initialization_with_zero_maxsize_raises_error(self) -> None:
        """Test that initializing with maxsize=0 raises ValueError."""
        with pytest.raises(ValueError, match="maxsize must be positive"):
            SharedLRUCache(maxsize=0)

    def test_initialization_with_negative_maxsize_raises_error(self) -> None:
        """Test that initializing with negative maxsize raises ValueError."""
        with pytest.raises(ValueError, match="maxsize must be positive"):
            SharedLRUCache(maxsize=-1)

    def test_set_and_get_single_item(self) -> None:
        """Test setting and getting a single item."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        assert cache["key1"] == "value1"
        assert len(cache) == 1

    def test_set_and_get_multiple_items(self) -> None:
        """Test setting and getting multiple items."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        cache["key2"] = "value2"
        cache["key3"] = "value3"

        assert cache["key1"] == "value1"
        assert cache["key2"] == "value2"
        assert cache["key3"] == "value3"
        assert len(cache) == 3

    def test_update_existing_item(self) -> None:
        """Test updating an existing item."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        cache["key1"] = "value2"

        assert cache["key1"] == "value2"
        assert len(cache) == 1

    def test_get_nonexistent_key_raises_keyerror(self) -> None:
        """Test that getting a nonexistent key raises KeyError."""
        cache = SharedLRUCache(maxsize=10)
        with pytest.raises(KeyError):
            _ = cache["nonexistent"]

    def test_get_method_with_default(self) -> None:
        """Test the get method with default value."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"

        assert cache.get("key1") == "value1"
        assert cache.get("nonexistent") is None
        assert cache.get("nonexistent", "default") == "default"

    def test_delete_item(self) -> None:
        """Test deleting an item from cache."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        cache["key2"] = "value2"

        del cache["key1"]

        assert len(cache) == 1
        assert "key1" not in cache
        assert "key2" in cache

    def test_delete_nonexistent_key_raises_keyerror(self) -> None:
        """Test that deleting a nonexistent key raises KeyError."""
        cache = SharedLRUCache(maxsize=10)
        with pytest.raises(KeyError):
            del cache["nonexistent"]

    def test_contains_operator(self) -> None:
        """Test the 'in' operator."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"

        assert "key1" in cache
        assert "nonexistent" not in cache

    def test_len_operator(self) -> None:
        """Test the len() function."""
        cache = SharedLRUCache(maxsize=10)
        assert len(cache) == 0

        cache["key1"] = "value1"
        assert len(cache) == 1

        cache["key2"] = "value2"
        assert len(cache) == 2

        del cache["key1"]
        assert len(cache) == 1

    def test_clear_method(self) -> None:
        """Test the clear method."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        cache["key2"] = "value2"
        cache["key3"] = "value3"

        cache.clear()

        assert len(cache) == 0
        assert "key1" not in cache
        assert "key2" not in cache
        assert "key3" not in cache

    def test_pop_method(self) -> None:
        """Test the pop method."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        cache["key2"] = "value2"

        value = cache.pop("key1")
        assert value == "value1"
        assert "key1" not in cache
        assert len(cache) == 1

    def test_pop_nonexistent_key_returns_default(self) -> None:
        """Test that popping a nonexistent key returns default."""
        cache = SharedLRUCache(maxsize=10)
        value = cache.pop("nonexistent")
        assert value is None

        value = cache.pop("nonexistent", "default")
        assert value == "default"

    def test_iter_over_keys(self) -> None:
        """Test iterating over cache keys."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"
        cache["key2"] = "value2"
        cache["key3"] = "value3"

        keys = list(cache)
        assert len(keys) == 3
        assert "key1" in keys
        assert "key2" in keys
        assert "key3" in keys

    def test_repr(self) -> None:
        """Test string representation."""
        cache = SharedLRUCache(maxsize=10)
        cache["key1"] = "value1"

        repr_str = repr(cache)
        assert "SharedLRUCache" in repr_str
        assert "maxsize=10" in repr_str
        assert "size=1" in repr_str


class TestSharedLRUCacheLRUEviction:
    """Test LRU eviction behavior of SharedLRUCache."""

    def test_eviction_when_cache_is_full(self) -> None:
        """Test that LRU item is evicted when cache reaches maxsize."""
        cache = SharedLRUCache(maxsize=3)

        # Fill cache
        cache["key1"] = "value1"
        time.sleep(0.01)  # Ensure different timestamps
        cache["key2"] = "value2"
        time.sleep(0.01)
        cache["key3"] = "value3"
        time.sleep(0.01)

        # Add one more - should evict key1 (least recently used)
        cache["key4"] = "value4"

        assert len(cache) == 3
        assert "key1" not in cache
        assert "key2" in cache
        assert "key3" in cache
        assert "key4" in cache

    def test_access_updates_lru_order(self) -> None:
        """Test that accessing an item updates its position in LRU order."""
        cache = SharedLRUCache(maxsize=3)

        cache["key1"] = "value1"
        time.sleep(0.01)
        cache["key2"] = "value2"
        time.sleep(0.01)
        cache["key3"] = "value3"
        time.sleep(0.01)

        # Access key1 to make it more recent
        _ = cache["key1"]
        time.sleep(0.01)

        # Add new item - should evict key2 (now least recently used)
        cache["key4"] = "value4"

        assert len(cache) == 3
        assert "key1" in cache
        assert "key2" not in cache
        assert "key3" in cache
        assert "key4" in cache

    def test_update_refreshes_lru_order(self) -> None:
        """Test that updating an item refreshes its position in LRU order."""
        cache = SharedLRUCache(maxsize=3)

        cache["key1"] = "value1"
        time.sleep(0.01)
        cache["key2"] = "value2"
        time.sleep(0.01)
        cache["key3"] = "value3"
        time.sleep(0.01)

        # Update key1 to make it more recent
        cache["key1"] = "value1_updated"
        time.sleep(0.01)

        # Add new item - should evict key2 (now least recently used)
        cache["key4"] = "value4"

        assert len(cache) == 3
        assert cache["key1"] == "value1_updated"
        assert "key2" not in cache
        assert "key3" in cache
        assert "key4" in cache

    def test_sequential_evictions(self) -> None:
        """Test multiple sequential evictions."""
        cache = SharedLRUCache(maxsize=2)

        cache["key1"] = "value1"
        time.sleep(0.01)
        cache["key2"] = "value2"
        time.sleep(0.01)

        # These should evict in order: key1, key2, key3
        cache["key3"] = "value3"
        time.sleep(0.01)
        assert "key1" not in cache

        cache["key4"] = "value4"
        time.sleep(0.01)
        assert "key2" not in cache

        cache["key5"] = "value5"
        time.sleep(0.01)
        assert "key3" not in cache

        assert len(cache) == 2
        assert "key4" in cache
        assert "key5" in cache


class TestSharedLRUCacheMultiprocessing:
    """Test multiprocessing capabilities of SharedLRUCache."""

    def test_cache_shared_between_processes_with_manager(self) -> None:
        """Test that cache can be shared between processes using a manager."""
        manager = multiprocessing.Manager()
        cache = SharedLRUCache(maxsize=10, manager=manager)

        # Add item in main process
        cache["main_key"] = "main_value"

        # Add item in child process
        process = multiprocessing.Process(target=_worker_add_item, args=(cache, "child_key", "child_value"))
        process.start()
        process.join()

        # Both items should be in cache
        assert "main_key" in cache
        assert "child_key" in cache
        assert cache["main_key"] == "main_value"
        assert cache["child_key"] == "child_value"

        manager.shutdown()

    def test_multiple_processes_writing_to_cache(self) -> None:
        """Test multiple processes writing to the same cache."""
        manager = multiprocessing.Manager()
        cache = SharedLRUCache(maxsize=100, manager=manager)

        # Start multiple processes
        processes = []
        num_workers = 3
        items_per_worker = 5

        for worker_id in range(num_workers):
            process = multiprocessing.Process(target=_worker_add_multiple_items, args=(cache, worker_id, items_per_worker))
            processes.append(process)
            process.start()

        # Wait for all processes to complete
        for process in processes:
            process.join()

        # Check that all items were added
        expected_items = num_workers * items_per_worker
        assert len(cache) == expected_items

        # Verify some specific items
        assert "worker_0_item_0" in cache
        assert "worker_1_item_2" in cache
        assert "worker_2_item_4" in cache

        manager.shutdown()

    def test_process_safety_with_concurrent_access(self) -> None:
        """Test that concurrent access from multiple processes is safe."""
        manager = multiprocessing.Manager()
        cache = SharedLRUCache(maxsize=50, manager=manager)

        # Start multiple processes
        processes = []
        for i in range(3):
            process = multiprocessing.Process(target=_worker_reader_writer, args=(cache, i))
            processes.append(process)
            process.start()

        # Wait for completion
        for process in processes:
            process.join()

        # Cache should have items from all processes (may be less than 30 due to maxsize=50)
        assert len(cache) > 0

        manager.shutdown()


class TestSharedLRUCacheDropInReplacement:
    """Test that SharedLRUCache can replace cachetools.LRUCache."""

    def test_compatible_with_dictionary_interface(self) -> None:
        """Test that cache supports standard dictionary operations."""
        cache = SharedLRUCache(maxsize=10)

        # Test standard dict operations
        cache["key"] = "value"
        assert cache["key"] == "value"
        assert "key" in cache
        assert len(cache) == 1

        del cache["key"]
        assert "key" not in cache
        assert len(cache) == 0

    def test_can_be_used_as_mutablemapping(self) -> None:
        """Test that cache can be used where MutableMapping is expected."""
        cache = SharedLRUCache(maxsize=10)
        assert isinstance(cache, MutableMapping)

    def test_compatible_initialization_signature(self) -> None:
        """Test that initialization is compatible with LRUCache."""
        # Should accept maxsize parameter like LRUCache
        cache = SharedLRUCache(maxsize=100)
        assert cache.maxsize == 100

    def test_get_method_compatibility(self) -> None:
        """Test that get method works like dict.get."""
        cache = SharedLRUCache(maxsize=10)
        cache["key"] = "value"

        # get() with no default
        assert cache.get("key") == "value"
        assert cache.get("missing") is None

        # get() with default
        assert cache.get("missing", "default") == "default"


class TestSharedLRUCacheEdgeCases:
    """Test edge cases and error conditions."""

    def test_cache_with_maxsize_one(self) -> None:
        """Test cache with maxsize=1."""
        cache = SharedLRUCache(maxsize=1)

        cache["key1"] = "value1"
        assert cache["key1"] == "value1"

        cache["key2"] = "value2"
        assert "key1" not in cache
        assert cache["key2"] == "value2"
        assert len(cache) == 1

    def test_cache_with_complex_keys(self) -> None:
        """Test cache with tuple keys (like in CachingMiddleware)."""
        cache = SharedLRUCache(maxsize=10)

        # Test with tuple keys
        key1 = ("path", (("param1", "value1"),), (("header1", "value1"),))
        key2 = ("path", (("param2", "value2"),), (("header2", "value2"),))

        cache[key1] = "response1"
        cache[key2] = "response2"

        assert cache[key1] == "response1"
        assert cache[key2] == "response2"

    def test_cache_with_none_values(self) -> None:
        """Test cache can store None values."""
        cache = SharedLRUCache(maxsize=10)

        cache["key"] = None
        assert "key" in cache
        assert cache["key"] is None
        assert cache.get("key") is None

    def test_empty_cache_operations(self) -> None:
        """Test operations on empty cache."""
        cache = SharedLRUCache(maxsize=10)

        assert len(cache) == 0
        assert list(cache) == []
        cache.clear()  # Should not raise
        assert len(cache) == 0
