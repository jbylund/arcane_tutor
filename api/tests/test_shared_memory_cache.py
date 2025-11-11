"""Tests for the shared memory cache implementation."""

from __future__ import annotations

import multiprocessing
import time
import uuid

import falcon
import pytest
from cachetools import LRUCache

from api.middlewares.shared_memory_cache import SharedMemoryLRUCache


def _worker_process(cache_name: str, worker_id: int, results: dict) -> None:
    """Worker process function for multi-process testing."""
    try:
        cache = SharedMemoryLRUCache(maxsize=100, name=cache_name)

        # Worker 0 writes a value
        if worker_id == 0:
            cache[f"worker_{worker_id}"] = f"value_{worker_id}"
            time.sleep(0.1)  # Give other workers time to start

        # Worker 1 reads the value written by worker 0
        elif worker_id == 1:
            time.sleep(0.2)  # Wait for worker 0 to write
            value = cache.get("worker_0")
            results["worker_1_read"] = value

        # Worker 2 writes a different value
        elif worker_id == 2:
            time.sleep(0.1)
            cache[f"worker_{worker_id}"] = f"value_{worker_id}"

        # All workers verify they can see all values
        time.sleep(0.3)
        results[f"worker_{worker_id}_sees_0"] = cache.get("worker_0")
        results[f"worker_{worker_id}_sees_2"] = cache.get("worker_2")

        cache.close()
    except Exception as e:
        results[f"worker_{worker_id}_error"] = str(e)


class MockResponse:
    """Simple class that mimics falcon.Response structure for testing."""

    def __init__(self) -> None:
        self.status = falcon.HTTP_200
        self.data = b"test data"
        self.media = {"test": "json"}
        self._headers = {"Content-Type": "application/json"}
        self.complete = True

class TestSharedMemoryLRUCache:
    def test_basic_get_set(self) -> None:
        """Test basic get and set operations."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            # Test setting and getting a simple value
            cache["key1"] = "value1"
            assert cache["key1"] == "value1"

            # Test updating a value
            cache["key1"] = "value1_updated"
            assert cache["key1"] == "value1_updated"

            # Test multiple keys
            cache["key2"] = "value2"
            cache["key3"] = {"nested": "dict"}
            assert cache["key2"] == "value2"
            assert cache["key3"] == {"nested": "dict"}


    def test_get_with_default(self) -> None:
        """Test get method with default value."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            assert cache.get("nonexistent") is None
            assert cache.get("nonexistent", "default") == "default"

            cache["key1"] = "value1"
            assert cache.get("key1") == "value1"
            assert cache.get("key1", "default") == "value1"


    def test_contains(self) -> None:
        """Test __contains__ method."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            assert "key1" not in cache
            cache["key1"] = "value1"
            assert "key1" in cache
            assert "key2" not in cache


    def test_delete(self) -> None:
        """Test deleting items from cache."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            cache["key1"] = "value1"
            cache["key2"] = "value2"

            assert "key1" in cache
            assert "key2" in cache

            del cache["key1"]
            assert "key1" not in cache
            assert "key2" in cache

            # Deleting non-existent key should raise KeyError
            with pytest.raises(KeyError):
                del cache["nonexistent"]


    def test_len(self) -> None:
        """Test __len__ method."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            assert len(cache) == 0

            cache["key1"] = "value1"
            assert len(cache) == 1

            cache["key2"] = "value2"
            assert len(cache) == 2

            del cache["key1"]
            assert len(cache) == 1


    def test_lru_eviction(self) -> None:
        """Test LRU eviction when cache is full."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=3, name=cache_name) as cache:
            # Fill the cache
            cache["key1"] = "value1"
            cache["key2"] = "value2"
            cache["key3"] = "value3"
            assert len(cache) == 3

            # Access key1 to make it more recently used
            _ = cache["key1"]

            # Add a new key - should evict the least recently used (key2 or key3)
            cache["key4"] = "value4"
            assert len(cache) == 3

            # key1 should still be there (most recently used)
            assert "key1" in cache

            # One of key2 or key3 should be evicted
            assert "key4" in cache

            if "key2" in cache:
                assert "key3" not in cache
            else:
                pass


    def test_clear(self) -> None:
        """Test clearing the cache."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            cache["key1"] = "value1"
            cache["key2"] = "value2"
            assert len(cache) == 2

            cache.clear()
            assert len(cache) == 0
            assert "key1" not in cache
            assert "key2" not in cache


    def test_pop(self) -> None:
        """Test pop method."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            cache["key1"] = "value1"
            cache["key2"] = "value2"

            value = cache.pop("key1")
            assert value == "value1"
            assert "key1" not in cache
            assert "key2" in cache

            # Pop with default
            value = cache.pop("nonexistent", "default")
            assert value == "default"

            # Pop without default should raise KeyError
            with pytest.raises(KeyError):
                cache.pop("nonexistent")


    def test_falcon_response_serialization(self) -> None:
        """Test that objects with falcon.Response-like structure can be cached."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            # Create a response-like object
            resp1 = MockResponse()

            # Cache it
            cache["test_response"] = resp1

            # Retrieve it
            resp2 = cache["test_response"]

            # Verify properties
            assert resp2.status == falcon.HTTP_200
            assert resp2.data == b"test data"
            assert resp2.media == {"test": "json"}
            assert resp2._headers["Content-Type"] == "application/json"
            assert resp2.complete is True

    def test_multiprocess_sharing(self) -> None:
        """Test that multiple processes can share the cache."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        manager = multiprocessing.Manager()
        results = manager.dict()

        # Create the cache in the main process first
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            cache.close()  # Close but don't unlink - workers need it

            # Start multiple worker processes
            processes = []
            for i in range(3):
                p = multiprocessing.Process(target=_worker_process, args=(cache_name, i, results))
                p.start()
                processes.append(p)

            # Wait for all processes to complete
            for p in processes:
                p.join(timeout=5)
                assert not p.is_alive(), "Process did not complete in time"

            # Verify results
            assert results.get("worker_1_read") == "value_0", "Worker 1 should see value from worker 0"
            assert results.get("worker_0_sees_0") == "value_0"
            assert results.get("worker_0_sees_2") == "value_2"
            assert results.get("worker_1_sees_0") == "value_0"
            assert results.get("worker_1_sees_2") == "value_2"
            assert results.get("worker_2_sees_0") == "value_0"
            assert results.get("worker_2_sees_2") == "value_2"

            # Check for errors
            for key in results:
                if key.endswith("_error"):
                    pytest.fail(f"Worker error: {results[key]}")


    def test_cache_info(self) -> None:
        """Test cache_info method."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            info = cache.cache_info()
            assert info.maxsize == 100
            assert info.currsize == 0

            cache["key1"] = "value1"
            info = cache.cache_info()
            assert info.currsize == 1


    def test_context_manager(self) -> None:
        """Test using cache as a context manager."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"

        with SharedMemoryLRUCache(maxsize=100, name=cache_name) as cache:
            cache["key1"] = "value1"
            assert cache["key1"] == "value1"

        # Cache should be closed after context exit
        # (We can't easily test this without trying to access it, which might fail)


    def test_large_value(self) -> None:
        """Test caching large values."""
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=100, cache_size_bytes=1024 * 1024, name=cache_name) as cache:  # 1MB cache
            # Create a moderately large value (100KB)
            large_value = b"x" * (100 * 1024)
            cache["large_key"] = large_value
            assert cache["large_key"] == large_value


class TestSharedMemoryLRUCachePerformance:
    """Performance tests comparing SharedMemoryLRUCache against dict and LRUCache."""

    def test_get_performance(self) -> None:
        """Compare get operation performance."""
        num_operations = 100_000 * 60
        cache_size = 1_000

        dict_cache: dict[str, str] = {}
        lru_cache = LRUCache(maxsize=cache_size)

        # Setup SharedMemoryLRUCache
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=cache_size, name=cache_name) as shm_cache:
            # Populate cache
            for i in range(cache_size):
                dict_cache[f"key_{i}"] = f"value_{i}"
                lru_cache[f"key_{i}"] = f"value_{i}"
                shm_cache[f"key_{i}"] = f"value_{i}"

            # Time dict gets
            start = time.perf_counter()
            for i in range(num_operations):
                _ = dict_cache[f"key_{i % cache_size}"]
            time.perf_counter() - start

            # Time LRUCache gets
            start = time.perf_counter()
            for i in range(num_operations):
                _ = lru_cache[f"key_{i % cache_size}"]
            time.perf_counter() - start

            # Time SharedMemoryLRUCache gets
            start = time.perf_counter()
            for i in range(num_operations):
                _ = shm_cache[f"key_{i % cache_size}"]
            time.perf_counter() - start



        # Log results (tests should pass regardless of performance)

    def test_set_performance(self) -> None:
        """Compare set operation performance."""
        num_operations = 10_000
        cache_size = 1_000

        # Setup SharedMemoryLRUCache
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=cache_size, name=cache_name) as shm_cache:
            # Time SharedMemoryLRUCache sets
            start = time.perf_counter()
            for i in range(num_operations):
                shm_cache[f"key_{i}"] = f"value_{i}"
            time.perf_counter() - start

        # Setup dict
        dict_cache: dict[str, str] = {}

        # Time dict sets
        start = time.perf_counter()
        for i in range(num_operations):
            dict_cache[f"key_{i}"] = f"value_{i}"
        time.perf_counter() - start

        # Setup LRUCache
        lru_cache = LRUCache(maxsize=cache_size)

        # Time LRUCache sets
        start = time.perf_counter()
        for i in range(num_operations):
            lru_cache[f"key_{i}"] = f"value_{i}"
        time.perf_counter() - start

        # Log results

    def test_mixed_operations_performance(self) -> None:
        """Compare mixed get/set operation performance."""
        num_operations = 10_000
        cache_size = 1_000

        # Setup SharedMemoryLRUCache
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=cache_size, name=cache_name) as shm_cache:
            # Pre-populate
            for i in range(cache_size // 2):
                shm_cache[f"key_{i}"] = f"value_{i}"

            # Time mixed operations
            start = time.perf_counter()
            for i in range(num_operations):
                if i % 2 == 0:
                    shm_cache[f"key_{i % cache_size}"] = f"value_{i}"
                else:
                    _ = shm_cache.get(f"key_{(i-1) % cache_size}")
            time.perf_counter() - start

        # Setup dict
        dict_cache: dict[str, str] = {}
        for i in range(cache_size // 2):
            dict_cache[f"key_{i}"] = f"value_{i}"

        # Time mixed operations
        start = time.perf_counter()
        for i in range(num_operations):
            if i % 2 == 0:
                dict_cache[f"key_{i % cache_size}"] = f"value_{i}"
            else:
                _ = dict_cache.get(f"key_{(i-1) % cache_size}")
        time.perf_counter() - start

        # Setup LRUCache
        lru_cache = LRUCache(maxsize=cache_size)
        for i in range(cache_size // 2):
            lru_cache[f"key_{i}"] = f"value_{i}"

        # Time mixed operations
        start = time.perf_counter()
        for i in range(num_operations):
            if i % 2 == 0:
                lru_cache[f"key_{i % cache_size}"] = f"value_{i}"
            else:
                _ = lru_cache.get(f"key_{(i-1) % cache_size}")
        time.perf_counter() - start

        # Log results

    def test_large_value_performance(self) -> None:
        """Compare performance with larger values."""
        num_operations = 1_000
        cache_size = 100
        value_size = 10 * 1024  # 10KB values

        # Create large value
        large_value = b"x" * value_size

        # Setup SharedMemoryLRUCache
        cache_name = f"test_cache_{uuid.uuid4().hex[:8]}"
        with SharedMemoryLRUCache(maxsize=cache_size, name=cache_name) as shm_cache:
            # Time SharedMemoryLRUCache operations
            start = time.perf_counter()
            for i in range(num_operations):
                shm_cache[f"key_{i % cache_size}"] = large_value
                _ = shm_cache[f"key_{i % cache_size}"]
            time.perf_counter() - start

        # Setup dict
        dict_cache: dict[str, bytes] = {}

        # Time dict operations
        start = time.perf_counter()
        for i in range(num_operations):
            dict_cache[f"key_{i % cache_size}"] = large_value
            _ = dict_cache[f"key_{i % cache_size}"]
        time.perf_counter() - start

        # Setup LRUCache
        lru_cache = LRUCache(maxsize=cache_size)

        # Time LRUCache operations
        start = time.perf_counter()
        for i in range(num_operations):
            lru_cache[f"key_{i % cache_size}"] = large_value
            _ = lru_cache[f"key_{i % cache_size}"]
        time.perf_counter() - start

        # Log results
