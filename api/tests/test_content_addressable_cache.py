"""Tests for content-addressable shared memory cache."""

from __future__ import annotations

import multiprocessing
import time

import pytest

from api.content_addressable_cache import ContentAddressableCache


def test_basic_set_get() -> None:
    """Test basic set and get operations."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        assert cache[b"key1"] == b"value1"
        assert len(cache) == 1
    finally:
        cache.close()


def test_multiple_keys() -> None:
    """Test multiple keys."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"
        cache[b"key3"] = b"value3"

        assert cache[b"key1"] == b"value1"
        assert cache[b"key2"] == b"value2"
        assert cache[b"key3"] == b"value3"
        assert len(cache) == 3
    finally:
        cache.close()


def test_update_existing_key() -> None:
    """Test updating an existing key."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        cache[b"key1"] = b"value2"

        assert cache[b"key1"] == b"value2"
        assert len(cache) == 1
    finally:
        cache.close()


def test_key_not_found() -> None:
    """Test KeyError when key not found."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        with pytest.raises(KeyError):
            _ = cache[b"nonexistent"]
    finally:
        cache.close()


def test_get_with_default() -> None:
    """Test get method with default value."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"

        assert cache.get(b"key1") == b"value1"
        assert cache.get(b"nonexistent") is None
        assert cache.get(b"nonexistent", b"default") == b"default"
    finally:
        cache.close()


def test_delete_key() -> None:
    """Test deleting a key."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"

        del cache[b"key1"]

        assert len(cache) == 1
        assert b"key1" not in cache
        assert b"key2" in cache
    finally:
        cache.close()


def test_delete_nonexistent_key() -> None:
    """Test KeyError when deleting nonexistent key."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        with pytest.raises(KeyError):
            del cache[b"nonexistent"]
    finally:
        cache.close()


def test_contains() -> None:
    """Test 'in' operator."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"

        assert b"key1" in cache
        assert b"nonexistent" not in cache
    finally:
        cache.close()


def test_clear() -> None:
    """Test clear method."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"
        cache[b"key3"] = b"value3"

        cache.clear()

        assert len(cache) == 0
        assert b"key1" not in cache
        assert b"key2" not in cache
        assert b"key3" not in cache
    finally:
        cache.close()


def test_iteration() -> None:
    """Test iterating over keys."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"
        cache[b"key3"] = b"value3"

        keys = list(cache)
        assert len(keys) == 3
        assert b"key1" in keys
        assert b"key2" in keys
        assert b"key3" in keys
    finally:
        cache.close()


def test_content_deduplication() -> None:
    """Test that multiple keys can share the same content."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        # Same value for different keys
        cache[b"key1"] = b"shared_value"
        cache[b"key2"] = b"shared_value"
        cache[b"key3"] = b"shared_value"

        assert cache[b"key1"] == b"shared_value"
        assert cache[b"key2"] == b"shared_value"
        assert cache[b"key3"] == b"shared_value"
        assert len(cache) == 3

        # All should return same value
        assert cache[b"key1"] == cache[b"key2"] == cache[b"key3"]
    finally:
        cache.close()


def test_key_deduplication() -> None:
    """Test that reusing same key doesn't create duplicate."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        initial_len = len(cache)

        # Update with different value
        cache[b"key1"] = b"value2"
        # Should still be one item
        assert len(cache) == 1
        assert cache[b"key1"] == b"value2"
    finally:
        cache.close()


def test_lru_eviction() -> None:
    """Test LRU eviction when cache is full."""
    cache = ContentAddressableCache(maxsize=3)
    try:
        # Fill cache
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"
        cache[b"key3"] = b"value3"

        # Add one more - should evict one
        cache[b"key4"] = b"value4"

        assert len(cache) == 3
        # At least one of the original keys should be gone
        # (Simple eviction - evicts first found)
        assert b"key4" in cache
    finally:
        cache.close()


def test_context_manager() -> None:
    """Test context manager usage."""
    with ContentAddressableCache(maxsize=10) as cache:
        cache[b"key1"] = b"value1"
        assert cache[b"key1"] == b"value1"


def test_multiprocessing_shared() -> None:
    """Test that cache can be shared between processes."""
    cache = ContentAddressableCache(maxsize=100)
    try:
        # Add item in main process
        cache[b"main_key"] = b"main_value"

        def worker(cache: ContentAddressableCache) -> None:
            cache[b"worker_key"] = b"worker_value"

        # Create worker process
        process = multiprocessing.Process(target=worker, args=(cache,))
        process.start()
        process.join()

        # Both items should be accessible
        assert b"main_key" in cache
        assert b"worker_key" in cache
        assert cache[b"main_key"] == b"main_value"
        assert cache[b"worker_key"] == b"worker_value"
    finally:
        cache.close()


def test_multiple_processes_concurrent() -> None:
    """Test multiple processes accessing cache concurrently."""
    cache = ContentAddressableCache(maxsize=100)
    try:
        def worker(worker_id: int, cache: ContentAddressableCache) -> None:
            for i in range(10):
                key = f"worker_{worker_id}_key_{i}".encode()
                value = f"worker_{worker_id}_value_{i}".encode()
                cache[key] = value

        # Start multiple processes
        processes = []
        for i in range(3):
            process = multiprocessing.Process(target=worker, args=(i, cache))
            processes.append(process)
            process.start()

        # Wait for all
        for process in processes:
            process.join()

        # Check that items were added
        assert len(cache) > 0
        # Verify some specific items
        assert b"worker_0_key_0" in cache
        assert b"worker_1_key_5" in cache
        assert b"worker_2_key_9" in cache
    finally:
        cache.close()


def test_large_values() -> None:
    """Test with large values."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        large_value = b"x" * 10000
        cache[b"key1"] = large_value
        assert cache[b"key1"] == large_value
    finally:
        cache.close()


def test_empty_key() -> None:
    """Test with empty key."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b""] = b"value"
        assert cache[b""] == b"value"
    finally:
        cache.close()


def test_empty_value() -> None:
    """Test with empty value."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key"] = b""
        assert cache[b"key"] == b""
    finally:
        cache.close()


def test_custom_hash_function() -> None:
    """Test with custom hash function."""

    def custom_hash(data: bytes) -> tuple[int, bytes]:
        """Simple custom hash."""
        key_hash = hash(data) & 0xFFFFFFFF_FFFFFFFF
        fingerprint = hash(data).to_bytes(16, byteorder="big")
        return key_hash, fingerprint

    cache = ContentAddressableCache(maxsize=10, hash_func=custom_hash)
    try:
        cache[b"key1"] = b"value1"
        assert cache[b"key1"] == b"value1"
    finally:
        cache.close()


def test_invalid_maxsize() -> None:
    """Test that invalid maxsize raises error."""
    with pytest.raises(ValueError, match="maxsize must be positive"):
        ContentAddressableCache(maxsize=0)

    with pytest.raises(ValueError, match="maxsize must be positive"):
        ContentAddressableCache(maxsize=-1)


def test_reuse_existing_shared_memory() -> None:
    """Test reusing existing shared memory."""
    cache1 = ContentAddressableCache(maxsize=10)
    try:
        cache1[b"key1"] = b"value1"

        # Create second cache using same shared memory
        cache2 = ContentAddressableCache(maxsize=10, shared_memory=cache1._shm)
        try:
            # Should see the same data
            assert cache2[b"key1"] == b"value1"
            assert len(cache2) == 1
        finally:
            cache2.close()
    finally:
        cache1.close()


def test_concurrent_reads() -> None:
    """Test that multiple processes can read concurrently."""
    cache = ContentAddressableCache(maxsize=100)
    try:
        # Pre-populate cache
        for i in range(50):
            cache[f"key_{i}".encode()] = f"value_{i}".encode()

        def reader(process_id: int, cache: ContentAddressableCache) -> None:
            """Read from cache."""
            for i in range(50):
                key = f"key_{i}".encode()
                if key in cache:
                    value = cache[key]
                    assert value == f"value_{i}".encode()

        # Start multiple reader processes
        processes = []
        for i in range(5):
            process = multiprocessing.Process(target=reader, args=(i, cache))
            processes.append(process)
            process.start()

        # Wait for all
        for process in processes:
            process.join()
    finally:
        cache.close()


def test_stress_test() -> None:
    """Stress test with many operations."""
    cache = ContentAddressableCache(maxsize=1000)
    try:
        # Write many items
        for i in range(500):
            cache[f"key_{i}".encode()] = f"value_{i}".encode()

        # Read many items
        for i in range(500):
            if f"key_{i}".encode() in cache:
                value = cache[f"key_{i}".encode()]
                assert value == f"value_{i}".encode()

        # Update some
        for i in range(100, 200):
            cache[f"key_{i}".encode()] = f"updated_value_{i}".encode()

        # Delete some
        for i in range(300, 400):
            if f"key_{i}".encode() in cache:
                del cache[f"key_{i}".encode()]

        assert len(cache) > 0
    finally:
        cache.close()
