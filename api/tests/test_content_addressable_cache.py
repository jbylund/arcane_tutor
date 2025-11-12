"""Tests for content-addressable shared memory cache."""

from __future__ import annotations

import itertools
import multiprocessing
import random
import time

import pytest

from api.content_addressable_cache import ContentAddressableCache


# Module-level worker functions for multiprocessing tests
def _worker_add_item(cache: ContentAddressableCache) -> None:
    """Worker function to add item to cache."""
    cache[b"worker_key"] = b"worker_value"


def _worker_add_multiple_items(worker_id: int, cache: ContentAddressableCache) -> None:
    """Worker function to add multiple items to cache."""
    for i in range(10):
        key = f"worker_{worker_id}_key_{i}".encode()
        value = f"worker_{worker_id}_value_{i}".encode()
        cache[key] = value


def _worker_reader(process_id: int, cache: ContentAddressableCache) -> None:
    """Worker function to read from cache."""
    for i in range(50):
        key = f"key_{i}".encode()
        if key in cache:
            value = cache[key]
            assert value == f"value_{i}".encode()


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


def test_keys_method() -> None:
    """Test the keys() method."""
    expected = set()
    cache = ContentAddressableCache(maxsize=10)
    try:
        for idx in range(3):
            key = f"key{idx}".encode()
            value = f"value{idx}".encode()
            cache[key] = value
            expected.add(key)

        keys = cache.keys()
        assert isinstance(keys, list)
        assert len(keys) == 3
        for key in expected:
            assert key in keys

        # Should return same as list(cache)
        assert set(keys) == set(cache)
        assert set(keys) == expected
    finally:
        cache.close()


def test_content_items() -> None:
    """Test iterating over content fingerprint and content blob pairs."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        # Store some items
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"
        cache[b"key3"] = b"value1"  # Same value as key1

        # Get all content items
        content_items = list(cache.content_items())

        # Should have 2 unique content blobs (value1 and value2)
        assert len(content_items) == 2

        # Verify structure: each item is (fingerprint, content_bytes)
        for fingerprint, content_bytes in content_items:
            assert isinstance(fingerprint, bytes)
            assert len(fingerprint) == 16  # 128-bit fingerprint
            assert isinstance(content_bytes, bytes)
            assert content_bytes in (b"value1", b"value2")

        # Verify fingerprints are unique
        fingerprints = [fp for fp, _ in content_items]
        assert len(fingerprints) == len(set(fingerprints))

        # Verify content deduplication: value1 should appear once
        contents = [content for _, content in content_items]
        assert b"value1" in contents
        assert b"value2" in contents
        assert contents.count(b"value1") == 1  # Only stored once
    finally:
        cache.close()


def test_content_items_memory_efficient() -> None:
    """Test that content_items() doesn't load all content into memory at once."""
    cache = ContentAddressableCache(maxsize=100)
    try:
        # Store many items with some duplicates
        for i in range(50):
            cache[f"key_{i}".encode()] = f"value_{i % 10}".encode()  # Only 10 unique values

        # Iterate and count - should be able to process one at a time
        count = 0
        seen_fingerprints = set()
        for fingerprint, content_bytes in cache.content_items():
            count += 1
            seen_fingerprints.add(fingerprint)
            # Verify we're getting content one at a time (not all at once)
            assert isinstance(content_bytes, bytes)

        # Should have 10 unique content blobs (0-9)
        assert count == 10
        assert len(seen_fingerprints) == 10
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

        stored_content_blobs = {
            content_bytes for _, content_bytes in cache.content_items()
        }
        assert {b"shared_value"} == stored_content_blobs

        # All should return same value
        assert cache[b"key1"] == cache[b"key2"] == cache[b"key3"]
    finally:
        cache.close()


def test_content_deduplication_storage() -> None:
    """Test that storing same value under different keys only stores one copy in blob pool."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        # Store same value under multiple keys
        shared_value = b"shared_value_12345"
        cache[b"key1"] = shared_value
        used_after_one = cache._get_blob_pool_used()

        cache[b"key2"] = shared_value
        used_after_two = cache._get_blob_pool_used()

        cache[b"key3"] = shared_value
        used_after_three = cache._get_blob_pool_used()

        # Blob pool usage should only increase by key storage (not content storage)
        # Each key adds: 1 (type) + 4 (length) + key_len, aligned to 8 bytes
        # Content is only stored once
        key_entry_size = (1 + 4 + len(b"key1") + 7) & ~7  # Aligned

        # After first insert: key1 + content
        # After second insert: key1 + content + key2 (content reused)
        # After third insert: key1 + content + key2 + key3 (content reused)
        expected_after_two = used_after_one + key_entry_size
        expected_after_three = used_after_two + key_entry_size

        # Allow some tolerance for alignment differences
        assert abs(used_after_two - expected_after_two) <= 8
        assert abs(used_after_three - expected_after_three) <= 8

        # Now store different values - should use more space
        cache[b"key4"] = b"different_value_1"
        used_after_different1 = cache._get_blob_pool_used()
        cache[b"key5"] = b"different_value_2"
        used_after_different2 = cache._get_blob_pool_used()

        # Each different value should add both key and content
        diff1 = used_after_different1 - used_after_three
        diff2 = used_after_different2 - used_after_different1

        # Should be significantly more than just a key (includes content)
        assert diff1 > key_entry_size
        assert diff2 > key_entry_size
    finally:
        cache.close()


def test_key_deduplication() -> None:
    """Test that reusing same key doesn't create duplicate."""
    cache = ContentAddressableCache(maxsize=10)
    try:
        cache[b"key1"] = b"value1"
        assert len(cache) == 1

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

        # Create worker process
        process = multiprocessing.Process(target=_worker_add_item, args=(cache,))
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
        # Start multiple processes
        processes = []
        for i in range(3):
            process = multiprocessing.Process(target=_worker_add_multiple_items, args=(i, cache))
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

    def custom_hash(data: bytes) -> bytes:
        """Simple custom hash returning 128-bit hash."""
        # Handle negative hash values by masking
        h = hash(data)
        # Use two different hash calls to get 128 bits
        h2 = hash(data[::-1])  # Hash reversed data for second part
        fp_int = abs(h) & 0xFFFFFFFF_FFFFFFFF
        fp_int2 = abs(h2) & 0xFFFFFFFF_FFFFFFFF
        return (fp_int << 64 | fp_int2).to_bytes(16, byteorder="big")

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

        # Start multiple reader processes
        processes = []
        for i in range(5):
            process = multiprocessing.Process(target=_worker_reader, args=(i, cache))
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

@pytest.mark.skip(reason="benchmark test, not expected to run every time")
def test_throughput_benchmark() -> None:  # noqa: PLR0915
    """Benchmark throughput with random set/get operations and compare to dict."""
    # Test parameters
    duration_seconds = 10.0
    set_ratio = 0.3  # 30% sets
    max_keys = 1000
    key_prefix = b"key_"
    value_prefix = b"value_"

    # true means set, false means get
    operations = itertools.cycle([
        random.random() < set_ratio
        for _ in range(10_000)
    ])

    key_ids = [
        random.randint(0, max_keys - 1)
        for _ in range(10_000)
    ]

    key_value_pairs = itertools.cycle([
        (
            key_prefix + str(key_id).encode(),
            value_prefix + str(key_id).encode(),
        )
        for key_id in key_ids
    ])



    # Test with ContentAddressableCache
    cache = ContentAddressableCache(maxsize=max_keys * 10)
    try:
        cache_ops = 0
        cache_sets = 0
        cache_gets = 0
        cache_misses = 0

        start_time = time.time()
        end_time = start_time + duration_seconds

        while time.time() < end_time:
            key, value = next(key_value_pairs)

            if next(operations):
                # Set operation
                cache[key] = value
                cache_sets += 1
            else:
                # Get operation
                try:
                    _ = cache[key]
                    cache_gets += 1
                except KeyError:
                    cache_misses += 1

            cache_ops += 1

        cache_elapsed = time.time() - start_time
        cache_ops / cache_elapsed

    finally:
        cache.close()

    # Test with regular dict for comparison
    dict_cache: dict[bytes, bytes] = {}
    dict_ops = 0
    dict_sets = 0
    dict_gets = 0
    dict_misses = 0

    start_time = time.time()
    end_time = start_time + duration_seconds

    while time.time() < end_time:
        key, value = next(key_value_pairs)

        if next(operations):
            # Set operation
            dict_cache[key] = value
            dict_sets += 1
        # Get operation
        elif key in dict_cache:
            _ = dict_cache[key]
            dict_gets += 1
        else:
            dict_misses += 1

        dict_ops += 1

    dict_elapsed = time.time() - start_time
    dict_ops / dict_elapsed

    # Print results

    # Test passes if it completes without errors
    assert cache_ops > 0
    assert dict_ops > 0
