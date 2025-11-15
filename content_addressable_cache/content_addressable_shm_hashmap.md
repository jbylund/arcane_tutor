# Content Addressable Storage Shared Memory Cache Plan

## Overview

A shared memory cache implementation using content-addressable storage for value deduplication. The cache is stored in a single `multiprocessing.SharedMemory` segment with multiple logical regions for hash tables and variable-width storage pools.

**Key Features**:
- Content deduplication: Multiple keys can share the same content (value), stored once
- Fixed-width hash tables for fast lookups
- Single variable-width blob storage pool for both keys and content
- LRU eviction with lazy deletion and periodic compaction
- Migration-based compaction: Create new segment, compact, migrate processes

## Shared Memory Segment Structure

The cache uses a **single SharedMemory segment** divided into logical regions:

```
┌─────────────────────────────────────────────────────────────┐
│                    SHARED MEMORY SEGMENT                    │
├─────────────────────────────────────────────────────────────┤
│  [Header/Metadata Region]                                   │
│    - Cache metadata, pointers, counters, locks              │
├─────────────────────────────────────────────────────────────┤
│  [Hash Table Region]                                        │
│    - Key hash table: key_hash -> (key_addr, content_fp)     │
│    - Content fingerprint table: content_fp -> content_addr  │
├─────────────────────────────────────────────────────────────┤
│  [Blob Storage Pool]                                        │
│    - Sequential variable-width data (keys and content)      │
│    - Each entry has type discriminator (key vs. content)    │
└─────────────────────────────────────────────────────────────┘
```

## Region Layouts

### 1. Header/Metadata Region

**Purpose**: Cache metadata, coordination, and pointers to other regions.

**Layout** (approximate 256-512 bytes):
```
Offset  Size    Field                    Description
─────────────────────────────────────────────────────────────
0x0000   8      magic                    Magic number (validation)
0x0008   4      version                  Format version
0x000C   4      segment_version          Migration version counter
0x0010   64     segment_name             Current segment name (UTF-8, null-terminated)
0x0050   8      total_size               Total segment size in bytes
0x0058   8      blob_pool_start          Offset to blob storage pool
0x0060   8      blob_pool_size            Total size of blob pool
0x0068   8      blob_pool_used            Bytes used in blob pool
0x0070   8      blob_pool_next            Append pointer (next free offset)
0x0098   8      key_hash_table_start     Offset to key hash table
0x00A0   8      key_hash_table_size      Size of key hash table (entries)
0x00A8   8      content_fp_table_start   Offset to content fingerprint table
0x00B0   8      content_fp_table_size    Size of content fingerprint table (entries)
0x00B8   8      max_items                Maximum number of cache entries
0x00C0   8      current_items            Current number of cache entries
0x00C8   8      lock_offset              Offset to lock structure (if in-segment)
...      ...    (reserved for future use)
```

**Questions**:
- Should lock be in-segment or separate `multiprocessing.Lock`?
- Exact field sizes and alignment (8-byte aligned recommended)?

### 2. Hash Table Region

**Purpose**: Fixed-width hash tables for O(1) lookups.

#### 2a. Key Hash Table

**Mapping**: `key_hash -> (key_address, content_fingerprint)`

**Entry Layout** (per entry):
```
Offset  Size    Field              Description
─────────────────────────────────────────────────
0x0000   16     key_hash           Hash of the key (128-bit)
0x0010   8      key_address        Offset to key in blob pool (64-bit)
0x0018   16     content_fingerprint  Content fingerprint (128-bit, 16 bytes)
0x0028   8      timestamp          Last access timestamp (64-bit, nanoseconds since epoch)
```

**Total Entry Size**: `16 + 8 + 16 + 8 = 48` bytes (key_hash: 16, key_address: 8, content_fingerprint: 16, timestamp: 8)

**Implementation**: **Open addressing with linear probing** (DECIDED)
- If slot is occupied, probe next slot sequentially
- Compare actual key bytes to handle hash collisions
- Tombstone markers (`0xFF` repeated 16 times) for deleted entries to preserve linear probe chains

**Decisions**:
- **Content fingerprint size**: 16 bytes (128-bit) - good balance of collision resistance and size
- **Key hash**: 128-bit (16 bytes) - matches content fingerprint size, provides better distribution
- **Timestamp**: 64-bit (8 bytes) - nanoseconds since epoch, used for approximated LRU eviction
- **Hash algorithm**: User-provided hash function, default to xxhash (DECIDED)
  - Same hash function used for both key hashing and content fingerprinting
  - xxhash is fast, deterministic, and provides good distribution
  - User can provide custom hash function if needed
- **Hash table load factor**: 65% (0.65) default (DECIDED)
  - Balanced threshold - good balance between memory usage and collision rate
  - When load factor exceeds 65%, trigger migration/compaction to resize hash tables
  - User can configure if needed

#### 2b. Content Fingerprint Table

**Mapping**: `content_fingerprint -> content_address`

**Entry Layout** (per entry):
```
Offset  Size    Field              Description
─────────────────────────────────────────────────
0x0000   16     content_fingerprint  Content fingerprint (128-bit, 16 bytes)
0x0010   8      content_address     Offset to content in blob pool (64-bit)
```

**Total Entry Size**: `16 + 8 = 24` bytes (content_fingerprint: 16, content_address: 8)

**Implementation**: **Open addressing with linear probing** (DECIDED)
- If slot is occupied, probe next slot sequentially
- Compare fingerprint bytes to handle collisions (extremely rare with 128-bit)

**Note**: This table enables content deduplication - multiple keys can reference the same content fingerprint.

### 3. Blob Storage Pool

**Purpose**: Variable-width storage for both serialized keys and content (values), with type discrimination.

**Layout**: Sequential append-only storage with format:
```
┌──────┬──────────┬──────────────┐
│ Type │ Length   │ Data         │
│(1byte│ (4 bytes)│ (variable)   │
└──────┴──────────┴──────────────┘
```

**Entry Format**:
- **Type** (1 byte, uint8): Entry type discriminator
  - `0x01` = Key
  - `0x02` = Content
- **Length** (4 bytes, uint32): Length of data in bytes
- **Data** (variable): Key or content bytes (client-provided, no built-in serialization)

**Address Format**: `start + length` (start = offset to type field, length = 1 + 4 + data_length, aligned to 8 bytes)

**Allocation**: Sequential append from `blob_pool_next` pointer, updated on each insert.
- **Alignment**: 8-byte aligned (DECIDED) - round up total entry size to multiple of 8 bytes for performance

**Serialization**: **Client-provided bytes** (DECIDED)
- Client is responsible for serializing keys and values to bytes
- Cache stores raw bytes, no built-in serialization (pickle, JSON, etc.)
- Provides maximum flexibility and performance (no serialization overhead in cache)

**Deduplication**:
- **Keys**: Checked at insert time via key hash table lookup. If key already exists, reuse existing `key_address` (deduplication). Only append new key if it doesn't exist.
- **Content**: Checked for existing fingerprint before appending (deduplication via content fingerprint table). If fingerprint exists, reuse existing `content_address`.

## Storage Pool Design: Single Unified Blob Pool

**Decision**: Use a **single unified blob storage pool** for both keys and content.

**Rationale**:
- **Better space utilization**: All available space in one pool, no fixed allocation between keys and content
- **Simpler metadata**: Single append pointer and size tracking
- **Simpler address management**: One address space instead of two
- **Flexible allocation**: Can use all space for whichever needs it (keys or content)
- **Single compaction operation**: One pool to compact during migration

**Trade-offs**:
- **Type discrimination**: Each entry needs 1-byte type field (minimal overhead)
- **Mixed data types**: Keys and content interleaved (acceptable for cache use case)
- **Compaction**: Need to distinguish types during sweep/compaction (slightly more complex)

## Data Flow Example

**Insert Operation** (`cache[key_bytes] = value_bytes`):

1. **Client provides**: `key_bytes` and `value_bytes` (already serialized)
2. **Hash key**: `hash(key_bytes)` -> 64-bit hash
3. **Check key hash table**: Does entry exist?
   - **If yes**: Get existing `key_address` (key deduplication - reuse existing key blob)
   - **If no**: Append key to blob pool as `[type=KEY][length][key_bytes]`, get `key_address`
4. **Hash content**: `hash(value_bytes)` -> 128-bit content fingerprint
6. **Check content fingerprint table**: Does fingerprint exist?
   - **If yes**: Use existing `content_address` (content deduplication - reuse existing content blob)
   - **If no**: Append content to blob pool as `[type=CONTENT][length][content_bytes]`, get `content_address`, insert into fingerprint table
7. **Update key hash table**: Store/update `(key_address, content_fingerprint)`
   - If key existed, update the content fingerprint in place
   - If key is new, insert new entry

**Lookup Operation** (`cache[key_bytes]`):

1. **Client provides**: `key_bytes` (already serialized)
2. **Hash key**: `hash(key_bytes)` -> 64-bit hash
3. **Lookup in key hash table**: Find entry, get `(key_address, content_fingerprint)`
4. **Read key from blob pool**: Read `[type][length][data]` at `key_address`, validate type is KEY and key bytes match (handle hash collisions)
5. **Lookup content fingerprint table**: Get `content_address`
6. **Read content from blob pool**: Read `[type][length][data]` at `content_address`, validate type is CONTENT, return `value_bytes`

## Hash Collision Handling

**Key Hash Collisions**: **Open addressing with linear probing** (DECIDED)
- If slot occupied, check next slot sequentially
- Compare actual key bytes to verify match
- Use tombstone markers (`0xFF` repeated 16 times) for deleted entries to preserve linear probe chains

**Content Fingerprint Collisions**: Extremely rare with 128-bit fingerprint, but should verify content bytes match on collision.

## Design Decisions Summary

**Hash Tables**:
- **Implementation**: Open addressing with linear probing (DECIDED)
- **Key hash**: 64-bit (8 bytes)
- **Content fingerprint**: 128-bit (16 bytes)

**Addresses**:
- **Size**: 64-bit offsets (8 bytes) (DECIDED)

**Serialization**:
- **Format**: Client-provided bytes (DECIDED) - no built-in serialization

**Alignment**:
- **Blob entries**: 8-byte aligned (DECIDED)

**Locking**:
- **Type**: Python `multiprocessing.RLock` (DECIDED)
- **Location**: Separate from shared memory segment

**Hash Algorithm** (DECIDED):
- **Default**: xxhash (fast, deterministic, good distribution)
- **Configurable**: User can provide custom hash function
- **Usage**: Same function for both key hashing (64-bit output) and content fingerprinting (128-bit output, can truncate or use multiple calls)

**Load Factor** (DECIDED):
- **Default**: 65% (0.65) - balanced threshold
- **Rationale**:
  - Typical hash tables use 75-80% load factor
  - 65% provides good balance between memory usage and collision rate
  - Still conservative enough to avoid excessive clustering with linear probing
  - More memory-efficient than 50% while maintaining low collision rate
  - **Recommendation**: 65% is a good default
- **When exceeded**: Trigger migration/compaction to resize hash tables
- **Configurable**: User can adjust if needed

## Reference Counting & Garbage Collection

### Lazy Deletion Strategy
When a key is deleted or evicted:
1. Mark entry in key hash table as tombstone (`0xFF` repeated 16 times) to preserve linear probe chains
2. Leave key data in blob pool (marked as unreferenced)
3. Leave content data in blob pool (may still be referenced by other keys via content fingerprint)

### Compaction Operation (IMPLEMENTED)
The `compact()` method performs in-place compaction of the blob pool:

1. **Collect referenced addresses**:
   - Scan key hash table (skipping empty slots and tombstones) to collect:
     - All referenced `key_addresses`
     - All referenced `content_fingerprints` (from key entries)
   - Scan content fingerprint table to collect:
     - All `content_addresses` for fingerprints that are referenced

2. **Validate and collect blob metadata**:
   - For each referenced address, read blob header to get size
   - Validate addresses are within blob pool bounds
   - Skip invalid blobs (out of bounds, invalid length, etc.)

3. **Sort and move blobs**:
   - Sort all referenced blobs by their old address
   - Move blobs sequentially to fill gaps, starting from the beginning of the pool
   - Build address mapping (old_addr -> new_addr) as blobs are moved

4. **Update hash table addresses**:
   - Update all `key_address` values in key hash table using address mapping
   - Update all `content_address` values in content fingerprint table using address mapping

5. **Cleanup**:
   - Zero out the remainder of the blob pool (from new_ptr to end) in chunks
   - Reset `blob_pool_next` pointer to new_ptr
   - Update `blob_pool_used` counter

**Note**: Content deduplication is automatic - if multiple keys reference the same content fingerprint,
the fingerprint will appear in the referenced set, so the content will be preserved and moved once
during compaction. The compaction process handles both keys and content in a single unified blob pool.

**Trigger**: Compaction is on-demand via `compact()` method. It can be called manually or triggered
when blob pool usage exceeds a threshold.

### Tombstone Handling
- Tombstones are used in the key hash table to mark deleted entries
- Tombstones preserve linear probe chains for other keys that may have probed past the deleted entry
- During compaction, tombstones are skipped when collecting referenced addresses
- Tombstones can be reused for new insertions (treated as empty slots for insertion purposes)

## Memory Allocation Strategy

### Variable-Width Storage Pool
**Implementation**: Single unified blob storage pool for both keys and content (see "Storage Pool Design: Single Unified Blob Pool" section above).

- **Unified blob pool**: Variable-width storage for both keys and content
- **Type discrimination**: Each entry has 1-byte type field to distinguish keys from content
- **Sequential allocation**: Append-only allocation from single pool

### Allocation Approaches

#### Option 1: Sequential Append-Only (Simplest)
- Allocate sequentially from a growing pool
- No free list management during normal operation
- Fragmentation handled during sweep/compaction
- **Pros**: Simple, fast allocation (just increment pointer), no fragmentation during inserts
- **Cons**: Requires periodic compaction, can't reuse freed space immediately

#### Option 2: Free List (Traditional)
- Maintain free list of freed blocks by size
- Allocate from free list if available, otherwise append
- **Pros**: Can reuse freed space immediately
- **Cons**: More complex, fragmentation from variable sizes, need to track free blocks

#### Option 3: Buddy System
- Allocate in power-of-2 sizes
- Split/merge blocks as needed
- **Pros**: Reduces external fragmentation, fast allocation
- **Cons**: Internal fragmentation (waste), more complex

#### Option 4: Segregated Free Lists
- Multiple free lists for different size ranges (e.g., <64B, 64-256B, 256-1KB, >1KB)
- **Pros**: Better fit than single free list, can reuse space
- **Cons**: More complex than append-only, still has fragmentation

### Recommendation: Sequential Append-Only + Periodic Compaction (IMPLEMENTED)
Given the lazy deletion + compaction approach, sequential allocation fits well:
- Fast inserts: just append to end of pool
- Compaction handles cleanup: compact and reset append pointer
- Simple: no free list management needed
- Works well with LRU: evictions are infrequent, compaction can happen on same schedule
- **Implementation**: Uses single unified blob pool with sequential append-only allocation

### Address Representation
**Recommendation: `start + length`**
- More compact than `start + end` (saves 4-8 bytes per address)
- Easier bounds checking: `end = start + length`
- Alignment: Consider 8-byte alignment for performance (round up length to multiple of 8)

### Memory Layout Structure
```
[Header/Metadata Region]
  - Total size
  - Blob pool start/end
  - Hash table metadata
  - Append pointer (blob_next)

[Hash Table Region]
  - key_hash -> (key_address, content_fingerprint) table
  - content_fingerprint -> content_address table

[Blob Storage Pool]
  - Sequential variable-width data (keys and content)
  - Format: [type: 1 byte][length: 4 bytes][data: variable]
```

### SharedMemory Constraints (Python multiprocessing.SharedMemory)
**Critical**: `multiprocessing.SharedMemory` creates a **fixed-size** block of memory:
- Size must be specified at creation time
- **Cannot be resized** after creation
- Uses memory-mapped files (on most platforms)
- Persists across process restarts if using named shared memory

**Implications for our design**:
1. **Fixed-size allocation**: Must pre-allocate entire shared memory block
2. **No dynamic growth**: Can't expand - must create new larger SharedMemory and migrate (complex)
3. **OOM handling critical**: When pool fills, must either:
   - Evict LRU items (preferred - fits LRU semantics)
   - Trigger compaction to free space
   - Fail the insert (last resort)
4. **High water mark essential**: Need to track usage to know when compaction/eviction needed
5. **Size calculation**: Must estimate total size needed upfront:
   - Hash table sizes (based on max items)
   - Blob storage pool size (estimate average key + content sizes, account for deduplication savings)

### Size Estimation Strategy
```
Total Size = Header + Hash Tables + Blob Pool

Header: ~256 bytes (metadata, pointers, counters)
Hash Tables:
  - key_hash table: max_items * entry_size (e.g., 8 bytes hash + 8 bytes addr + N bytes fingerprint)
  - content_fingerprint table: estimated_unique_content * entry_size
Blob Pool: (max_items * avg_key_size + max_items * avg_content_size * dedup_factor) * overhead_factor
  - Accounts for both keys and content in single pool
  - dedup_factor: 0.5-1.0x (accounts for content deduplication savings)
  - overhead_factor: 1.5-2x (buffer for growth, fragmentation)
```

### Migration-Based Compaction Strategy
Instead of in-place compaction, use a **migration approach** with new SharedMemory segments:

**Process**:
1. When pool becomes nearly full (threshold reached), create **new SharedMemory segment**
2. **Compact & copy** data into new segment:
   - Only copy referenced keys and content (from sweep operation)
   - Defragment by packing sequentially
   - Update all addresses to point to new locations
3. **Update metadata** with name of new segment (atomic update)
4. **Other processes** detect metadata change and switch to new segment
5. **Old segment** can be cleaned up after all processes migrate

**Benefits**:
- **Non-blocking**: Compaction happens in new segment, old segment still readable
- **Better concurrency**: Processes can continue reading from old segment during compaction
- **Atomic migration**: Metadata update is single atomic operation
- **No in-place mutation**: Safer, less risk of corruption during compaction

**Challenges**:
- **Coordination**: How do processes coordinate migration?
- **Transition period**: Both segments exist simultaneously (memory overhead)
- **Metadata atomicity**: Need atomic way to update segment name/pointer
- **Process discovery**: How do processes discover new segment?
- **Cleanup**: When is it safe to delete old segment?

### Migration Coordination Options

#### Option 1: Version Number in Metadata
- Header contains `segment_version` (monotonically increasing)
- Header contains `segment_name` (current active segment)
- Process doing compaction:
  1. Creates new segment with `version = current_version + 1`
  2. Compacts data into new segment
  3. Atomically updates `segment_version` and `segment_name` in header
- Other processes:
  - Check `segment_version` on each operation (or periodically)
  - If version changed, attach to new segment and release old one

#### Option 2: Separate Coordination Segment
- Small shared memory segment just for coordination
- Contains pointer/name to current active segment
- Processes check this segment periodically or on each operation
- Atomic updates via compare-and-swap or lock

#### Option 3: File-Based Coordination
- Use a small file or lock file to coordinate
- Contains current segment name
- Processes read file to discover current segment
- Atomic file update (rename operation)

### High Water Mark Tracking
Maintain in header:
- `blob_pool_used`: Current bytes used in blob pool
- `blob_pool_size`: Total size of blob pool
- `segment_version`: Version number for migration coordination
- `segment_name`: Name of current active SharedMemory segment

Trigger migration/compaction when: `used / size > threshold` (e.g., 0.8 or 0.9)

### Migration Process Details
1. **Compaction trigger**: Process detects high water mark exceeded
2. **Lock acquisition**: Acquire exclusive lock (only one process compacts at a time)
3. **Create new segment**: Allocate new SharedMemory with same or larger size
4. **Sweep & compact**:
   - Run sweep to identify referenced items
   - Copy referenced keys and content to new segment sequentially
   - Update hash tables with new addresses
5. **Atomic update**: Update header with new `segment_version` and `segment_name`
6. **Release lock**: Other processes can now migrate
7. **Cleanup**: After all processes migrate (or timeout), delete old segment

### Process Migration on Read/Write
Each process checks segment version:
- **On cache operations**: Check if `segment_version` changed
- **If changed**:
  - Attach to new SharedMemory segment
  - Release reference to old segment
  - Continue operation with new segment
- **Optimization**: Could check periodically in background thread instead of on every operation

### Questions:
- What's a reasonable default size? (e.g., 100MB, 500MB, 1GB?)
- Should size be configurable at initialization?
- How to handle the case where size estimate is too small? (fail fast vs. graceful degradation)
- Alignment requirements? (8-byte for better performance on most CPUs)

## Locking & Concurrency Strategy

### Operations That Need Protection
1. **Read operations**:
   - Lookup in hash tables
   - Read from key/content storage pools
   - Check segment version
2. **Write operations**:
   - Insert new key/value
   - Update hash table entries
   - Append to storage pools
   - Update append pointers
   - LRU eviction
3. **Migration operations**:
   - Creating new segment
   - Compacting data
   - Updating segment metadata
   - Process migration

### Lock Granularity Options

#### Option 1: Single Global Lock (Simplest)
- One lock protects all operations
- **Pros**: Simple, no deadlock risk, easy to reason about
- **Cons**: Poor concurrency, all operations serialize
- **Use case**: Low contention, simple implementation

#### Option 2: Read-Write Lock (Better for Read-Heavy) - **DECIDED**
- Single RWLock: multiple readers OR one writer
- **Implementation**: Python `multiprocessing.RLock`
- **Pros**: Allows concurrent reads, simple, built-in and well-tested
- **Cons**: Writes still serialize, migration blocks everything
- **Use case**: Read-heavy workloads (typical for caches)

#### Option 3: Per-Bucket Locks (Fine-Grained)
- Separate lock for each hash table bucket
- **Pros**: High concurrency, operations on different buckets don't block
- **Cons**: More complex, more memory overhead, potential deadlocks
- **Use case**: High contention, many concurrent operations

#### Option 4: Separate Locks per Region
- Lock for hash tables
- Lock for blob pool
- Lock for metadata/header
- **Pros**: Better granularity than global, simpler than per-bucket
- **Cons**: Need to acquire multiple locks (deadlock risk), more complex
- **Use case**: Moderate contention, want some parallelism

### Recommended: Read-Write Lock (Option 2)
For a cache with read-heavy workload:
- **Read operations**: Acquire read lock (shared)
- **Write operations**: Acquire write lock (exclusive)
- **Migration**: Acquire write lock (exclusive, blocks all operations)

**Rationale**:
- Caches are typically read-heavy (80-90% reads)
- Read-write lock allows concurrent reads
- Simpler than fine-grained locking
- Migration is infrequent, so blocking is acceptable

### Lock Implementation Options

#### Python multiprocessing.Lock / RLock
- **Pros**: Built-in, cross-platform, process-safe
- **Cons**: May have overhead, not optimized for shared memory

#### Python multiprocessing.Semaphore
- Can implement RWLock with semaphores
- **Pros**: Flexible, built-in
- **Cons**: Need to implement RWLock logic yourself

#### Custom RWLock in SharedMemory
- Implement RWLock directly in shared memory header
- Use atomic operations (if available) or locks
- **Pros**: Fast, no extra objects
- **Cons**: More complex, need to implement correctly

#### Third-party: `readerwriterlock` or similar
- External library for RWLock
- **Pros**: Well-tested, optimized
- **Cons**: Extra dependency

### Lock Acquisition Patterns

#### Read Operation (GET)
```
1. Acquire read lock
2. Check segment version (if changed, migrate)
3. Hash key -> lookup in hash table
4. Read key from storage (validate address)
5. Read content fingerprint -> lookup content
6. Read content from storage
7. Release read lock
```

#### Write Operation (SET)
```
1. Acquire write lock
2. Check segment version (if changed, migrate)
3. Check high water mark (trigger migration if needed)
4. Hash key -> probe hash table
5. If key exists:
   - Get existing key_address (key deduplication)
   - Update content fingerprint in place
6. If new key:
   - Append key to blob pool (with type=KEY), get key_address
   - Hash content -> check if content exists
   - If content exists: get existing content_address (content deduplication)
   - If new content: append to blob pool (with type=CONTENT), get content_address
   - Insert new entry in key hash table
7. Update append pointers
8. If at capacity: evict LRU items
9. Release write lock
```

#### Migration Operation (Blocking - **CHOSEN APPROACH**)
**Decision**: Use blocking migration for initial implementation. Simpler, fewer edge cases, migration is infrequent.

```
1. Acquire write lock (exclusive, blocks all operations)
2. Create new SharedMemory segment
3. Run sweep to identify referenced items
4. Copy referenced keys into new key segment
5. Copy referenced content blobs into new content segment
6. Update hash tables with new addresses
7. Atomically update segment_version and segment_name in header
8. Release write lock
9. (Background) Cleanup old segment after timeout
```

**Characteristics**:
- **All operations blocked** during migration (reads and writes)
- **Simple**: No queue management, no consistency issues
- **Safe**: No race conditions, no lost writes
- **Acceptable**: Migration is infrequent, so brief blocking is fine

#### Migration Operation (Non-Blocking with Write Queue - Advanced)
```
1. Set migration_in_progress flag (atomic)
2. Create new SharedMemory segment
3. Run sweep to identify referenced items (reads can continue from old segment)
4. Copy referenced keys into new key segment
5. Copy referenced content blobs into new content blob segment
6. Acquire write lock (briefly, for atomic update)
7. Atomically update segment_version and segment_name
8. Backfill changes from write queue into new segment
9. Clear migration_in_progress flag
10. Release write lock
11. (Background) Cleanup old segment after timeout
```

**During Migration**:
- **Reads**: Continue from old segment (no lock needed, just check migration flag)
- **Writes**: Queue operations (key, value, operation type) instead of applying immediately
- **Queue location**: Could be in-memory per-process, or small shared memory segment

### Write-Queue Migration: Trade-offs Analysis

#### Benefits
1. **Non-blocking reads**: Reads continue during migration (better user experience)
2. **Better throughput**: Migration doesn't block read operations
3. **Write batching**: Queued writes can be applied efficiently in batch
4. **Lower latency**: Read operations don't wait for migration

#### Downsides & Challenges

1. **Write Queue Storage**
   - Where to store the queue? Options:
     - **Per-process in-memory**: Simple, but lost if process crashes
     - **Shared memory queue**: Survives crashes, but needs coordination
     - **Size limits**: What if queue fills up? Need backpressure
   - **Memory overhead**: Queue consumes memory during migration

2. **Consistency & Ordering**
   - **Read-after-write**: If write is queued, subsequent read from same process might not see it (reads from old segment)
   - **Cross-process consistency**: Process A queues write, Process B reads - won't see queued write
   - **Ordering**: Need to preserve write order when backfilling
   - **Solution**: Could check queue on reads, but adds complexity

3. **Queue Size Management**
   - **Bounded queue**: Limit size, block writes when full (defeats purpose)
   - **Unbounded queue**: Risk of OOM if migration is slow
   - **Backpressure**: Need strategy when queue is full

4. **Race Conditions**
   - **Read during migration**: Reads old segment, but write might be queued
   - **Multiple migrations**: What if migration is triggered while another is in progress?
   - **Process crash**: Queued writes might be lost

5. **Backfill Complexity**
   - **Ordering**: Must apply writes in order (FIFO)
   - **Deduplication**: If same key written multiple times, only need last value
   - **Conflict resolution**: What if queued write conflicts with migrated data?
   - **Performance**: Backfill might be slow if queue is large

6. **Migration Detection**
   - **Flag checking**: Every read/write needs to check `migration_in_progress`
   - **Atomic flag**: Need atomic read/write for flag (memory barrier)
   - **False positives**: Flag might be set but migration not started yet

7. **Edge Cases**
   - **Migration during high write rate**: Queue might grow very large
   - **Long-running migration**: Queue accumulates, memory pressure
   - **Process dies during migration**: Queued writes lost (if per-process queue)

#### Implementation Considerations

**Write Queue Design Options**:

**Option A: Per-Process Queue (Simplest)**
- Each process maintains its own queue in local memory (e.g., `collections.deque`)
- **Pros**: Simple, no coordination needed, fast (no serialization)
- **Cons**: Lost on process crash, each process backfills separately

**Option B: Shared Memory Queue**
- Single queue in shared memory for all processes (custom implementation)
- **Pros**: Survives crashes, centralized
- **Cons**: Needs locking, coordination, size management, complex to implement

**Option C: Hybrid - Per-Process with Shared Coordination**
- Each process has local queue, but shared metadata tracks migration
- **Pros**: Balance of simplicity and coordination
- **Cons**: Still lose per-process queues on crash

**Option D: multiprocessing.Queue (Python Built-in)**
- Use Python's `multiprocessing.Queue` for write queue
- **Pros**:
  - Built-in, well-tested, process-safe
  - Handles serialization automatically (pickle)
  - Can be bounded (maxsize parameter)
  - Blocking/non-blocking operations (`put_nowait`, `get_nowait`)
  - Thread-safe operations
- **Cons**:
  - **Serialization overhead**: Pickling keys/values adds CPU and memory cost
  - **Separate mechanism**: Different from shared memory (uses pipes/sockets internally)
  - **Consumer coordination**: Need to decide which process consumes from queue
  - **Performance**: May be slower than in-memory queue due to IPC overhead
  - **Size limits**: Bounded queue blocks when full (need backpressure strategy)

**multiprocessing.Queue Implementation Details**:

**Setup**:
- Create queue once, share reference between processes (via manager or passed at init)
- Queue can be bounded: `queue = multiprocessing.Queue(maxsize=1000)`
- Or unbounded: `queue = multiprocessing.Queue()`

**During Migration**:
- **Writers** (all processes): `queue.put((key, value, operation_type), block=False)`
  - Use `block=False` to avoid blocking, handle `queue.Full` exception
- **Consumer** (migrating process): `queue.get(block=False)` until empty
  - Apply writes to new segment during backfill

**Coordination**:
- Queue needs to be accessible to all processes
- Could store queue reference in shared state or pass via manager
- Migrating process is responsible for consuming queue

**Backpressure**:
- If queue is full and `put_nowait` fails:
  - **Option 1**: Block and wait (defeats non-blocking purpose)
  - **Option 2**: Drop writes (data loss, not ideal)
  - **Option 3**: Fall back to blocking migration (safety valve)
  - **Option 4**: Increase queue size or trigger migration earlier

**Recommendation for multiprocessing.Queue**:
- **Good choice if**: Serialization overhead is acceptable, want built-in safety
- **Consider**: Bounded queue with reasonable size (e.g., 1000-10000 items)
- **Watch out for**: Pickle performance with large/complex objects
- **Alternative**: Could use `multiprocessing.SimpleQueue` (simpler, but unbounded)

**Read Consistency Strategy**:
- **Option 1**: Accept inconsistency - reads from old segment, writes queued (simplest)
- **Option 2**: Check queue on reads - if key in queue, use queued value (more complex)
- **Option 3**: Block reads for keys that are being written (defeats purpose)

**Decision**: **Use blocking migration (Option 1 - Blocking)**

**Rationale**:
- Simpler implementation with fewer edge cases
- Migration is infrequent (triggered at high water mark, e.g., 80-90% full)
- Brief blocking during migration is acceptable for cache use case
- Can optimize later with write-queue if migration becomes a bottleneck
- Avoids complexity of queue management, consistency issues, and backfill logic

**Future optimization**: If migration blocking becomes an issue, can implement write-queue approach (Option 2) with `multiprocessing.Queue` or per-process queues.

### Concurrency Considerations

#### Memory Ordering / Visibility
- Shared memory updates may not be immediately visible to other processes
- **Solution**: Use memory barriers or rely on lock acquire/release (which provide barriers)
- Python's multiprocessing locks should handle this, but verify

#### Lock-Free Alternatives (Advanced)
Could use lock-free data structures:
- Atomic compare-and-swap for hash table updates
- Lock-free hash tables
- **Pros**: No lock contention, potentially faster
- **Cons**: Much more complex, harder to debug, may not be worth it for cache

#### Deadlock Prevention
If using multiple locks:
- Always acquire in same order (e.g., hash_table_lock -> pool_lock)
- Use timeout on lock acquisition
- Consider lock ordering protocol

### Migration Coordination Locking
- **Migration lock**: Separate lock (or use write lock) to ensure only one process migrates
- **Process discovery**: May need separate coordination mechanism
- **Version checking**: Should be lock-free or very fast (atomic read)

### Performance Optimizations

#### Lock-Free Reads (Optimistic)
- Read without lock, validate after (compare-and-swap style)
- If validation fails, retry with lock
- **Trade-off**: Complexity vs. performance

#### Lock Elision
- For read operations, could use version numbers to detect conflicts
- Only acquire lock if version changes during operation
- **Trade-off**: More complex, may not be worth it

#### Background Migration Check
- Instead of checking version on every operation, check in background thread
- Reduces overhead on hot path
- **Trade-off**: Slight delay in migration detection (usually acceptable)

### Decisions:
- **Lock type**: Python `multiprocessing.RLock` (DECIDED)
- **Lock granularity**: Single RWLock for entire cache (DECIDED)
- **Migration**: Blocking (DECIDED) - all operations blocked during migration

### Lock Timeout Strategy

**Problem**: If a process crashes while holding the lock, other processes will wait forever (deadlock).

**Solution**: Use timeout on lock acquisition (DECIDED)

**Implementation**:
- Use `lock.acquire(timeout=X)` instead of blocking `lock.acquire()`
- If timeout expires, raise exception or retry
- **Default timeout**: TBD (e.g., 30 seconds, 60 seconds, or configurable)

**When it comes into play**:
- **Normal operation**: Lock should be held briefly (microseconds to milliseconds)
- **Migration**: Lock held longer (seconds), but migration is infrequent
- **Process crash**: If process crashes while holding lock, other processes will timeout
- **Timeout handling**:
  - Log warning/error
  - Retry operation (with backoff)
  - Or fail the operation gracefully

**Alternative approaches**:
- **No timeout**: Accept risk of deadlock if process crashes (simpler, but risky)
- **Watchdog**: Separate process monitors for stuck locks (more complex)
- **Timeout with retry**: Default approach (balanced)

**Decision**: Use timeout on lock acquisition with configurable timeout value (default TBD, e.g., 60 seconds)

## Implementation Changes from Design

This section documents changes made during implementation that differ from the original design document.

### 1. Key Hash Size

**Design**: Key hash was specified as 64-bit (8 bytes) for key lookup.

**Implementation**: Key hash uses 128-bit (16 bytes) via `xxhash.xxh128_digest()`, matching the content fingerprint size. The full 128-bit hash is used for slot calculation in the hash table, providing better distribution than using only 64 bits. The full 128-bit hash is stored in the key hash table entry.

**Rationale**:
- Simplifies implementation by using the same hash function output for both keys and content
- Provides better collision resistance by using all 128 bits for slot calculation
- Better hash distribution reduces clustering in hash tables
- Entry size increased from 32 bytes to 40 bytes (16 + 8 + 16)

**Impact**: Key hash table entries are 40 bytes instead of 32 bytes, requiring more memory but providing significantly better hash distribution and reduced collision clustering.

### 2. Memory Layout Order

**Design**: Layout order was: Header → Hash Tables → Blob Pool

**Implementation**: Layout order is: Header → Blob Pool → Hash Tables

**Rationale**:
- Allows blob pool to grow from the start of available space
- Hash tables are fixed-size and placed after the variable-size blob pool
- Simplifies size calculation: blob pool size = total_size - header - hash_tables

**Impact**: No functional impact, but affects memory layout visualization.

### 3. Header Fields

**Design**: Header included:
- `segment_name` (64 bytes, UTF-8, null-terminated) at offset 0x0010
- `lock_offset` at offset 0x00C8 for in-segment lock

**Implementation**:
- `segment_name` field not implemented (segment_version is stored but migration not implemented)
- Lock is separate `multiprocessing.RLock`, not stored in-segment
- Header uses simpler layout with only implemented fields

**Rationale**:
- Migration/compaction not yet implemented, so segment_name not needed
- Separate lock is simpler and avoids in-segment lock complexity
- Reduces header size and complexity

**Impact**:
- Header is smaller (512 bytes, with reserved space for future use)
- Migration functionality not available (future work)
- Lock coordination is simpler but requires separate lock object

### 4. Lock Timeout

**Design**: Lock timeout was marked as "TBD" (to be determined).

**Implementation**: Default lock timeout is 60.0 seconds, configurable via `lock_timeout` parameter.

**Rationale**:
- 60 seconds provides reasonable timeout for normal operations and migration
- Prevents deadlock if process crashes while holding lock
- Configurable to allow adjustment for different use cases

**Impact**: Operations will raise `CouldNotLockError` if lock cannot be acquired within timeout period.

### 5. LRU Eviction

**Design**: LRU eviction with proper tracking of least recently used items.

**Implementation**: Approximated LRU eviction using random sampling (similar to Redis's approximated LRU algorithm).

**Details**:
- Each key hash table entry includes an 8-byte timestamp (nanoseconds since epoch)
- Timestamps are updated on both `__getitem__` (read) and `__setitem__` (write) operations
- When eviction is needed, the `_evict_lru()` method:
  1. Randomly samples slots from the key hash table (default: 10 samples, configurable)
  2. Skips empty slots and tombstones
  3. Finds the slot with the oldest timestamp among the sampled slots
  4. Evicts that slot by clearing the entry
- This is an approximation because it doesn't check all entries, only a random sample

**Rationale**:
- True LRU would require checking all entries (O(n)), which is expensive for large caches
- Approximated LRU with sampling provides good-enough eviction behavior with O(samples) complexity
- Similar approach used by Redis, which has proven effective in practice
- Timestamps are stored inline in hash table entries (8 bytes overhead per entry)

**Impact**:
- Eviction behavior is good but not perfect (may occasionally evict recently used items if they're not in the sample)
- Performance is good (O(samples) instead of O(n) for true LRU)
- Suitable for production use, though true LRU could be added as an optimization if needed

### 6. Reference Counting and Garbage Collection

**Design**: Reference counting for content blobs to track how many keys reference each content fingerprint. Sweep operation to remove unreferenced content.

**Implementation**: Explicit reference counting not implemented, but compaction handles cleanup of unreferenced blobs.

**Details**:
- Content blobs are not explicitly reference-counted
- Instead, compaction collects all referenced content fingerprints from the key hash table
- Only content blobs whose fingerprints appear in referenced set are preserved during compaction
- Unreferenced content blobs are automatically removed during compaction

**Rationale**:
- Explicit reference counting would require additional metadata and complexity
- Compaction-based cleanup is simpler and sufficient for most use cases
- Compaction can be run periodically or on-demand to reclaim space

**Impact**:
- Orphaned content blobs accumulate until compaction is run
- Compaction must be called periodically to prevent blob pool from filling up
- Not a memory leak per se, but requires periodic compaction for long-running workloads
- Suitable for production use with periodic compaction

### 7. Migration and Compaction

**Design**: Migration-based compaction strategy with segment versioning, process coordination, and atomic segment switching.

**Implementation**: In-place compaction is implemented via `compact()` method. Migration-based compaction with segment versioning is not implemented.

**Compaction Implementation**:
- **In-place compaction**: The `compact()` method performs in-place defragmentation of the blob pool
- **Process**: Collects referenced addresses, sorts them, moves blobs sequentially, updates hash table addresses, zeros remainder
- **Thread-safe**: Requires exclusive lock (blocks all operations during compaction)
- **On-demand**: Must be called explicitly; no automatic triggering
- **Handles both keys and content**: Single unified blob pool is compacted

**Migration Not Implemented**:
- No segment versioning or process coordination
- No way to resize cache or migrate to new segment
- Cache size is fixed at initialization

**Rationale**:
- In-place compaction is simpler than migration-based approach
- Sufficient for reclaiming space from deleted/orphaned items
- Migration would require complex process coordination
- Can be added as future enhancement if needed

**Impact**:
- Space can be reclaimed via `compact()` method
- Compaction must be called periodically or on-demand
- Cache size cannot be changed after initialization
- Suitable for production use with periodic compaction

### 8. Hash Table Load Factor Enforcement

**Design**: When load factor exceeds 65%, trigger migration/compaction to resize hash tables.

**Implementation**: Load factor is used only for initial size calculation. No enforcement or resizing when load factor is exceeded.

**Rationale**:
- Migration not implemented, so resizing not possible
- Hash tables are fixed-size based on initial maxsize
- Will fail inserts when tables are full (via RuntimeError)

**Impact**:
- Cache may fail with "Key hash table full" or "Content hash table full" errors
- No automatic resizing or compaction
- Must size cache appropriately upfront

### 9. Blob Pool Bounds Validation

**Design**: No explicit mention of bounds checking for blob pool pointer.

**Implementation**: Added validation in `_append_blob()` to check that `next_ptr` is within valid bounds before use.

**Rationale**:
- Defensive programming to catch corruption early
- Prevents memory corruption or crashes from invalid pointers
- Provides better error messages for debugging

**Impact**: Better error handling and safety, but adds small overhead to append operations.

### 10. Constants Extraction

**Design**: Magic numbers used throughout (e.g., blob type width, length width).

**Implementation**: Extracted magic numbers into named constants:
- `BLOB_TYPE_WIDTH = 1`
- `BLOB_LENGTH_WIDTH = 4`
- `BLOB_HEADER_SIZE = BLOB_TYPE_WIDTH + BLOB_LENGTH_WIDTH`

**Rationale**:
- Improves code maintainability
- Makes blob format explicit and self-documenting
- Easier to modify if format changes

**Impact**: Code is more maintainable and self-documenting.

### 11. Tombstone Markers

**Design**: Mentioned tombstone markers for deleted entries but didn't specify implementation details.

**Implementation**: Tombstone markers implemented using `0xFF` repeated 16 times (matching `KEY_HASH_WIDTH`).

**Details**:
- When a key is deleted, the hash field is set to `TOMBSTONE` (`b"\xFF" * 16`)
- Rest of the entry is zeroed out
- Tombstones preserve linear probe chains for other keys
- Tombstones are treated as occupied for probing but available for insertion
- Tombstones are skipped during iteration and compaction

**Rationale**:
- Prevents breaking linear probe chains when entries are deleted
- Essential for correctness of hash table operations with linear probing
- Allows reuse of deleted slots for new insertions

**Impact**: Correct deletion behavior in hash tables with linear probing.

### Summary of Implementation Status

**Implemented Features**:
1. ✅ **Approximated LRU Eviction**: Random sampling with timestamp tracking
2. ✅ **In-Place Compaction**: Defragmentation of blob pool with address remapping
3. ✅ **Tombstone Markers**: Proper deletion handling for linear probing hash tables
4. ✅ **Timestamp Tracking**: Last access time stored in key hash table entries

**Not Yet Implemented** (Future Enhancements):
1. **Migration-Based Compaction**: Segment versioning and process coordination for resizing
2. **Load Factor Enforcement**: Automatic resizing when tables fill up
3. **Automatic Compaction Triggering**: Background thread or threshold-based compaction
4. **True LRU Eviction**: Check all entries instead of sampling (may not be necessary)

**Production Readiness**:
The cache is suitable for production use with the following considerations:
- Periodic compaction should be scheduled to reclaim space
- Cache size should be sized appropriately at initialization (cannot be resized)
- Approximated LRU provides good eviction behavior for most workloads
