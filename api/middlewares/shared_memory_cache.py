"""Shared memory cache implementation compatible with cachetools Cache API."""

from __future__ import annotations

import logging
import random
import struct
from collections.abc import MutableMapping
from marshal import dumps as key_dumps
from multiprocessing import RLock, shared_memory
from pickle import dumps as value_dumps
from pickle import loads as value_loads
from time import monotonic as time_monotonic
from typing import Any

from xxhash import xxh128_digest

logger = logging.getLogger(__name__)

# Constants for the cache structure
MAX_KEY_HASH_SIZE = 16  # MD5 hash
MAX_VALUE_SIZE = 10 * 1024 * 1024  # 10MB max per value
DEFAULT_CACHE_SIZE = 100 * 1024 * 1024  # 100MB default cache size

# Index slot structure: [key_hash (16 bytes), data_offset (4 bytes), timestamp (8 bytes)]
INDEX_SLOT_SIZE = 28  # 16 + 4 + 8

# Data entry structure: [value_length (4 bytes), value_data (variable)]
DATA_ENTRY_HEADER_SIZE = 4

# Metadata structure (256 bytes):
# - bytes 0-3: entry_count (uint32)
# - bytes 4-7: index_capacity (uint32) - number of slots in index
# - bytes 8-11: index_count (uint32) - number of used slots in index
# - bytes 12-15: data_offset (uint32) - current position in data section
# - bytes 16-19: index_offset (uint32) - where index section starts
# - bytes 20-23: data_start_offset (uint32) - where data section starts
# - bytes 24-27: lock_pid (uint32) - process ID holding the lock, 0 = unlocked
# - bytes 28-255: reserved
METADATA_SIZE = 256
FILL_FACTOR_THRESHOLD = 0.75  # Resize index when this full
INITIAL_INDEX_CAPACITY = 2**12

class SharedMemoryLRUCache(MutableMapping):
    """A shared memory LRU cache that implements the cachetools Cache API.

    This cache uses multiprocessing.shared_memory to share cache data across
    multiple worker processes, avoiding duplicate cache entries and improving
    cache hit rates.

    The cache uses a hash table index for O(1) lookups and stores values
    sequentially in a backing array. Access is synchronized using multiprocessing.RLock.
    """

    def __init__(
        self,
        *,
        lock: RLock,
        maxsize: int = 10_000,
        cache_size_bytes: int = DEFAULT_CACHE_SIZE,
        name: str | None = None,
    ) -> None:
        """Initialize the shared memory LRU cache.

        Args:
            maxsize: Maximum number of entries in the cache (for LRU eviction).
            cache_size_bytes: Size of the shared memory buffer in bytes.
            name: Optional name for the shared memory block. If None, a unique name is generated.
            lock: multiprocessing.RLock to use for synchronization. Required for proper
                synchronization across processes. The lock should be created in the main process
                and passed to all worker processes.
        """
        if lock is None:
            raise ValueError(
                "lock is required for SharedMemoryLRUCache. "
                "Create a multiprocessing.RLock in the main process and pass it to all worker processes."
            )
        self.maxsize = maxsize
        self.cache_size_bytes = cache_size_bytes
        self._lock = lock
        self._shm_name = name
        self._shm: shared_memory.SharedMemory = self._initialize_shared_memory()

    def _initialize_shared_memory(self) -> shared_memory.SharedMemory:
        """Initialize or attach to shared memory."""
        try:
            if self._shm_name:
                # Try to attach to existing shared memory
                try:
                    self._shm = shared_memory.SharedMemory(name=self._shm_name, create=False)
                    logger.info("Attached to existing shared memory: %s", self._shm_name)
                except FileNotFoundError:
                    # Create new shared memory
                    self._shm = shared_memory.SharedMemory(
                        name=self._shm_name,
                        create=True,
                        size=self.cache_size_bytes,
                    )
                    self._initialize_metadata()
                    logger.info("Created new shared memory: %s", self._shm_name)
                    return self._shm
            else:
                # Create anonymous shared memory
                self._shm = shared_memory.SharedMemory(create=True, size=self.cache_size_bytes)
                self._initialize_metadata()
                logger.info("Created anonymous shared memory: %s", self._shm.name)
                return self._shm
        except Exception as e:
            logger.error("Failed to initialize shared memory: %s", e, exc_info=True)
            raise

    def _initialize_metadata(self) -> None:
        """Initialize the metadata section of shared memory."""
        # Calculate initial index capacity (aim for ~2x maxsize to keep fill factor low)
        initial_index_capacity = max(INITIAL_INDEX_CAPACITY, (self.maxsize * 2).bit_length() * 2)
        initial_index_capacity = 1 << (initial_index_capacity.bit_length() - 1)  # Round to power of 2

        index_size = initial_index_capacity * INDEX_SLOT_SIZE
        index_offset = METADATA_SIZE
        data_start_offset = index_offset + index_size

        # Initialize metadata
        struct.pack_into(
            "IIIIII",
            self._shm.buf,
            0,
            0,  # entry_count
            initial_index_capacity,  # index_capacity
            0,  # index_count
            data_start_offset,  # data_offset (start of data section)
            index_offset,  # index_offset
            data_start_offset,  # data_start_offset
        )

        # Clear index (all zeros = empty slots)
        self._shm.buf[index_offset : index_offset + index_size] = b"\x00" * index_size

    def _get_metadata(self) -> tuple[int, int, int, int, int, int]:
        """Get metadata from shared memory.

        Returns:
            Tuple of (entry_count, index_capacity, index_count, data_offset, index_offset, data_start_offset).
        """
        return struct.unpack_from("IIIIII", self._shm.buf, 0)

    def _set_metadata(
        self,
        entry_count: int,
        index_capacity: int | None = None,
        index_count: int | None = None,
        data_offset: int | None = None,
    ) -> None:
        """Set metadata in shared memory.

        Args:
            entry_count: Number of entries in the cache.
            index_capacity: Capacity of index (None to keep current).
            index_count: Number of used index slots (None to keep current).
            data_offset: Current data offset (None to keep current).
        """
        current = self._get_metadata()
        struct.pack_into(
            "IIIIII",
            self._shm.buf,
            0,
            entry_count,
            index_capacity if index_capacity is not None else current[1],
            index_count if index_count is not None else current[2],
            data_offset if data_offset is not None else current[3],
            current[4],  # index_offset
            current[5],  # data_start_offset
        )

    def _hash_key(self, key: Any) -> bytes:
        """Hash a key to a fixed-size bytes object.

        Args:
            key: The key to hash.

        Returns:
            xxhash128 digest of the key as bytes (16 bytes).
        """
        try:
            return xxh128_digest(key)
        except TypeError:
            # Try to hash the key directly if it's bytes or string
            # For other types, serialize with marshal (faster than pickle)
            return xxh128_digest(key_dumps(key))

    def _index_hash(self, key_hash: bytes, capacity: int) -> int:
        """Hash a key hash to an index slot using modulo.

        Args:
            key_hash: The key hash.
            capacity: Index capacity.

        Returns:
            Initial slot index.
        """
        # Use first 4 bytes of hash as integer for modulo
        hash_int = struct.unpack_from("I", key_hash, 0)[0]
        return hash_int % capacity

    def _find_index_slot(self, key_hash: bytes) -> tuple[int, bool] | None:
        """Find an index slot for a key hash using linear probing.

        Args:
            key_hash: The key hash to find.

        Returns:
            Tuple of (slot_index, is_existing) if found, None if not found and table is full.
            is_existing is True if slot already contains this key, False if it's an empty slot.
        """
        _entry_count, index_capacity, _index_count, _data_offset, index_offset, _ = self._get_metadata()

        if index_capacity == 0:
            return None

        start_slot = self._index_hash(key_hash, index_capacity)
        slot = start_slot

        while True:
            slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)
            slot_key_hash = bytes(self._shm.buf[slot_offset : slot_offset + MAX_KEY_HASH_SIZE])

            # Check if slot is empty (all zeros)
            if all(b == 0 for b in slot_key_hash):
                return (slot, False)

            # Check if slot contains our key
            if slot_key_hash == key_hash:
                return (slot, True)

            # Linear probing: move to next slot
            slot = (slot + 1) % index_capacity

            # If we've wrapped around, table is full (shouldn't happen with proper resizing)
            if slot == start_slot:
                logger.warning("Index table full, cannot find slot")
                return None

    def _resize_index_if_needed(self) -> None:
        """Resize the index if fill factor exceeds threshold."""
        entry_count, index_capacity, index_count, data_offset, index_offset, data_start_offset = self._get_metadata()

        if index_capacity == 0:
            return

        fill_factor = index_count / index_capacity if index_capacity > 0 else 1.0

        if fill_factor < FILL_FACTOR_THRESHOLD:
            return

        # Double the capacity
        new_capacity = index_capacity * 2
        new_index_size = new_capacity * INDEX_SLOT_SIZE

        # Check if we have enough space
        new_index_offset = METADATA_SIZE
        new_data_start_offset = new_index_offset + new_index_size

        if new_data_start_offset + (data_offset - data_start_offset) > len(self._shm.buf):
            logger.warning("Not enough space to resize index")
            return

        # Create new index
        new_index_buf = bytearray(b"\x00" * new_index_size)

        # Rehash all existing entries
        old_index_offset = index_offset
        for old_slot in range(index_capacity):
            old_slot_offset = old_index_offset + (old_slot * INDEX_SLOT_SIZE)
            old_key_hash = bytes(self._shm.buf[old_slot_offset : old_slot_offset + MAX_KEY_HASH_SIZE])

            if all(b == 0 for b in old_key_hash):
                continue  # Empty slot

            # Find new slot
            new_slot = self._index_hash(old_key_hash, new_capacity)
            while new_index_buf[new_slot * INDEX_SLOT_SIZE] != 0:
                new_slot = (new_slot + 1) % new_capacity

            # Copy entry to new index
            old_entry = bytes(self._shm.buf[old_slot_offset : old_slot_offset + INDEX_SLOT_SIZE])
            new_slot_offset = new_slot * INDEX_SLOT_SIZE
            new_index_buf[new_slot_offset : new_slot_offset + INDEX_SLOT_SIZE] = old_entry

        # Move data section if needed and update data_offset values in index
        data_size = data_offset - data_start_offset
        offset_delta = new_data_start_offset - data_start_offset

        if new_data_start_offset != data_start_offset:
            # Move data to new location
            self._shm.buf[new_data_start_offset : new_data_start_offset + data_size] = self._shm.buf[
                data_start_offset : data_start_offset + data_size
            ]

            # Update all data_offset values in the new index
            for slot in range(new_capacity):
                slot_offset = slot * INDEX_SLOT_SIZE
                key_hash = bytes(new_index_buf[slot_offset : slot_offset + MAX_KEY_HASH_SIZE])

                if not all(b == 0 for b in key_hash):
                    # This slot has an entry - update its data_offset
                    old_data_offset = struct.unpack_from("I", new_index_buf, slot_offset + MAX_KEY_HASH_SIZE)[0]
                    new_data_offset = old_data_offset + offset_delta
                    struct.pack_into("I", new_index_buf, slot_offset + MAX_KEY_HASH_SIZE, new_data_offset)

        # Write new index
        self._shm.buf[new_index_offset : new_index_offset + new_index_size] = new_index_buf

        # Update metadata
        self._set_metadata(
            entry_count=entry_count,
            index_capacity=new_capacity,
            index_count=index_count,
            data_offset=new_data_start_offset + data_size,
        )

        logger.info("Resized index from %d to %d slots", index_capacity, new_capacity)

    def _find_entry(self, key_hash: bytes) -> tuple[int, float, int, int] | None:
        """Find an entry in the cache by key hash using the index.

        Args:
            key_hash: The hash of the key to find.

        Returns:
            Tuple of (data_offset, timestamp, value_length, slot) if found, None otherwise.
        """
        result = self._find_index_slot(key_hash)
        if result is None:
            return None

        slot, is_existing = result
        if not is_existing:
            return None

        _, _, _, _, index_offset, _ = self._get_metadata()
        slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)

        # Read data_offset and timestamp from index slot
        data_offset = struct.unpack_from("I", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE)[0]
        timestamp = struct.unpack_from("d", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE + 4)[0]

        # Read value_length from data section
        value_length = struct.unpack_from("I", self._shm.buf, data_offset)[0]

        return (
            data_offset,
            timestamp,
            value_length,
            slot,
        )

    def _read_value(self, data_offset: int, value_length: int) -> bytes:
        """Read a value from the data section.

        Args:
            data_offset: Offset in data section.
            value_length: Length of value.

        Returns:
            Value data as bytes.
        """
        return bytes(self._shm.buf[data_offset + DATA_ENTRY_HEADER_SIZE : data_offset + DATA_ENTRY_HEADER_SIZE + value_length])

    def _evict_lru(self) -> None:
        """Evict the least recently used entry from the cache."""
        entry_count, index_capacity, _index_count, _data_offset, index_offset, _ = self._get_metadata()
        if entry_count == 0:
            return

        # Find the entry with the oldest timestamp
        empty_hash = b"\x00" * MAX_KEY_HASH_SIZE
        timestamp_slot_pairs = []

        # If we have few entries, check all slots. Otherwise, sample for performance.
        if entry_count <= 20:
            # Check all slots
            slots_to_check = range(index_capacity)
        else:
            # Sample up to 20 random slots for performance
            slots_to_check = random.sample(population=range(index_capacity), k=min(20, index_capacity))

        for slot in slots_to_check:
            slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)
            key_hash = bytes(self._shm.buf[slot_offset : slot_offset + MAX_KEY_HASH_SIZE])

            if key_hash == empty_hash:
                continue  # Empty slot

            timestamp = struct.unpack_from("d", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE + 4)[0]
            timestamp_slot_pairs.append((timestamp, slot))

        if timestamp_slot_pairs:
            _oldest_timestamp, oldest_slot = min(timestamp_slot_pairs)
            self._remove_entry_at_slot(oldest_slot)

    def _remove_entry_at_slot(self, slot: int) -> None:
        """Remove an entry at a specific index slot.

        Args:
            slot: The index slot to remove.
        """
        entry_count, _index_capacity, index_count, _data_offset, index_offset, _data_start_offset = self._get_metadata()

        slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)
        key_hash = bytes(self._shm.buf[slot_offset : slot_offset + MAX_KEY_HASH_SIZE])

        if all(b == 0 for b in key_hash):
            return  # Already empty

        # Get data offset and value length
        entry_data_offset = struct.unpack_from("I", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE)[0]
        value_length = struct.unpack_from("I", self._shm.buf, entry_data_offset)[0]
        DATA_ENTRY_HEADER_SIZE + value_length

        # Clear index slot
        self._shm.buf[slot_offset : slot_offset + INDEX_SLOT_SIZE] = b"\x00" * INDEX_SLOT_SIZE

        # For now, we don't compact the data section (could add compaction later)
        # Just update metadata
        self._set_metadata(
            entry_count=entry_count - 1,
            index_count=index_count - 1,
        )

    def __getitem__(self, key: Any) -> Any:
        """Get an item from the cache.

        Args:
            key: The key to look up.

        Returns:
            The cached value.

        Raises:
            KeyError: If the key is not in the cache.
        """
        key_hash = self._hash_key(key)
        with self._lock:
            result = self._find_entry(key_hash)

            if result is None:
                raise KeyError(key)

            entry_data_offset, _timestamp, value_length, slot = result

            # Update access time (LRU) in index
            _, _, _, _, index_offset, _ = self._get_metadata()
            slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)
            struct.pack_into("d", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE + 4, time_monotonic())

            # Read and deserialize value
            value_data = self._read_value(entry_data_offset, value_length)
        try:
            return value_loads(value_data)
        except Exception as e:
            logger.error("Failed to deserialize cached value: %s", e, exc_info=True)
            # Remove corrupted entry
            self._remove_entry_at_slot(slot)
            raise KeyError(key) from e

    def __setitem__(self, key: Any, value: Any) -> None:
        """Set an item in the cache.

        Args:
            key: The key to store.
            value: The value to store.
        """
        with self._lock:
            # Check if we need to resize index
            self._resize_index_if_needed()

            key_hash = self._hash_key(key)

            # Serialize the value
            try:
                value_data = value_dumps(value)
            except Exception as e:
                logger.error("Failed to serialize value for cache: %s", e, exc_info=True)
                return

            value_length = len(value_data)

            if value_length > MAX_VALUE_SIZE:
                logger.warning("Value too large to cache: %d bytes (max: %d)", value_length, MAX_VALUE_SIZE)
                return

            DATA_ENTRY_HEADER_SIZE + value_length

            # Check if key already exists
            existing = self._find_entry(key_hash)
            if existing is not None:
                # Update existing entry
                old_data_offset, _, old_value_length, slot = existing

                if value_length <= old_value_length:
                    # New value fits in existing slot - update in place
                    struct.pack_into("I", self._shm.buf, old_data_offset, value_length)
                    self._shm.buf[
                        old_data_offset + DATA_ENTRY_HEADER_SIZE : old_data_offset + DATA_ENTRY_HEADER_SIZE + value_length
                    ] = value_data

                    # Update timestamp in index
                    _, _, _, _, index_offset, _ = self._get_metadata()
                    slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)
                    struct.pack_into("d", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE + 4, time_monotonic())
                else:
                    # Remove old entry and add new one
                    self._remove_entry_at_slot(slot)
                    self._add_entry(key_hash, value_data)
            else:
                # Check if we need to evict
                entry_count, _, _, _, _, _ = self._get_metadata()
                if entry_count >= self.maxsize:
                    self._evict_lru()

                # Add new entry
                self._add_entry(key_hash, value_data)

    def _add_entry(self, key_hash: bytes, value_data: bytes) -> None:
        """Add a new entry to the cache.

        Args:
            key_hash: The hash of the key.
            value_data: The serialized value data.
        """
        entry_count, _index_capacity, index_count, data_offset, index_offset, _data_start_offset = self._get_metadata()
        value_length = len(value_data)
        entry_size = DATA_ENTRY_HEADER_SIZE + value_length

        # Check if we have enough space
        if data_offset + entry_size > len(self._shm.buf):
            logger.warning("Cache full, cannot add entry")
            return

        # Find index slot
        slot_result = self._find_index_slot(key_hash)
        if slot_result is None:
            logger.warning("Cannot find index slot for entry")
            return

        slot, is_existing = slot_result
        if is_existing:
            logger.warning("Entry already exists in index")
            return

        # Write value to data section (append)
        struct.pack_into("I", self._shm.buf, data_offset, value_length)
        self._shm.buf[data_offset + DATA_ENTRY_HEADER_SIZE : data_offset + DATA_ENTRY_HEADER_SIZE + value_length] = (
            value_data
        )

        # Update index slot
        slot_offset = index_offset + (slot * INDEX_SLOT_SIZE)
        self._shm.buf[slot_offset : slot_offset + MAX_KEY_HASH_SIZE] = key_hash
        struct.pack_into("I", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE, data_offset)
        struct.pack_into("d", self._shm.buf, slot_offset + MAX_KEY_HASH_SIZE + 4, time_monotonic())

        # Update metadata
        self._set_metadata(
            entry_count=entry_count + 1,
            index_count=index_count + 1,
            data_offset=data_offset + entry_size,
        )

    def __delitem__(self, key: Any) -> None:
        """Delete an item from the cache.

        Args:
            key: The key to delete.

        Raises:
            KeyError: If the key is not in the cache.
        """
        with self._lock:
            key_hash = self._hash_key(key)
            slot_result = self._find_index_slot(key_hash)

            if slot_result is None or not slot_result[1]:
                raise KeyError(key)

            slot, _ = slot_result
            self._remove_entry_at_slot(slot)

    def __contains__(self, key: Any) -> bool:
        """Check if a key is in the cache.

        Args:
            key: The key to check.

        Returns:
            True if the key is in the cache, False otherwise.
        """
        with self._lock:
            key_hash = self._hash_key(key)
            result = self._find_index_slot(key_hash)
            return result is not None and result[1]

    def __len__(self) -> int:
        """Get the number of entries in the cache.

        Returns:
            The number of entries.
        """
        with self._lock:
            entry_count, _, _, _, _, _ = self._get_metadata()
            return entry_count

    def __iter__(self) -> Any:
        """Iterate over keys in the cache.

        Note: This is not fully implemented as we don't store the original keys,
        only their hashes. This would require storing keys separately.

        Yields:
            Keys in the cache.
        """
        msg = "Iteration not supported - keys are hashed and not stored"
        raise NotImplementedError(msg)

    def get(self, key: Any, default: Any = None) -> Any:
        """Get an item from the cache with a default value.

        Args:
            key: The key to look up.
            default: The default value to return if key is not found.

        Returns:
            The cached value or the default value.
        """
        try:
            return self[key]
        except KeyError:
            return default

    def clear(self) -> None:
        """Clear all entries from the cache."""
        with self._lock:
            self._initialize_metadata()

    def pop(self, key: Any, default: Any = None) -> Any:
        """Remove and return an item from the cache.

        Args:
            key: The key to remove.
            default: The default value to return if key is not found.

        Returns:
            The cached value or the default value.

        Raises:
            KeyError: If the key is not found and no default is provided.
        """
        with self._lock:
            try:
                value = self[key]
                del self[key]
                return value
            except KeyError:
                if default is not None:
                    return default
                raise

    def popitem(self) -> tuple[Any, Any]:
        """Remove and return the least recently used item.

        Returns:
            Tuple of (key, value) for the LRU item.

        Raises:
            KeyError: If the cache is empty.
        """
        msg = "popitem not supported - keys are hashed and not stored"
        raise NotImplementedError(msg)

    @property
    def currsize(self) -> int:
        """Get the current number of entries in the cache.

        Returns:
            The number of entries.
        """
        return len(self)

    def cache_info(self) -> Any:
        """Get cache statistics (compatible with cachetools API).

        Returns:
            A CacheInfo-like object with cache statistics.
        """
        from collections import namedtuple

        CacheInfo = namedtuple("CacheInfo", ["hits", "misses", "maxsize", "currsize"])
        # Note: We don't track hits/misses in the current implementation
        return CacheInfo(hits=0, misses=0, maxsize=self.maxsize, currsize=self.currsize)

    def close(self) -> None:
        """Close the shared memory connection."""
        if self._shm is not None:
            self._shm.close()

    def unlink(self) -> None:
        """Unlink (delete) the shared memory block."""
        if self._shm is not None:
            self._shm.close()
            if self._shm_name:
                try:
                    self._shm.unlink()
                except FileNotFoundError:
                    pass  # Already unlinked

    def __enter__(self) -> SharedMemoryLRUCache:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - unlinks the shared memory for cleanup."""
        self.unlink()
