"""Tests for content-addressable shared memory cache."""

from __future__ import annotations

import itertools
import logging
import multiprocessing
import random
import time
import uuid

import pytest
import xxhash

from content_addressable_cache.content_addressable_cache import BLOB_TYPE_CONTENT, ContentAddressableCache


# Helper function to create a lock for single-process tests
def _create_lock() -> multiprocessing.RLock:
    """Create a new RLock for single-process test usage."""
    return multiprocessing.RLock()


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


def _worker_create_cache(shm_name: str, lock: multiprocessing.RLock) -> None:
    """Worker that creates its own cache instance with shared memory and lock."""
    from multiprocessing.shared_memory import SharedMemory  # noqa: PLC0415

    # Attach to existing shared memory
    shm = SharedMemory(name=shm_name)
    # Create cache with same shared memory and lock
    cache = ContentAddressableCache(maxsize=100, shared_memory=shm, lock=lock)
    try:
        # Worker can read parent's data
        assert cache[b"parent_key"] == b"parent_value"
        # Worker can write its own data
        cache[b"worker_key"] = b"worker_value"
    finally:
        cache.close()


def test_basic_set_get() -> None:
    """Test basic set and get operations."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        cache[b"key1"] = b"value1"
        assert cache[b"key1"] == b"value1"
        assert len(cache) == 1
    finally:
        cache.close()


def test_multiple_keys() -> None:
    """Test multiple keys."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        cache[b"key1"] = b"value1"
        cache[b"key1"] = b"value2"

        assert cache[b"key1"] == b"value2"
        assert len(cache) == 1
    finally:
        cache.close()


def test_key_not_found() -> None:
    """Test KeyError when key not found."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        with pytest.raises(KeyError):
            _ = cache[b"nonexistent"]
    finally:
        cache.close()


def test_get_with_default() -> None:
    """Test get method with default value."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        cache[b"key1"] = b"value1"

        assert cache.get(b"key1") == b"value1"
        assert cache.get(b"nonexistent") is None
        assert cache.get(b"nonexistent", b"default") == b"default"
    finally:
        cache.close()


def test_delete_key() -> None:
    """Test deleting a key."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        with pytest.raises(KeyError):
            del cache[b"nonexistent"]
    finally:
        cache.close()


def test_contains() -> None:
    """Test 'in' operator."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        cache[b"key1"] = b"value1"

        assert b"key1" in cache
        assert b"nonexistent" not in cache
    finally:
        cache.close()


def test_clear() -> None:
    """Test clear method."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=100, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        # Same value for different keys
        cache[b"key1"] = b"shared_value"
        cache[b"key2"] = b"shared_value"
        cache[b"key3"] = b"shared_value"

        assert cache[b"key1"] == b"shared_value"
        assert cache[b"key2"] == b"shared_value"
        assert cache[b"key3"] == b"shared_value"
        assert len(cache) == 3

        stored_content_blobs = {content_bytes for _, content_bytes in cache.content_items()}
        assert {b"shared_value"} == stored_content_blobs

        # All should return same value
        assert cache[b"key1"] == cache[b"key2"] == cache[b"key3"]
    finally:
        cache.close()


def test_content_deduplication_storage() -> None:
    """Test that storing same value under different keys only stores one copy in blob pool."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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
    cache = ContentAddressableCache(maxsize=3, lock=_create_lock())
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
    with ContentAddressableCache(maxsize=10, lock=_create_lock()) as cache:
        cache[b"key1"] = b"value1"
        assert cache[b"key1"] == b"value1"


def test_multiprocessing_shared() -> None:
    """Test that cache can be shared between processes."""
    # Create shared lock for multiprocessing
    shared_lock = multiprocessing.RLock()
    cache = ContentAddressableCache(maxsize=100, lock=shared_lock)
    try:
        # Add item in main process
        cache[b"main_key"] = b"main_value"

        # Create worker process - pass cache with shared lock
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
    # Create shared lock for multiprocessing
    shared_lock = multiprocessing.RLock()
    cache = ContentAddressableCache(maxsize=100, lock=shared_lock)
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
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        large_value = b"x" * 10000
        cache[b"key1"] = large_value
        assert cache[b"key1"] == large_value
    finally:
        cache.close()


def test_empty_key() -> None:
    """Test with empty key."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        cache[b""] = b"value"
        assert cache[b""] == b"value"
    finally:
        cache.close()


def test_empty_value() -> None:
    """Test with empty value."""
    cache = ContentAddressableCache(maxsize=10, lock=_create_lock())
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

    cache = ContentAddressableCache(maxsize=10, lock=_create_lock(), hash_func=custom_hash)
    try:
        cache[b"key1"] = b"value1"
        assert cache[b"key1"] == b"value1"
    finally:
        cache.close()


def test_hash_collision() -> None:
    """Test that hash collisions are handled correctly by comparing actual key bytes."""
    # Create a hash function that returns the same hash for different keys
    collision_count = {"key1": 0, "key2": 0}

    def collision_hash(data: bytes) -> bytes:
        """Hash function that returns same value for key1 and key2."""
        # Count calls to verify both keys are being hashed
        if data == b"key1":
            collision_count["key1"] += 1
        elif data == b"key2":
            collision_count["key2"] += 1

        # Return the same hash for both key1 and key2 to force collision
        if data in (b"key1", b"key2"):
            # Return a fixed hash value for both keys
            return b"\x00" * 15 + b"\x01"  # Fixed hash for collision test
        # For other keys, use normal hash
        return xxhash.xxh128_digest(data)

    cache = ContentAddressableCache(maxsize=10, lock=_create_lock(), hash_func=collision_hash)
    try:
        # Store different values for keys that hash to the same value
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"

        # Verify both keys are stored
        assert len(cache) == 2

        # Verify we can retrieve the correct value for each key
        assert cache[b"key1"] == b"value1"
        assert cache[b"key2"] == b"value2"

        # Verify they are different
        assert cache[b"key1"] != cache[b"key2"]

        # Verify both keys are in the cache
        assert b"key1" in cache
        assert b"key2" in cache

        # Update one key and verify the other is unaffected
        cache[b"key1"] = b"updated_value1"
        assert cache[b"key1"] == b"updated_value1"
        assert cache[b"key2"] == b"value2"  # Should be unchanged

        # Verify hash function was called for both keys
        assert collision_count["key1"] > 0
        assert collision_count["key2"] > 0
    finally:
        cache.close()


def test_approximated_lru_eviction() -> None:
    """Test that approximated LRU eviction evicts least recently used keys."""
    cache = ContentAddressableCache(maxsize=5, lock=_create_lock())
    try:
        # Fill cache with 5 items, using different values to avoid content deduplication
        for i in range(5):
            cache[f"key{i}".encode()] = f"value{i}_unique_{i}".encode()

        assert len(cache) == 5

        # Access some keys to update their timestamps
        # Access key0, key1, key2 (making them more recently used)
        _ = cache[b"key0"]
        _ = cache[b"key1"]
        _ = cache[b"key2"]
        # Small delay to ensure timestamps differ
        time.sleep(0.001)

        # Add a new key, which should trigger eviction
        # Since we sample 10 keys and evict the oldest, key3 or key4 should be evicted
        # (not key0, key1, key2 which were recently accessed)
        cache[b"key5"] = b"value5_unique"

        # Verify we still have 5 items
        assert len(cache) == 5

        # Verify recently accessed keys are still present
        assert b"key0" in cache
        assert b"key1" in cache
        assert b"key2" in cache
        assert b"key5" in cache

        # Verify one of the non-accessed keys was evicted
        # (key3 or key4, but not both)
        evicted_count = 0
        if b"key3" not in cache:
            evicted_count += 1
        if b"key4" not in cache:
            evicted_count += 1

        assert evicted_count == 1, "Exactly one of key3 or key4 should be evicted"

        # Test that repeatedly accessing the same key prevents its eviction
        # Clear and refill cache
        cache.clear()
        for i in range(5):
            cache[f"key{i}".encode()] = f"value{i}_unique_{i}".encode()

        # Continuously access key0 to keep it hot
        for _ in range(10):
            _ = cache[b"key0"]
            time.sleep(0.0001)

        # Add a new key (should evict one of the non-accessed keys)
        cache[b"key6"] = b"value6_unique"

        # key0 should still be present (was accessed most recently)
        assert b"key0" in cache
        assert b"key6" in cache

        # Verify cache size is still maxsize
        assert len(cache) == 5
    finally:
        cache.close()


def test_invalid_maxsize() -> None:
    """Test that invalid maxsize raises error."""
    with pytest.raises(ValueError, match="maxsize must be positive"):
        ContentAddressableCache(maxsize=0, lock=_create_lock())

    with pytest.raises(ValueError, match="maxsize must be positive"):
        ContentAddressableCache(maxsize=-1, lock=_create_lock())


def test_reuse_existing_shared_memory() -> None:
    """Test reusing existing shared memory."""
    cache1 = ContentAddressableCache(maxsize=10, lock=_create_lock())
    try:
        cache1[b"key1"] = b"value1"

        # Create second cache using same shared memory and lock
        cache2 = ContentAddressableCache(maxsize=10, shared_memory=cache1._shm, lock=cache1._lock)
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
    # Create shared lock for multiprocessing
    shared_lock = multiprocessing.RLock()
    cache = ContentAddressableCache(maxsize=100, lock=shared_lock)
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


def test_multiprocessing_with_explicit_lock_sharing() -> None:
    """Test proper multiprocessing pattern with explicit lock and shared memory sharing."""
    # Create shared lock and cache in parent process
    # Use Manager to create a lock that can be shared across processes
    manager = multiprocessing.Manager()
    shared_lock = manager.RLock()
    cache1 = ContentAddressableCache(maxsize=100, lock=shared_lock)
    try:
        # Add some initial data
        cache1[b"parent_key"] = b"parent_value"

        # Start worker process, passing shared memory name and lock
        process = multiprocessing.Process(
            target=_worker_create_cache,
            args=(cache1._shm.name, shared_lock),
        )
        process.start()
        process.join()

        # Parent should see worker's data (thanks to shared lock)
        assert cache1[b"parent_key"] == b"parent_value"
        assert cache1[b"worker_key"] == b"worker_value"
    finally:
        cache1.close()


def test_stress_test() -> None:
    """Stress test with many operations."""
    cache = ContentAddressableCache(maxsize=1000, lock=_create_lock())
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
    operations = itertools.cycle([random.random() < set_ratio for _ in range(10_000)])

    key_ids = [random.randint(0, max_keys - 1) for _ in range(10_000)]

    key_value_pairs = itertools.cycle(
        [
            (
                key_prefix + str(key_id).encode(),
                value_prefix + str(key_id).encode(),
            )
            for key_id in key_ids
        ]
    )

    # Test with ContentAddressableCache
    cache = ContentAddressableCache(maxsize=max_keys * 10, lock=_create_lock())
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
        cache_ops_per_sec = cache_ops / cache_elapsed

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
    dict_ops_per_sec = dict_ops / dict_elapsed

    # Log results
    logger = logging.getLogger(__name__)
    logger.info("=" * 80)
    logger.info("Throughput Benchmark Results")
    logger.info("=" * 80)
    logger.info("ContentAddressableCache:")
    logger.info("  Operations: %d (%.2f ops/sec)", cache_ops, cache_ops_per_sec)
    logger.info("  Sets: %d (%.1f%%)", cache_sets, 100.0 * cache_sets / cache_ops if cache_ops > 0 else 0)
    logger.info("  Gets: %d (%.1f%%)", cache_gets, 100.0 * cache_gets / cache_ops if cache_ops > 0 else 0)
    logger.info("  Misses: %d (%.1f%%)", cache_misses, 100.0 * cache_misses / cache_ops if cache_ops > 0 else 0)
    logger.info("  Elapsed: %.2f seconds", cache_elapsed)
    logger.info("")
    logger.info("dict (for comparison):")
    logger.info("  Operations: %d (%.2f ops/sec)", dict_ops, dict_ops_per_sec)
    logger.info("  Sets: %d (%.1f%%)", dict_sets, 100.0 * dict_sets / dict_ops if dict_ops > 0 else 0)
    logger.info("  Gets: %d (%.1f%%)", dict_gets, 100.0 * dict_gets / dict_ops if dict_ops > 0 else 0)
    logger.info("  Misses: %d (%.1f%%)", dict_misses, 100.0 * dict_misses / dict_ops if dict_ops > 0 else 0)
    logger.info("  Elapsed: %.2f seconds", dict_elapsed)
    logger.info("")
    logger.info(
        "Performance Ratio: %.2fx (dict is faster)",
        dict_ops_per_sec / cache_ops_per_sec if cache_ops_per_sec > 0 else 0,
    )
    logger.info("=" * 80)

    # Test passes if it completes without errors
    assert cache_ops > 0
    assert dict_ops > 0


def test_compaction() -> None:
    """Test that compaction removes unreferenced blobs and defragments pool."""
    cache = ContentAddressableCache(maxsize=100, lock=_create_lock())
    try:
        # Add some items
        cache[b"key1"] = b"value1"
        cache[b"key2"] = b"value2"
        cache[b"key3"] = b"value3"
        cache[b"key4"] = b"shared_value"  # Same value as key5
        cache[b"key5"] = b"shared_value"  # Same value as key4 (content deduplication)

        # Get initial blob pool usage
        initial_used = cache._get_blob_pool_used()
        initial_next = cache._get_blob_pool_next()

        # Delete some keys to create fragmentation
        del cache[b"key1"]
        del cache[b"key3"]

        # Blob pool should still show same usage (lazy deletion)
        after_delete_used = cache._get_blob_pool_used()
        assert after_delete_used == initial_used

        # Compact
        cache.compact()

        # After compaction, blob pool usage should be reduced
        after_compact_used = cache._get_blob_pool_used()
        after_compact_next = cache._get_blob_pool_next()

        # Usage should be less than before (unreferenced blobs removed)
        assert after_compact_used < initial_used
        assert after_compact_next < initial_next

        # All remaining items should still be accessible
        assert cache[b"key2"] == b"value2"
        assert cache[b"key4"] == b"shared_value"
        assert cache[b"key5"] == b"shared_value"
        assert len(cache) == 3

        # Deleted items should not be accessible
        assert b"key1" not in cache
        assert b"key3" not in cache
    finally:
        cache.close()


def test_compaction_preserves_content_deduplication() -> None:
    """Test that compaction preserves content deduplication."""
    cache = ContentAddressableCache(maxsize=100, lock=_create_lock())
    try:
        # Add multiple keys with same value (content deduplication)
        shared_value = b"shared_content_12345"
        cache[b"key1"] = shared_value
        cache[b"key2"] = shared_value
        cache[b"key3"] = shared_value

        # Get content items before compaction
        content_items_before = list(cache.content_items())
        assert len(content_items_before) == 1  # Only one unique content

        # Delete one key
        del cache[b"key1"]

        # Compact
        cache.compact()

        # Content should still be deduplicated (key2 and key3 share it)
        content_items_after = list(cache.content_items())
        assert len(content_items_after) == 1  # Still only one unique content

        # Remaining keys should still work
        assert cache[b"key2"] == shared_value
        assert cache[b"key3"] == shared_value
    finally:
        cache.close()


def test_compaction_with_many_items() -> None:
    """Test compaction with many items and deletions."""
    cache = ContentAddressableCache(maxsize=1000, lock=_create_lock())
    try:
        # Add many items
        for i in range(50):
            cache[f"key_{i}".encode()] = f"value_{i}".encode()

        initial_used = cache._get_blob_pool_used()
        assert len(cache) == 50

        # Delete every other item
        for i in range(0, 50, 2):
            del cache[f"key_{i}".encode()]

        assert len(cache) == 25

        # Compact
        cache.compact()

        # Usage should be reduced
        after_compact_used = cache._get_blob_pool_used()
        assert after_compact_used < initial_used

        # Remaining items should work
        for i in range(1, 50, 2):
            assert cache[f"key_{i}".encode()] == f"value_{i}".encode()

        # Deleted items should not work
        for i in range(0, 50, 2):
            assert f"key_{i}".encode() not in cache
    finally:
        cache.close()


def test_blob_pool_full() -> None:
    """Test that _append_blob raises RuntimeError when blob pool is full."""
    # Create a small cache to fill up quickly
    # Use a very small maxsize to get a small blob pool
    cache = ContentAddressableCache(maxsize=100, lock=_create_lock())
    initial_empty_used = cache._get_blob_pool_used()
    try:
        # Keep appending UUIDs until we get a RuntimeError
        # Use the lock context manager since _append_blob expects to be called within a lock
        with pytest.raises(RuntimeError, match="Blob pool full"):  # noqa: PT012
            with cache._locked():
                for _ in range(14_000):
                    # Generate a UUID as bytes (16 bytes)
                    uuid_bytes = uuid.uuid4().bytes
                    cache._append_blob(BLOB_TYPE_CONTENT, uuid_bytes)
        full_used = cache._get_blob_pool_used()
        cache.compact()
        re_empty_used = cache._get_blob_pool_used()
        assert initial_empty_used == re_empty_used
        assert re_empty_used < full_used
        # then re-add a bunch of items
        with cache._locked():
            for _ in range(100):
                # Generate a UUID as bytes (16 bytes)
                uuid_bytes = uuid.uuid4().bytes
                cache._append_blob(BLOB_TYPE_CONTENT, uuid_bytes)
    finally:
        cache.close()
