"""Content-addressable shared memory cache implementation.

This module provides a shared memory cache with content deduplication using
multiprocessing.SharedMemory and content-addressable storage.
"""

from __future__ import annotations

import logging
import random
import struct
import time
from contextlib import contextmanager
from multiprocessing.shared_memory import SharedMemory
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from multiprocessing import RLock
    from types import TracebackType

import xxhash

logger = logging.getLogger(__name__)


class CouldNotLockError(Exception):
    """Exception raised when lock acquisition fails or times out."""


# Constants
# Magic number to validate shared memory contains our cache structure
# Randomly generated: 4AB8_6639_3C4D, last 2 bytes (4 hex digits) reserved for version
MAGIC_BASE = 0x4AB8_6639_3C4D_0000
VERSION = 1
MAGIC = MAGIC_BASE | VERSION  # 0x4AB8_6639_3C4D_0001
HEADER_SIZE = 512
KEY_HASH_WIDTH = 16  # 128-bit key hash in bytes
CONTENT_FP_WIDTH = 16  # 128-bit content fingerprint in bytes
ADDRESS_WIDTH = 8  # 64-bit address in bytes
TIMESTAMP_WIDTH = 8  # 64-bit timestamp in bytes (nanoseconds since epoch)
KEY_HASH_ENTRY_SIZE = KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH + TIMESTAMP_WIDTH  # 16 + 8 + 16 + 8 = 48
CONTENT_FP_ENTRY_SIZE = CONTENT_FP_WIDTH + ADDRESS_WIDTH  # 16 + 8 = 24
DEFAULT_LRU_SAMPLES = 10  # Number of keys to sample for approximated LRU eviction
BLOB_TYPE_KEY = 0x01
BLOB_TYPE_CONTENT = 0x02
BLOB_TYPE_WIDTH = 1  # 1 byte for blob type discriminator
BLOB_LENGTH_WIDTH = 4  # 4 bytes (uint32) for blob length
BLOB_HEADER_SIZE = BLOB_TYPE_WIDTH + BLOB_LENGTH_WIDTH  # 5 bytes total header
# Empty slot markers for hash tables
EMPTY_KEY_HASH = b"\x00" * KEY_HASH_WIDTH  # 16 bytes of 0x00
EMPTY_CONTENT_FP = b"\x00" * CONTENT_FP_WIDTH  # 16 bytes of 0x00
# Tombstone marker for deleted hash table entries (all 0xFF bytes)
TOMBSTONE = b"\xFF" * KEY_HASH_WIDTH  # 16 bytes of 0xFF
ALIGNMENT = 8
DEFAULT_LOAD_FACTOR = 0.65
DEFAULT_LOCK_TIMEOUT = 60.0


def _extract_version_from_magic(magic: int) -> int:
    """Extract version from magic number (lower 2 bytes)."""
    return magic & 0xFFFF


def _align(size: int, alignment: int = ALIGNMENT) -> int:
    """Round up size to alignment boundary."""
    return (size + alignment - 1) & ~(alignment - 1)


def _default_hash(data: bytes) -> bytes:
    """Default hash function using xxhash.

    Returns:
        128-bit hash as bytes.
    """
    return xxhash.xxh128_digest(data)


class ContentAddressableCache:
    """Content-addressable shared memory cache.

    This cache stores keys and values as bytes in shared memory with content
    deduplication. Multiple keys can share the same content (value).

    Attributes:
        maxsize: Maximum number of cache entries.
        load_factor: Hash table load factor threshold (default 0.65).
        hash_func: Hash function for keys and content (default xxhash).
        lock_timeout: Timeout for lock acquisition in seconds (default 60.0).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        maxsize: int,
        lock: RLock,
        shared_memory: SharedMemory | None = None,
        load_factor: float = DEFAULT_LOAD_FACTOR,
        hash_func: Callable[[bytes], bytes] | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
        avg_key_size: int = 200,
        avg_value_size: int = 2000,
    ) -> None:
        """Initialize the cache.

        Args:
            maxsize: Maximum number of cache entries.
            lock: RLock to use for synchronization. Required for proper cross-process
                synchronization. For multiprocessing scenarios where multiple processes
                share the same SharedMemory, pass the same lock instance to all cache
                instances to ensure proper cross-process synchronization.
            shared_memory: Existing SharedMemory to use, or None to create new.
            load_factor: Hash table load factor threshold (0.0-1.0).
            hash_func: Hash function returning 128-bit hash as bytes.
            lock_timeout: Timeout for lock acquisition in seconds.
            avg_key_size: Average size of a key in bytes.
            avg_value_size: Average size of a value in bytes.
        """
        if maxsize <= 0:
            msg = "maxsize must be positive"
            raise ValueError(msg)
        if lock is None:
            msg = "lock must be provided"
            raise ValueError(msg)

        self.maxsize = maxsize
        self.load_factor = load_factor
        self.hash_func = hash_func or _default_hash
        self.lock_timeout = lock_timeout
        self._lock = lock

        # Calculate sizes
        max_items = maxsize
        num_slots = int(max_items / load_factor)
        key_table_size = num_slots * KEY_HASH_ENTRY_SIZE
        content_table_size = num_slots * CONTENT_FP_ENTRY_SIZE
        blob_pool_size = int(max_items * (avg_key_size + avg_value_size))
        total_size = HEADER_SIZE + key_table_size + content_table_size + blob_pool_size

        if shared_memory is None:
            # Create new shared memory
            self._shm = SharedMemory(create=True, size=total_size)
            self._owned_shm = True
            self._initialize_memory()
        else:
            # Use existing shared memory
            self._shm = shared_memory
            self._owned_shm = False
            self._validate_memory()

        # Set up memory views
        self._setup_memory_views()

    def _initialize_memory(self) -> None:
        """Initialize the shared memory with header and metadata."""
        buf = memoryview(self._shm.buf)
        # Write magic and version
        struct.pack_into(">Q", buf, 0, MAGIC)
        struct.pack_into(">I", buf, 8, VERSION)
        struct.pack_into(">I", buf, 12, 0)  # segment_version
        # Initialize header fields
        struct.pack_into(">Q", buf, 16, len(self._shm.buf))  # total_size
        # Blob pool
        blob_start = HEADER_SIZE
        blob_size = len(self._shm.buf) - HEADER_SIZE - self._get_key_table_size() - self._get_content_table_size()
        struct.pack_into(">Q", buf, 24, blob_start)  # blob_pool_start
        struct.pack_into(">Q", buf, 32, blob_size)  # blob_pool_size
        struct.pack_into(">Q", buf, 40, 0)  # blob_pool_used
        struct.pack_into(">Q", buf, 48, blob_start)  # blob_pool_next
        # Hash tables
        key_table_start = blob_start + blob_size
        key_table_size = self._get_key_table_size()
        struct.pack_into(">Q", buf, 56, key_table_start)  # key_hash_table_start
        struct.pack_into(">Q", buf, 64, int(key_table_size / KEY_HASH_ENTRY_SIZE))  # key_hash_table_size
        content_table_start = key_table_start + key_table_size
        content_table_size = self._get_content_table_size()
        struct.pack_into(">Q", buf, 72, content_table_start)  # content_fp_table_start
        struct.pack_into(">Q", buf, 80, int(content_table_size / CONTENT_FP_ENTRY_SIZE))  # content_fp_table_size
        # Counters
        struct.pack_into(">Q", buf, 88, self.maxsize)  # max_items
        struct.pack_into(">Q", buf, 96, 0)  # current_items

        # Initialize hash tables to empty (all zeros)
        key_table_buf = buf[key_table_start : key_table_start + key_table_size]
        key_table_buf[:] = b"\x00" * key_table_size
        content_table_buf = buf[content_table_start : content_table_start + content_table_size]
        content_table_buf[:] = b"\x00" * content_table_size

    def _validate_memory(self) -> None:
        """Validate that shared memory has correct magic and version."""
        buf = memoryview(self._shm.buf)
        magic = struct.unpack_from(">Q", buf, 0)[0]
        magic_base = magic & 0xFFFF_FFFF_FFFF_0000  # Upper 6 bytes
        version = _extract_version_from_magic(magic)

        if magic_base != MAGIC_BASE:
            msg = f"Invalid magic number: {magic:#x} (expected base {MAGIC_BASE:#x})"
            raise ValueError(msg)
        if version != VERSION:
            msg = f"Unsupported version: {version} (expected {VERSION})"
            raise ValueError(msg)

    def _get_key_table_size(self) -> int:
        """Calculate key hash table size."""
        return int(self.maxsize / self.load_factor) * KEY_HASH_ENTRY_SIZE

    def _get_content_table_size(self) -> int:
        """Calculate content fingerprint table size."""
        return int(self.maxsize / self.load_factor) * CONTENT_FP_ENTRY_SIZE

    def _setup_memory_views(self) -> None:
        """Set up memory views for different regions."""
        buf = memoryview(self._shm.buf)
        self._blob_pool_start = struct.unpack_from(">Q", buf, 24)[0]
        self._blob_pool_size = struct.unpack_from(">Q", buf, 32)[0]
        self._key_table_start = struct.unpack_from(">Q", buf, 56)[0]
        self._key_table_size = struct.unpack_from(">Q", buf, 64)[0]
        self._content_table_start = struct.unpack_from(">Q", buf, 72)[0]
        self._content_table_size = struct.unpack_from(">Q", buf, 80)[0]

    def _read_header_field(self, offset: int, fmt: str) -> Any:  # noqa: ANN401
        """Read a field from the header."""
        return struct.unpack_from(fmt, memoryview(self._shm.buf), offset)[0]

    def _write_header_field(self, offset: int, fmt: str, value: Any) -> None:  # noqa: ANN401
        """Write a field to the header."""
        struct.pack_into(fmt, memoryview(self._shm.buf), offset, value)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Context manager for acquiring lock with timeout.

        Yields:
            None

        Raises:
            CouldNotLockError: If lock acquisition times out.
        """
        acquired = self._lock.acquire(timeout=self.lock_timeout)
        if not acquired:
            msg = "Failed to acquire lock"
            raise CouldNotLockError(msg)
        try:
            yield
        finally:
            self._lock.release()

    def _get_blob_pool_next(self) -> int:
        """Get the next append pointer for blob pool."""
        return self._read_header_field(48, ">Q")

    def _get_blob_pool_used(self) -> int:
        """Get the bytes used in blob pool (for testing)."""
        return self._read_header_field(40, ">Q")

    def _set_blob_pool_next(self, value: int) -> None:
        """Set the next append pointer for blob pool."""
        self._write_header_field(48, ">Q", value)

    def _get_current_items(self) -> int:
        """Get current number of items."""
        return self._read_header_field(96, ">Q")

    def _set_current_items(self, value: int) -> None:
        """Set current number of items."""
        self._write_header_field(96, ">Q", value)

    def _find_key_slot(self, key_bytes: bytes) -> int | None:
        """Find slot for key in hash table using linear probing.

        Args:
            key_bytes: The key bytes to find.

        Returns:
            Slot index if found, None if not found.
        """
        key_hash = self.hash_func(key_bytes)
        buf = memoryview(self._shm.buf)
        start = self._key_table_start
        entry_size = KEY_HASH_ENTRY_SIZE
        table_size = self._key_table_size

        # Use full 128-bit hash for slot calculation
        slot_hash = int.from_bytes(key_hash, byteorder="big")
        slot = slot_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            entry_hash = bytes(buf[offset : offset + KEY_HASH_WIDTH])

            if entry_hash == EMPTY_KEY_HASH:
                # Empty slot (never occupied) - key not found
                return None

            if entry_hash == TOMBSTONE:
                # Tombstone (deleted entry) - continue probing
                slot = (slot + 1) % table_size
                if slot == initial_slot:
                    # Table full
                    return None
                continue

            if entry_hash == key_hash:
                # Hash matches, check actual key
                key_addr = struct.unpack_from(">Q", buf, offset + KEY_HASH_WIDTH)[0]
                if key_addr != 0:
                    # Read and compare key
                    stored_key = self._read_blob(key_addr)
                    if stored_key == key_bytes:
                        return slot

            # Linear probe
            slot = (slot + 1) % table_size
            if slot == initial_slot:
                # Table full
                return None

    def _find_empty_key_slot(self, key_hash: bytes) -> int | None:
        """Find empty slot for key using linear probing.

        Returns:
            Slot index if found, None if table full.
        """
        buf = memoryview(self._shm.buf)
        start = self._key_table_start
        entry_size = KEY_HASH_ENTRY_SIZE
        table_size = self._key_table_size

        # Use full 128-bit hash for slot calculation
        slot_hash = int.from_bytes(key_hash, byteorder="big")
        slot = slot_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            entry_hash = bytes(buf[offset : offset + KEY_HASH_WIDTH])

            if entry_hash in (EMPTY_KEY_HASH, TOMBSTONE):
                # Empty slot or tombstone - can use for insertion
                return slot

            # Linear probe
            slot = (slot + 1) % table_size
            if slot == initial_slot:
                # Table full
                return None

    def _find_content_slot(self, fingerprint: bytes) -> int | None:
        """Find slot for content fingerprint in hash table.

        Returns:
            Slot index if found, None if not found.
        """
        buf = memoryview(self._shm.buf)
        start = self._content_table_start
        entry_size = CONTENT_FP_ENTRY_SIZE
        table_size = self._content_table_size

        # Use full 128-bit fingerprint for slot calculation
        slot_hash = int.from_bytes(fingerprint, byteorder="big")
        slot = slot_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            stored_fp = buf[offset : offset + CONTENT_FP_WIDTH]

            if stored_fp == EMPTY_CONTENT_FP:
                # Empty slot
                return None

            if stored_fp == fingerprint:
                # Found
                return slot

            # Linear probe
            slot = (slot + 1) % table_size
            if slot == initial_slot:
                # Table full
                return None

    def _find_empty_content_slot(self, fingerprint: bytes) -> int | None:
        """Find empty slot for content fingerprint.

        Returns:
            Slot index if found, None if table full.
        """
        buf = memoryview(self._shm.buf)
        start = self._content_table_start
        entry_size = CONTENT_FP_ENTRY_SIZE
        table_size = self._content_table_size

        # Use full 128-bit fingerprint for slot calculation
        slot_hash = int.from_bytes(fingerprint, byteorder="big")
        slot = slot_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            stored_fp = buf[offset : offset + CONTENT_FP_WIDTH]

            if stored_fp == EMPTY_CONTENT_FP:
                # Empty slot found
                return slot

            # Linear probe
            slot = (slot + 1) % table_size
            if slot == initial_slot:
                # Table full
                return None

    def _append_blob(self, blob_type: int, data: bytes) -> int:
        """Append blob to pool and return address.

        Args:
            blob_type: BLOB_TYPE_KEY or BLOB_TYPE_CONTENT.
            data: Blob data bytes.

        Returns:
            Address (offset) of blob in pool.
        """
        buf = memoryview(self._shm.buf)
        next_ptr = self._get_blob_pool_next()
        start = self._blob_pool_start

        # Validate blob pool pointer is within bounds
        if next_ptr < start or next_ptr >= start + self._blob_pool_size:
            msg = f"Blob pool pointer out of bounds: {next_ptr} (start={start}, size={self._blob_pool_size})"
            raise RuntimeError(msg)

        # Calculate entry size: header (type + length) + data + alignment
        entry_size = _align(BLOB_HEADER_SIZE + len(data), ALIGNMENT)

        # Check if we have space
        if next_ptr + entry_size > start + self._blob_pool_size:
            msg = "Blob pool full"
            raise RuntimeError(msg)

        # Write entry
        offset = next_ptr
        struct.pack_into("B", buf, offset, blob_type)
        struct.pack_into(">I", buf, offset + BLOB_TYPE_WIDTH, len(data))
        buf[offset + BLOB_HEADER_SIZE : offset + BLOB_HEADER_SIZE + len(data)] = data

        # Update next pointer
        self._set_blob_pool_next(next_ptr + entry_size)
        # Update blob pool usage counter (read-modify-write is safe because
        # _append_blob is only called from within methods that hold the lock)
        used = self._read_header_field(40, ">Q") + entry_size
        self._write_header_field(40, ">Q", used)

        return offset

    def _read_blob(self, address: int) -> bytes:
        """Read blob from pool.

        Args:
            address: Address of blob.

        Returns:
            Blob data bytes.
        """
        buf = memoryview(self._shm.buf)
        struct.unpack_from("B", buf, address)[0]
        length = struct.unpack_from(">I", buf, address + BLOB_TYPE_WIDTH)[0]
        return bytes(buf[address + BLOB_HEADER_SIZE : address + BLOB_HEADER_SIZE + length])

    def _get_blob_type(self, address: int) -> int:
        """Get blob type at address."""
        return struct.unpack_from("B", memoryview(self._shm.buf), address)[0]

    def _update_timestamp(self, slot: int) -> None:
        """Update access timestamp for key at given slot.

        Args:
            slot: Key hash table slot index.
        """
        buf = memoryview(self._shm.buf)
        offset = self._key_table_start + slot * KEY_HASH_ENTRY_SIZE
        timestamp_offset = offset + KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH
        struct.pack_into(">Q", buf, timestamp_offset, time.time_ns())

    def __getitem__(self, key: bytes) -> bytes:
        """Get value for key.

        Args:
            key: Key bytes.

        Returns:
            Value bytes.

        Raises:
            KeyError: If key not found.
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            slot = self._find_key_slot(key)
            if slot is None:
                raise KeyError(key)

            # Read entry
            buf = memoryview(self._shm.buf)
            offset = self._key_table_start + slot * KEY_HASH_ENTRY_SIZE
            key_addr = struct.unpack_from(">Q", buf, offset + KEY_HASH_WIDTH)[0]
            if key_addr == 0:
                raise KeyError(key)
            fingerprint = bytes(buf[offset + KEY_HASH_WIDTH + ADDRESS_WIDTH : offset + KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH])

            # Find content
            content_slot = self._find_content_slot(fingerprint)
            if content_slot is None:
                raise KeyError(key)

            content_offset = self._content_table_start + content_slot * CONTENT_FP_ENTRY_SIZE
            content_addr = struct.unpack_from(">Q", buf, content_offset + CONTENT_FP_WIDTH)[0]

            # Verify content type
            if self._get_blob_type(content_addr) != BLOB_TYPE_CONTENT:
                raise KeyError(key)

            # Update access timestamp
            self._update_timestamp(slot)

            return self._read_blob(content_addr)

    def __setitem__(self, key: bytes, value: bytes) -> None: # noqa: PLR0915
        """Set value for key.

        Args:
            key: Key bytes.
            value: Value bytes.

        Raises:
            CouldNotLockError: If lock acquisition fails.
        """
        value_fp = self.hash_func(value)

        with self._locked():
            # Check if key exists
            key_slot = self._find_key_slot(key)
            if key_slot is not None:
                # Key exists, update content fingerprint
                buf = memoryview(self._shm.buf)
                offset = self._key_table_start + key_slot * KEY_HASH_ENTRY_SIZE
                key_addr = struct.unpack_from(">Q", buf, offset + KEY_HASH_WIDTH)[0]

                # Check if content already exists
                content_slot = self._find_content_slot(value_fp)
                if content_slot is None:
                    # New content - append to blob pool
                    content_addr = self._append_blob(BLOB_TYPE_CONTENT, value)
                    # Find empty slot for content
                    content_slot = self._find_empty_content_slot(value_fp)
                    if content_slot is None:
                        msg = "Content hash table full"
                        raise RuntimeError(msg)
                    # Insert into content table
                    content_offset = self._content_table_start + content_slot * CONTENT_FP_ENTRY_SIZE
                    buf[content_offset : content_offset + CONTENT_FP_WIDTH] = value_fp
                    struct.pack_into(">Q", buf, content_offset + CONTENT_FP_WIDTH, content_addr)

                # Update fingerprint in key table
                buf[offset + KEY_HASH_WIDTH + ADDRESS_WIDTH : offset + KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH] = value_fp
                # Update access timestamp
                self._update_timestamp(key_slot)
            else:
                # New key - check if we need to evict first
                if self._get_current_items() >= self.maxsize:
                    self._evict_lru()

                # Append key to blob pool
                key_addr = self._append_blob(BLOB_TYPE_KEY, key)

                # Find or create content
                content_slot = self._find_content_slot(value_fp)
                if content_slot is None:
                    # New content - append to blob pool
                    content_addr = self._append_blob(BLOB_TYPE_CONTENT, value)
                    # Find empty slot for content
                    content_slot = self._find_empty_content_slot(value_fp)
                    if content_slot is None:
                        msg = "Content hash table full"
                        raise RuntimeError(msg)
                    # Insert into content table
                    buf = memoryview(self._shm.buf)
                    content_offset = self._content_table_start + content_slot * CONTENT_FP_ENTRY_SIZE
                    buf[content_offset : content_offset + CONTENT_FP_WIDTH] = value_fp
                    struct.pack_into(">Q", buf, content_offset + CONTENT_FP_WIDTH, content_addr)
                else:
                    # Content exists - reuse address
                    buf = memoryview(self._shm.buf)
                    content_offset = self._content_table_start + content_slot * CONTENT_FP_ENTRY_SIZE
                    content_addr = struct.unpack_from(">Q", buf, content_offset + CONTENT_FP_WIDTH)[0]

                # Insert into key table
                key_hash = self.hash_func(key)
                key_slot = self._find_empty_key_slot(key_hash)
                if key_slot is None:
                    msg = "Key hash table full"
                    raise RuntimeError(msg)

                buf = memoryview(self._shm.buf)
                offset = self._key_table_start + key_slot * KEY_HASH_ENTRY_SIZE
                buf[offset : offset + KEY_HASH_WIDTH] = key_hash
                struct.pack_into(">Q", buf, offset + KEY_HASH_WIDTH, key_addr)
                buf[offset + KEY_HASH_WIDTH + ADDRESS_WIDTH : offset + KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH] = value_fp
                # Set initial timestamp
                timestamp_offset = offset + KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH
                struct.pack_into(">Q", buf, timestamp_offset, time.time_ns())

                # Update item count
                self._set_current_items(self._get_current_items() + 1)

    def _evict_lru(self, samples: int = DEFAULT_LRU_SAMPLES) -> None:
        """Evict least recently used item using approximated LRU.

        Samples N random keys and evicts the one with the oldest timestamp.
        This is similar to Redis's approximated LRU algorithm.

        Args:
            samples: Number of keys to sample (default 10).
        """
        buf = memoryview(self._shm.buf)
        start = self._key_table_start
        entry_size = KEY_HASH_ENTRY_SIZE
        table_size = self._key_table_size

        # Calculate maximum slots to test before giving up
        max_slots_to_test = int(samples / DEFAULT_LOAD_FACTOR * 2)

        # Randomly sample slots until we find enough filled ones or hit the limit
        valid_slots: list[int] = []

        try:
            slots_to_test = random.sample(range(table_size), max_slots_to_test)
        except ValueError:
            slots_to_test = range(table_size)

        for slot in slots_to_test:
            # Check if slot is filled (skip empty slots and tombstones)
            offset = start + slot * entry_size
            entry_hash = bytes(buf[offset : offset + KEY_HASH_WIDTH])
            if entry_hash not in (EMPTY_KEY_HASH, TOMBSTONE):
                valid_slots.append(slot)
                if len(valid_slots) >= samples:
                    break

        if not valid_slots:
            return  # No items to evict

        def slot_timestamp(slot: int) -> int:
            offset = start + slot * entry_size + KEY_HASH_WIDTH + ADDRESS_WIDTH + CONTENT_FP_WIDTH
            return struct.unpack_from(">Q", buf, offset)[0]

        oldest_slot = min(valid_slots, key=slot_timestamp)

        # Evict the oldest slot
        offset = start + oldest_slot * entry_size
        buf[offset : offset + entry_size] = b"\x00" * entry_size
        self._set_current_items(self._get_current_items() - 1)

    def __delitem__(self, key: bytes) -> None:
        """Delete key from cache.

        Args:
            key: Key bytes.

        Raises:
            KeyError: If key not found.
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            slot = self._find_key_slot(key)
            if slot is None:
                raise KeyError(key)

            # Mark entry as tombstone (deleted) instead of clearing
            # This preserves the linear probe chain for other keys
            buf = memoryview(self._shm.buf)
            offset = self._key_table_start + slot * KEY_HASH_ENTRY_SIZE
            # Set hash to tombstone marker, clear the rest
            buf[offset : offset + KEY_HASH_WIDTH] = TOMBSTONE
            buf[offset + KEY_HASH_WIDTH : offset + KEY_HASH_ENTRY_SIZE] = b"\x00" * (
                KEY_HASH_ENTRY_SIZE - KEY_HASH_WIDTH
            )

            self._set_current_items(self._get_current_items() - 1)

    def __contains__(self, key: bytes) -> bool:
        """Check if key is in cache.

        Args:
            key: Key bytes.

        Returns:
            True if key exists, False otherwise.

        Raises:
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            return self._find_key_slot(key) is not None

    def __len__(self) -> int:
        """Get number of items in cache.

        Returns:
            Number of items.
        """
        return self._get_current_items()

    def __iter__(self) -> Iterator[bytes]:
        """Iterate over keys in cache."""
        return iter(self.keys())

    def keys(self) -> list[bytes]:
        """Get a list of all keys in the cache.

        Returns:
            List of all keys in the cache.

        Raises:
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            keys = []
            buf = memoryview(self._shm.buf)
            start = self._key_table_start
            table_size = self._key_table_size

            for slot in range(table_size):
                offset = start + slot * KEY_HASH_ENTRY_SIZE
                entry_hash = bytes(buf[offset : offset + KEY_HASH_WIDTH])
                # Skip empty slots and tombstones
                if entry_hash not in (EMPTY_KEY_HASH, TOMBSTONE):
                    key_addr = struct.unpack_from(">Q", buf, offset + KEY_HASH_WIDTH)[0]
                    if key_addr != 0:
                        key = self._read_blob(key_addr)
                        keys.append(key)

            return keys

    def content_items(self) -> Iterator[tuple[bytes, bytes]]:
        """Iterate over content fingerprint and content blob pairs.

        Yields tuples of (fingerprint, content_bytes) without loading all
        content into memory at once.

        Yields:
            Tuple of (content_fingerprint, content_bytes).

        Raises:
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            buf = memoryview(self._shm.buf)
            start = self._content_table_start
            entry_size = CONTENT_FP_ENTRY_SIZE
            table_size = self._content_table_size

            for slot in range(table_size):
                offset = start + slot * entry_size
                fingerprint = bytes(buf[offset : offset + CONTENT_FP_WIDTH])

                # Check if slot is empty (all zeros)
                if fingerprint == EMPTY_CONTENT_FP:
                    continue

                # Get content address
                content_addr = struct.unpack_from(">Q", buf, offset + CONTENT_FP_WIDTH)[0]
                if content_addr == 0:
                    continue

                # Verify it's a content blob
                if self._get_blob_type(content_addr) != BLOB_TYPE_CONTENT:
                    continue

                # Read content blob
                content_bytes = self._read_blob(content_addr)

                yield (fingerprint, content_bytes)

    def get(self, key: bytes, default: bytes | None = None) -> bytes | None:
        """Get value for key with default.

        Args:
            key: Key bytes.
            default: Default value if key not found.

        Returns:
            Value bytes or default.
        """
        try:
            return self[key]
        except KeyError:
            return default

    def compact(self) -> None: # noqa: C901, PLR0915, PLR0912
        """Compact the blob pool by removing unreferenced blobs and defragmenting.

        This method:
        1. Collects all referenced blob addresses from hash tables
        2. Sorts addresses and moves blobs sequentially to fill gaps
        3. Updates all addresses in hash tables to point to new locations
        4. Zeros out the remainder and resets blob pool pointers

        Raises:
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            buf = memoryview(self._shm.buf)
            start = self._blob_pool_start
            pool_size = self._blob_pool_size

            # Step 1: Collect all referenced addresses
            referenced_key_addrs: set[int] = set()
            referenced_content_fps: set[bytes] = set()

            # Scan key hash table
            key_table_start = self._key_table_start
            key_table_size = self._key_table_size
            for slot in range(key_table_size):
                offset = key_table_start + slot * KEY_HASH_ENTRY_SIZE
                entry_hash = bytes(buf[offset : offset + KEY_HASH_WIDTH])
                # Skip empty slots and tombstones
                if entry_hash not in (EMPTY_KEY_HASH, TOMBSTONE):
                    key_addr = struct.unpack_from(">Q", buf, offset + KEY_HASH_WIDTH)[0]
                    if key_addr != 0:
                        referenced_key_addrs.add(key_addr)
                        # Get content fingerprint
                        fp_offset = offset + KEY_HASH_WIDTH + ADDRESS_WIDTH
                        content_fp = bytes(buf[fp_offset : fp_offset + CONTENT_FP_WIDTH])
                        if content_fp != EMPTY_CONTENT_FP:
                            referenced_content_fps.add(content_fp)

            # Collect referenced content addresses
            referenced_content_addrs: set[int] = set()
            content_table_start = self._content_table_start
            content_table_size = self._content_table_size
            for slot in range(content_table_size):
                offset = content_table_start + slot * CONTENT_FP_ENTRY_SIZE
                fingerprint = bytes(buf[offset : offset + CONTENT_FP_WIDTH])
                if fingerprint != EMPTY_CONTENT_FP and fingerprint in referenced_content_fps:
                    content_addr = struct.unpack_from(">Q", buf, offset + CONTENT_FP_WIDTH)[0]
                    if content_addr != 0:
                        referenced_content_addrs.add(content_addr)

            # Step 2: Collect all referenced addresses with their sizes
            all_referenced_addrs = referenced_key_addrs | referenced_content_addrs
            referenced_blobs: list[tuple[int, int]] = []  # (old_addr, entry_size)
            skipped_addrs: set[int] = set()

            for addr in all_referenced_addrs:
                try:
                    # Validate address is within blob pool bounds
                    if addr < start or addr >= start + pool_size:
                        logger.warning("Referenced address %d is out of blob pool bounds", addr)
                        skipped_addrs.add(addr)
                        continue
                    length = struct.unpack_from(">I", buf, addr + BLOB_TYPE_WIDTH)[0]
                    # Validate length is reasonable
                    if length > pool_size:
                        logger.warning("Blob at %d has invalid length %d", addr, length)
                        skipped_addrs.add(addr)
                        continue
                    entry_size = _align(BLOB_HEADER_SIZE + length, ALIGNMENT)
                    # Validate entry doesn't exceed pool bounds
                    if addr + entry_size > start + pool_size:
                        logger.warning("Blob at %d (size %d) exceeds pool bounds", addr, entry_size)
                        skipped_addrs.add(addr)
                        continue
                    referenced_blobs.append((addr, entry_size))
                except (struct.error, IndexError) as e:
                    # Invalid blob, skip
                    logger.warning("Failed to read blob at %d: %s", addr, e)
                    skipped_addrs.add(addr)
                    continue

            # If we skipped any addresses, they won't be in addr_map, so their addresses won't be updated
            # This could cause issues, but they're likely invalid blobs anyway
            if skipped_addrs:
                logger.warning("Skipped %d invalid blob addresses during compaction", len(skipped_addrs))

            # Step 3: Sort by address and move sequentially to fill gaps
            referenced_blobs.sort(key=lambda x: x[0])  # Sort by old address
            addr_map: dict[int, int] = {}  # old_addr -> new_addr
            new_ptr = start

            for old_addr, entry_size in referenced_blobs:
                # Always update address mapping
                addr_map[old_addr] = new_ptr

                if new_ptr != old_addr:
                    # Copy blob to new location
                    # Safe because we process in sorted order:
                    # - If new_ptr < old_addr: writing before reading (safe)
                    # - If new_ptr > old_addr: we've already processed everything <= old_addr (safe)
                    old_contents = buf[old_addr : old_addr + entry_size]
                    buf[new_ptr : new_ptr + entry_size] = old_contents

                new_ptr += entry_size

            # Step 4: Update all addresses in hash tables
            # Update key addresses in key hash table
            for slot in range(key_table_size):
                offset = key_table_start + slot * KEY_HASH_ENTRY_SIZE
                entry_hash = bytes(buf[offset : offset + KEY_HASH_WIDTH])
                # Skip empty slots and tombstones
                if entry_hash not in (EMPTY_KEY_HASH, TOMBSTONE):
                    key_addr = struct.unpack_from(">Q", buf, offset + KEY_HASH_WIDTH)[0]
                    if key_addr != 0:
                        if key_addr in addr_map:
                            struct.pack_into(">Q", buf, offset + KEY_HASH_WIDTH, addr_map[key_addr])
                        else:
                            # Address not in addr_map - this shouldn't happen for valid keys
                            # It means the blob wasn't in referenced_blobs (couldn't read size or invalid)
                            logger.warning(
                                "Key address %d in hash table not found in addr_map during compaction",
                                key_addr,
                            )

            # Update content addresses in content fingerprint table
            for slot in range(content_table_size):
                offset = content_table_start + slot * CONTENT_FP_ENTRY_SIZE
                fingerprint = bytes(buf[offset : offset + CONTENT_FP_WIDTH])
                if fingerprint != EMPTY_CONTENT_FP:
                    content_addr = struct.unpack_from(">Q", buf, offset + CONTENT_FP_WIDTH)[0]
                    if content_addr != 0 and content_addr in addr_map:
                        struct.pack_into(">Q", buf, offset + CONTENT_FP_WIDTH, addr_map[content_addr])

            # Step 5: Zero out remainder and reset pointers
            if new_ptr < start + pool_size:
                # Zero out in chunks to avoid creating large bytes object in memory
                chunk_size = 8192  # 8KB chunks
                zero_chunk = b"\x00" * chunk_size
                current = new_ptr
                while current < start + pool_size:
                    chunk_len = min(chunk_size, start + pool_size - current)
                    buf[current : current + chunk_len] = zero_chunk[:chunk_len]
                    current += chunk_len

            # Update pointers
            self._set_blob_pool_next(new_ptr)
            self._write_header_field(40, ">Q", new_ptr - start)  # blob_pool_used

    def clear(self) -> None:
        """Clear all items from cache.

        Raises:
            CouldNotLockError: If lock acquisition fails.
        """
        with self._locked():
            # Clear hash tables
            buf = memoryview(self._shm.buf)
            key_table_size = self._get_key_table_size()
            content_table_size = self._get_content_table_size()
            buf[self._key_table_start : self._key_table_start + key_table_size] = b"\x00" * key_table_size
            buf[
                self._content_table_start : self._content_table_start + content_table_size
            ] = b"\x00" * content_table_size

            # Reset counters
            self._set_current_items(0)
            self._set_blob_pool_next(self._blob_pool_start)
            self._write_header_field(40, ">Q", 0)  # blob_pool_used

    def close(self) -> None:
        """Close and cleanup shared memory."""
        if self._owned_shm:
            self._shm.close()
            self._shm.unlink()

    def __enter__(self) -> ContentAddressableCache:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit."""
        self.close()
