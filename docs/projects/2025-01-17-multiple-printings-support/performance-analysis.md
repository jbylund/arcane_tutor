# Performance Analysis for Multiple Printings Support

This document analyzes the performance implications of supporting multiple card printings and provides strategies for maintaining target response times.

## Performance Requirements

### Target Metrics
- **Search Query Execution**: < 50ms
- **HTTP Response Time**: < 70ms total  
- **Concurrent Users**: Support existing load levels
- **Memory Usage**: Reasonable increase proportional to data growth

### Current Baseline
- **Data Size**: ~92k cards, ~164MB
- **Query Performance**: Most searches complete in 10-30ms
- **Index Size**: ~50MB for primary indexes
- **Cache Hit Rate**: ~80% for popular queries

## Data Growth Impact

### Size Comparison

| Metric | Current (Oracle) | Proposed (Default) | Growth Factor |
|--------|------------------|-------------------|---------------|
| Total Records | ~92k | ~800k | 8.7x |
| Raw Data Size | 164MB | 514MB | 3.1x |
| Index Overhead | ~50MB | ~200MB | 4.0x |
| Total Storage | ~214MB | ~714MB | 3.3x |

### Storage Analysis

**Normalized Schema Benefits**:
- Oracle cards: 92k × ~2KB = 184MB (vs 180MB estimated)
- Card printings: 800k × ~0.7KB = 560MB (vs 550MB estimated)
- Reduced duplication of oracle text, mana costs across printings

**Index Strategy**:
```sql
-- High-impact indexes for search performance
CREATE INDEX idx_oracle_cards_name_gin ON magic.oracle_cards USING gin(to_tsvector('english', name));
CREATE INDEX idx_oracle_cards_oracle_text_gin ON magic.oracle_cards USING gin(to_tsvector('english', oracle_text));
CREATE INDEX idx_card_printings_oracle_id ON magic.card_printings(oracle_id);
CREATE INDEX idx_card_printings_set_code ON magic.card_printings(set_code);
CREATE INDEX idx_card_printings_artist_gin ON magic.card_printings USING gin(to_tsvector('english', artist));

-- Specialized indexes for unique modes
CREATE INDEX idx_card_printings_illustration_id ON magic.card_printings(illustration_id) 
    WHERE illustration_id IS NOT NULL;
CREATE INDEX idx_card_printings_released_at ON magic.card_printings(released_at);

-- Composite indexes for common query patterns
CREATE INDEX idx_oracle_printings_composite ON magic.card_printings(oracle_id, released_at DESC, set_code);
```

## Query Performance Analysis

### Query Pattern Comparison

#### `unique=cards` (Default Mode)

**Query Structure**:
```sql
SELECT DISTINCT ON (oc.oracle_id) ...
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE [search_conditions]
ORDER BY oc.oracle_id, cp.released_at DESC
```

**Performance Characteristics**:
- **Best Case**: Text search hits oracle_cards index → fast oracle_id lookup → recent printing
- **Typical**: 10-25ms (similar to current performance)
- **Worst Case**: Complex filters requiring full table scan → 100-200ms
- **Optimization**: DISTINCT ON with proper ordering is very efficient in PostgreSQL

#### `unique=art` Mode

**Query Structure**:
```sql
SELECT DISTINCT ON (cp.illustration_id) ...
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE [search_conditions] AND cp.illustration_id IS NOT NULL
ORDER BY cp.illustration_id, cp.released_at DESC
```

**Performance Characteristics**:
- **Challenge**: Must scan printings table first
- **Mitigation**: Specialized index on illustration_id with WHERE clause
- **Expected**: 15-35ms for typical searches
- **Risk**: Complex text searches may be slower than cards mode

#### `unique=prints` Mode

**Query Structure**:
```sql
SELECT ...
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE [search_conditions]
ORDER BY oc.name, cp.released_at DESC
```

**Performance Characteristics**:
- **Advantage**: No DISTINCT ON processing required
- **Challenge**: Can return many more results (8x average)
- **Expected**: 20-40ms for query execution, but larger result set
- **Mitigation**: Lower default limit (50 vs 100), pagination

### Join Performance

**Oracle → Printings Join**:
- **Selectivity**: High - most queries filter significantly
- **Index Strategy**: Foreign key index on `card_printings.oracle_id`
- **Memory Impact**: Join hash table size increases with data

**Estimated Join Costs**:
- **Current**: ~50k rows joined on average
- **Proposed**: ~200k rows joined on average (4x)
- **Mitigation**: Better query selectivity, index-only scans where possible

## Memory and Caching Strategy

### Database Cache Impact

**Shared Buffers Sizing**:
- **Current**: 256MB recommended
- **Proposed**: 512MB-1GB recommended
- **Justification**: More hot data, larger indexes

**Query Plan Cache**:
- Plans will be more complex (2-table joins vs 1-table)
- Plan cache hit rate may decrease initially
- Stable workload should reach similar hit rates

### Application Cache Strategy

**Current Caching**:
```python
@cached(cache=TTLCache(maxsize=1000, ttl=60))
def _search(self, query: str, limit: int = 100) -> dict:
```

**Enhanced Caching**:
```python
# Separate cache keys by unique mode
cache_key = f"{query_hash}:{unique_mode}:{limit}:{offset}"

# Different cache sizes by mode
caches = {
    'cards': TTLCache(maxsize=2000, ttl=60),    # Larger cache for default mode
    'art': TTLCache(maxsize=1000, ttl=60),      # Medium cache for art mode  
    'prints': TTLCache(maxsize=500, ttl=30)     # Smaller cache, shorter TTL for prints
}
```

## Performance Testing Strategy

### Load Testing Scenarios

#### Baseline Performance Test
```python
# Test current performance with existing queries
test_queries = [
    "lightning bolt",
    "cmc:3 color:red",
    "type:creature power:2",
    "format:modern rarity:mythic"
]

# Measure: query time, memory usage, cache hit rate
```

#### Scalability Test
```python
# Test with new schema and full dataset
for unique_mode in ['cards', 'art', 'prints']:
    for query in test_queries:
        measure_performance(query, unique_mode)
        
# Target: <50ms query time, <70ms total response
```

#### Stress Test
```python
# Concurrent users test
concurrent_users = [10, 50, 100, 200]
for user_count in concurrent_users:
    run_concurrent_searches(user_count)
    measure_throughput_and_latency()
```

### Performance Monitoring

#### Key Metrics
```sql
-- Query performance monitoring
SELECT 
    query,
    calls,
    total_time,
    mean_time,
    rows,
    hit_percent
FROM pg_stat_statements 
WHERE query LIKE '%magic.oracle_cards%'
ORDER BY mean_time DESC;

-- Index usage monitoring  
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'magic'
ORDER BY idx_scan DESC;
```

#### Application Metrics
- Response time by unique mode
- Cache hit rate by mode
- Memory usage patterns
- Error rates for complex queries

## Optimization Strategies

### Query Optimization

#### Index-Only Scans
```sql
-- Design indexes to support index-only scans for common queries
CREATE INDEX idx_oracle_cards_search_coverage ON magic.oracle_cards(oracle_id, name, cmc, colors)
    WHERE conditions_for_common_searches;
```

#### Partial Indexes
```sql
-- Reduce index size for specialized searches
CREATE INDEX idx_printings_mythic_recent ON magic.card_printings(oracle_id, released_at DESC)
    WHERE rarity = 'mythic' AND released_at > '2020-01-01';
```

#### Statistics and Query Planning
```sql
-- Ensure accurate statistics for query planner
ANALYZE magic.oracle_cards;
ANALYZE magic.card_printings;

-- Consider increasing statistics target for key columns
ALTER TABLE magic.oracle_cards ALTER COLUMN name SET STATISTICS 1000;
ALTER TABLE magic.card_printings ALTER COLUMN oracle_id SET STATISTICS 1000;
```

### Application-Level Optimization

#### Response Streaming
```python
# For unique=prints mode with many results
async def stream_search_results(query: str, unique: str):
    async for batch in execute_search_query(query, unique, batch_size=50):
        yield batch
```

#### Lazy Loading
```python
# Load minimal data first, then enrich on demand
class SearchResult:
    def __init__(self, oracle_id: str, printing_id: str):
        self.oracle_id = oracle_id
        self.printing_id = printing_id
        self._printing_data = None
        
    @property
    def printing_data(self):
        if self._printing_data is None:
            self._printing_data = load_printing_data(self.printing_id)
        return self._printing_data
```

## Risk Mitigation

### Performance Degradation Risks

**Risk**: Query performance drops below 50ms target
**Mitigation**:
- Performance testing before deployment
- Query optimization during development
- Rollback plan to old schema if needed

**Risk**: Memory usage exceeds available resources
**Mitigation**:
- Memory monitoring and alerting
- Graceful degradation (disable caching if needed)
- Horizontal scaling options

### Monitoring and Alerting

```python
# Performance monitoring
@contextmanager
def measure_query_performance(query_type: str, unique_mode: str):
    start_time = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start_time
        metrics.histogram(f'query.duration.{query_type}.{unique_mode}', elapsed)
        
        if elapsed > 0.050:  # 50ms threshold
            logger.warning(f"Slow query: {elapsed:.3f}s for {query_type}:{unique_mode}")
```

## Expected Performance Outcomes

### Conservative Estimates
- **Cards mode**: 15-40ms (vs 10-30ms current) - 25% increase
- **Art mode**: 20-45ms - New functionality  
- **Prints mode**: 25-50ms - New functionality
- **Memory usage**: 2-3x increase in database cache

### Optimistic Estimates  
- **Cards mode**: 12-35ms - Better than current due to normalized schema
- **Art mode**: 15-40ms - Well-indexed artwork searches
- **Prints mode**: 20-45ms - Efficient joins with good selectivity

### Success Criteria
- 95th percentile response times under 70ms total
- No degradation in cache hit rates after 1 week
- Successful load testing at 2x current peak traffic