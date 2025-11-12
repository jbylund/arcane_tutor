"""Content-addressable shared memory cache implementation.

This module provides a shared memory cache with content deduplication using
multiprocessing.SharedMemory and content-addressable storage.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable, Iterator
from multiprocessing import RLock
from multiprocessing.shared_memory import SharedMemory
from typing import Any

try:
    import xxhash
except ImportError:
    xxhash = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Constants
# Magic number to validate shared memory contains our cache structure
# Randomly generated: 4AB8_6639_3C4D, last 2 bytes (4 hex digits) reserved for version
MAGIC_BASE = 0x4AB8_6639_3C4D_0000
VERSION = 1
MAGIC = MAGIC_BASE | VERSION  # 0x4AB8_6639_3C4D_0001
HEADER_SIZE = 512
KEY_HASH_ENTRY_SIZE = 32  # 8 (hash) + 8 (addr) + 16 (fingerprint)
CONTENT_FP_ENTRY_SIZE = 24  # 16 (fingerprint) + 8 (addr)
BLOB_TYPE_KEY = 0x01
BLOB_TYPE_CONTENT = 0x02
ALIGNMENT = 8
DEFAULT_LOAD_FACTOR = 0.65
DEFAULT_LOCK_TIMEOUT = 60.0


def _extract_version_from_magic(magic: int) -> int:
    """Extract version from magic number (lower 2 bytes)."""
    return magic & 0xFFFF


def _align(size: int, alignment: int = ALIGNMENT) -> int:
    """Round up size to alignment boundary."""
    return (size + alignment - 1) & ~(alignment - 1)


def _default_hash(data: bytes) -> tuple[int, bytes]:
    """Default hash function using xxhash.

    Returns:
        Tuple of (64-bit hash for keys, 128-bit fingerprint for content)
    """
    # Use xxhash for both
    key_hash = xxhash.xxh64(data).intdigest()
    # For 128-bit fingerprint, use two different seeds
    fp1 = xxhash.xxh64(data, seed=0).intdigest()
    fp2 = xxhash.xxh64(data, seed=1).intdigest()
    fingerprint = (fp1 << 64 | fp2).to_bytes(16, byteorder="big")
    return key_hash, fingerprint


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

    def __init__(
        self,
        maxsize: int,
        shared_memory: SharedMemory | None = None,
        load_factor: float = DEFAULT_LOAD_FACTOR,
        hash_func: Callable[[bytes], tuple[int, bytes]] | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        """Initialize the cache.

        Args:
            maxsize: Maximum number of cache entries.
            shared_memory: Existing SharedMemory to use, or None to create new.
            load_factor: Hash table load factor threshold (0.0-1.0).
            hash_func: Hash function returning (key_hash, content_fingerprint).
            lock_timeout: Timeout for lock acquisition in seconds.
        """
        if maxsize <= 0:
            msg = "maxsize must be positive"
            raise ValueError(msg)

        self.maxsize = maxsize
        self.load_factor = load_factor
        self.hash_func = hash_func or _default_hash
        self.lock_timeout = lock_timeout
        self._lock = RLock()

        # Calculate sizes
        key_table_size = int(maxsize / load_factor) * KEY_HASH_ENTRY_SIZE
        content_table_size = int(maxsize / load_factor) * CONTENT_FP_ENTRY_SIZE
        # Estimate blob pool: assume avg 200 bytes key + 2000 bytes content
        blob_pool_size = int(maxsize * (200 + 2000) * 1.5)
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

    def _read_header_field(self, offset: int, fmt: str) -> Any:
        """Read a field from the header."""
        return struct.unpack_from(fmt, memoryview(self._shm.buf), offset)[0]

    def _write_header_field(self, offset: int, fmt: str, value: Any) -> None:
        """Write a field to the header."""
        struct.pack_into(fmt, memoryview(self._shm.buf), offset, value)

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

    def _find_key_slot(self, key_hash: int, key_bytes: bytes) -> int | None:
        """Find slot for key in hash table using linear probing.

        Returns:
            Slot index if found, None if not found.
        """
        buf = memoryview(self._shm.buf)
        start = self._key_table_start
        entry_size = KEY_HASH_ENTRY_SIZE
        table_size = self._key_table_size

        slot = key_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            entry_hash = struct.unpack_from(">Q", buf, offset)[0]

            if entry_hash == 0:
                # Empty slot
                return None

            if entry_hash == key_hash:
                # Hash matches, check actual key
                key_addr = struct.unpack_from(">Q", buf, offset + 8)[0]
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

    def _find_empty_key_slot(self, key_hash: int) -> int | None:
        """Find empty slot for key using linear probing.

        Returns:
            Slot index if found, None if table full.
        """
        buf = memoryview(self._shm.buf)
        start = self._key_table_start
        entry_size = KEY_HASH_ENTRY_SIZE
        table_size = self._key_table_size

        slot = key_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            entry_hash = struct.unpack_from(">Q", buf, offset)[0]

            if entry_hash == 0:
                # Empty slot found
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

        # Use first 8 bytes of fingerprint as hash for slot calculation
        slot_hash = int.from_bytes(fingerprint[:8], byteorder="big")
        slot = slot_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            stored_fp = buf[offset : offset + 16]

            if stored_fp == b"\x00" * 16:
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

        slot_hash = int.from_bytes(fingerprint[:8], byteorder="big")
        slot = slot_hash % table_size
        initial_slot = slot

        while True:
            offset = start + slot * entry_size
            stored_fp = buf[offset : offset + 16]

            if stored_fp == b"\x00" * 16:
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

        # Calculate entry size: 1 (type) + 4 (length) + data + alignment
        entry_size = _align(1 + 4 + len(data), ALIGNMENT)

        # Check if we have space
        if next_ptr + entry_size > start + self._blob_pool_size:
            msg = "Blob pool full"
            raise RuntimeError(msg)

        # Write entry
        offset = next_ptr
        struct.pack_into("B", buf, offset, blob_type)
        struct.pack_into(">I", buf, offset + 1, len(data))
        buf[offset + 5 : offset + 5 + len(data)] = data

        # Update next pointer
        self._set_blob_pool_next(next_ptr + entry_size)
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
        blob_type = struct.unpack_from("B", buf, address)[0]
        length = struct.unpack_from(">I", buf, address + 1)[0]
        data = bytes(buf[address + 5 : address + 5 + length])
        return data

    def _get_blob_type(self, address: int) -> int:
        """Get blob type at address."""
        return struct.unpack_from("B", memoryview(self._shm.buf), address)[0]

    def __getitem__(self, key: bytes) -> bytes:
        """Get value for key.

        Args:
            key: Key bytes.

        Returns:
            Value bytes.

        Raises:
            KeyError: If key not found.
        """
        if not self._lock.acquire(timeout=self.lock_timeout):
            msg = "Failed to acquire lock"
            raise RuntimeError(msg)

        try:
            key_hash, _ = self.hash_func(key)
            slot = self._find_key_slot(key_hash, key)
            if slot is None:
                raise KeyError(key)

            # Read entry
            buf = memoryview(self._shm.buf)
            offset = self._key_table_start + slot * KEY_HASH_ENTRY_SIZE
            key_addr = struct.unpack_from(">Q", buf, offset + 8)[0]
            fingerprint = bytes(buf[offset + 16 : offset + 32])

            # Find content
            content_slot = self._find_content_slot(fingerprint)
            if content_slot is None:
                raise KeyError(key)

            content_offset = self._content_table_start + content_slot * CONTENT_FP_ENTRY_SIZE
            content_addr = struct.unpack_from(">Q", buf, content_offset + 16)[0]

            # Verify content type
            if self._get_blob_type(content_addr) != BLOB_TYPE_CONTENT:
                raise KeyError(key)

            return self._read_blob(content_addr)
        finally:
            self._lock.release()

    def __setitem__(self, key: bytes, value: bytes) -> None:
        """Set value for key.

        Args:
            key: Key bytes.
            value: Value bytes.
        """
        if not self._lock.acquire(timeout=self.lock_timeout):
            msg = "Failed to acquire lock"
            raise RuntimeError(msg)

        try:
            key_hash, _ = self.hash_func(key)
            _, value_fp = self.hash_func(value)

            # Check if key exists
            key_slot = self._find_key_slot(key_hash, key)
            if key_slot is not None:
                # Key exists, update content fingerprint
                buf = memoryview(self._shm.buf)
                offset = self._key_table_start + key_slot * KEY_HASH_ENTRY_SIZE
                key_addr = struct.unpack_from(">Q", buf, offset + 8)[0]

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
                    buf[content_offset : content_offset + 16] = value_fp
                    struct.pack_into(">Q", buf, content_offset + 16, content_addr)

                # Update fingerprint in key table
                buf[offset + 16 : offset + 32] = value_fp
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
                    buf[content_offset : content_offset + 16] = value_fp
                    struct.pack_into(">Q", buf, content_offset + 16, content_addr)
                else:
                    # Content exists - reuse address
                    buf = memoryview(self._shm.buf)
                    content_offset = self._content_table_start + content_slot * CONTENT_FP_ENTRY_SIZE
                    content_addr = struct.unpack_from(">Q", buf, content_offset + 16)[0]

                # Insert into key table
                key_slot = self._find_empty_key_slot(key_hash)
                if key_slot is None:
                    msg = "Key hash table full"
                    raise RuntimeError(msg)

                buf = memoryview(self._shm.buf)
                offset = self._key_table_start + key_slot * KEY_HASH_ENTRY_SIZE
                struct.pack_into(">Q", buf, offset, key_hash)
                struct.pack_into(">Q", buf, offset + 8, key_addr)
                buf[offset + 16 : offset + 32] = value_fp

                # Update item count
                self._set_current_items(self._get_current_items() + 1)

        finally:
            self._lock.release()

    def _evict_lru(self) -> None:
        """Evict least recently used item (simple: evict first item found)."""
        # Simple LRU: for now, just evict the first item we find
        # TODO: Implement proper LRU tracking
        buf = memoryview(self._shm.buf)
        start = self._key_table_start
        entry_size = KEY_HASH_ENTRY_SIZE
        table_size = self._key_table_size

        for slot in range(table_size):
            offset = start + slot * entry_size
            entry_hash = struct.unpack_from(">Q", buf, offset)[0]
            if entry_hash != 0:
                # Found an entry - evict it
                # Clear the entry
                buf[offset : offset + entry_size] = b"\x00" * entry_size
                self._set_current_items(self._get_current_items() - 1)
                return

    def __delitem__(self, key: bytes) -> None:
        """Delete key from cache.

        Args:
            key: Key bytes.

        Raises:
            KeyError: If key not found.
        """
        if not self._lock.acquire(timeout=self.lock_timeout):
            msg = "Failed to acquire lock"
            raise RuntimeError(msg)

        try:
            key_hash, _ = self.hash_func(key)
            slot = self._find_key_slot(key_hash, key)
            if slot is None:
                raise KeyError(key)

            # Clear entry (lazy deletion)
            buf = memoryview(self._shm.buf)
            offset = self._key_table_start + slot * KEY_HASH_ENTRY_SIZE
            buf[offset : offset + KEY_HASH_ENTRY_SIZE] = b"\x00" * KEY_HASH_ENTRY_SIZE

            self._set_current_items(self._get_current_items() - 1)
        finally:
            self._lock.release()

    def __contains__(self, key: bytes) -> bool:
        """Check if key is in cache.

        Args:
            key: Key bytes.

        Returns:
            True if key exists, False otherwise.
        """
        if not self._lock.acquire(timeout=self.lock_timeout):
            return False

        try:
            key_hash, _ = self.hash_func(key)
            return self._find_key_slot(key_hash, key) is not None
        finally:
            self._lock.release()

    def __len__(self) -> int:
        """Get number of items in cache.

        Returns:
            Number of items.
        """
        return self._get_current_items()

    def __iter__(self) -> Iterator[bytes]:
        """Iterate over keys in cache."""
        if not self._lock.acquire(timeout=self.lock_timeout):
            return iter([])

        try:
            keys = []
            buf = memoryview(self._shm.buf)
            start = self._key_table_start
            entry_size = KEY_HASH_ENTRY_SIZE
            table_size = self._key_table_size

            for slot in range(table_size):
                offset = start + slot * entry_size
                entry_hash = struct.unpack_from(">Q", buf, offset)[0]
                if entry_hash != 0:
                    key_addr = struct.unpack_from(">Q", buf, offset + 8)[0]
                    if key_addr != 0:
                        key = self._read_blob(key_addr)
                        keys.append(key)

            return iter(keys)
        finally:
            self._lock.release()

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

    def clear(self) -> None:
        """Clear all items from cache."""
        if not self._lock.acquire(timeout=self.lock_timeout):
            msg = "Failed to acquire lock"
            raise RuntimeError(msg)

        try:
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
        finally:
            self._lock.release()

    def close(self) -> None:
        """Close and cleanup shared memory."""
        if self._owned_shm:
            self._shm.close()
            self._shm.unlink()

    def __enter__(self) -> ContentAddressableCache:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
