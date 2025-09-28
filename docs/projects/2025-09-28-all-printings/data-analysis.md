# Data Size Analysis for All Printings Project

**Document:** Data Size Analysis  
**Project:** 2025-09-28 All Printings  
**Last Updated:** 2025-09-28

## Table of Contents

1. [Scryfall Bulk Data Analysis](#scryfall-bulk-data-analysis)
2. [Storage Impact Projections](#storage-impact-projections)
3. [Performance Implications](#performance-implications)
4. [Cost Analysis](#cost-analysis)
5. [Optimization Strategies](#optimization-strategies)

## Scryfall Bulk Data Analysis

### Current Bulk Data Sizes (2025)

**Scryfall Bulk Data Downloads:**
- **Oracle Cards:** 156.7 MB (~31,000 unique card names)
- **Unique Artwork:** 225.1 MB (~45,000 unique artworks)  
- **Default Cards:** 490.2 MB (~95,000 standard printings)
- **All Cards:** 2,281.5 MB (~150,000+ total objects including tokens)

**Key Ratios:**
- **Default to Oracle:** 3.13x size increase
- **All Cards to Oracle:** 14.6x size increase
- **Unique Artwork to Oracle:** 1.44x size increase

### Recommended Implementation Approach

Based on this analysis, we recommend a **phased approach**:

1. **Phase 1:** Default Cards (~3.13x increase) - Most practical
2. **Phase 2:** Unique Artwork support (additional artwork metadata)
3. **Future:** All Cards consideration (14.6x increase requires significant infrastructure)

## Storage Impact Projections

### Current Scryfall OS Database
- **Estimated current size:** ~2GB (based on Oracle cards equivalent)
- **Current card count:** ~31,000 unique cards
- **Average card record size:** ~65KB including indexes and metadata

### Projected Storage Requirements

**Phase 1: Default Cards Implementation**
- **Raw data size:** 490.2 MB (JSON from Scryfall)
- **Database storage:** ~6.3GB (3.13x current)
- **Index overhead:** ~1.5GB additional
- **Total storage requirement:** ~7.8GB
- **Card count:** ~95,000 printings

**Phase 2: All Cards Implementation (Future)**
- **Raw data size:** 2,281.5 MB (JSON from Scryfall)
- **Database storage:** ~29.2GB (14.6x current)
- **Index overhead:** ~8GB additional  
- **Total storage requirement:** ~37GB
- **Card count:** ~150,000+ objects

### Storage Breakdown by Component

```
Current (Oracle Cards):
├── Card data: ~1.6GB
├── Indexes: ~0.3GB
└── Metadata: ~0.1GB
Total: ~2GB

Phase 1 (Default Cards):
├── Card data: ~5.0GB  
├── Indexes: ~1.5GB
├── Oracle ID mappings: ~0.3GB
├── Artwork ID mappings: ~0.3GB
├── Release date indexes: ~0.2GB
└── Metadata: ~0.5GB
Total: ~7.8GB

Phase 2 (All Cards):
├── Card data: ~23GB
├── Indexes: ~8GB
├── Token/special data: ~4GB  
├── Complex relationships: ~1GB
└── Metadata: ~1GB
Total: ~37GB
```

## Performance Implications

### Query Performance Impact

**Expected Performance Changes:**
- **unique:cards queries:** Minimal impact (uses DISTINCT ON with proper indexing)
- **unique:art queries:** ~20% slower (additional artwork_id joins)
- **unique:prints queries:** 3x more data returned, proportional bandwidth impact
- **Complex searches:** May see 15-25% performance degradation

### Memory and Cache Requirements

**Current Cache Performance:**
- TTL cache hit rate: ~85% for common searches
- Memory usage: ~512MB for search cache
- Query cache size: ~1000 entries average

**Projected Cache Requirements:**
- Memory usage: ~1.6GB for search cache (3.13x)
- Cache entry count: Need larger cache for reasonable hit rates
- Cache segmentation by unique mode needed

### Network and API Impact

**Data Transfer Volumes:**
- Current avg search result: ~15KB (20 cards average)
- unique:prints results: ~47KB (60+ cards average) 
- API response times: Additional 10-20ms for larger payloads

## Cost Analysis

### Infrastructure Costs

**Development Phase:**
- Database migration effort: 40-60 hours
- API development: 80-120 hours
- Testing and validation: 40-60 hours
- **Total development:** ~160-240 hours

**Operational Costs (Annual):**
- Additional storage (6GB): ~$50-100/year
- Increased compute (20% higher): ~$200-400/year  
- Bandwidth (3x search responses): ~$100-200/year
- **Total operational increase:** ~$350-700/year

**Break-even Analysis:**
- One-time development cost: ~$20,000-30,000 
- Ongoing operational cost: ~$350-700/year
- User value: Enhanced search capability, complete card database

## Optimization Strategies

### Storage Optimization

**1. Compression Strategy**
```sql
-- Use JSONB compression for raw card blobs
ALTER TABLE magic.cards ALTER COLUMN raw_card_blob 
SET STORAGE EXTERNAL;  -- Force compression
```

**2. Partitioning by Set**
```sql
-- Partition table by set release year for better performance
CREATE TABLE magic.cards_2023 PARTITION OF magic.cards
FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
```

**3. Selective Indexing**
```sql
-- Create partial indexes only for commonly queried data
CREATE INDEX idx_cards_modern_legal 
ON magic.cards (card_name) 
WHERE card_legalities @> '{"modern": "legal"}';
```

### Query Optimization

**1. Materialized Views for Common Queries**
```sql
-- Pre-computed unique:cards view
CREATE MATERIALIZED VIEW magic.unique_cards AS
SELECT DISTINCT ON (card_oracle_id) *
FROM magic.cards
ORDER BY card_oracle_id, release_date DESC NULLS LAST;

-- Refresh periodically
REFRESH MATERIALIZED VIEW CONCURRENTLY magic.unique_cards;
```

**2. Query Result Caching**
```python
# Cache by query signature including unique/prefer options
cache_key = f"{base_query}#{unique_mode}#{prefer_mode}"
cached_result = search_cache.get(cache_key)
```

**3. Connection Pooling Optimization**
```python
# Increase connection pool for higher query volume
psycopg_pool.ConnectionPool(
    min_size=10,    # Increased from 5
    max_size=50,    # Increased from 20
    max_idle=300    # 5 minute idle timeout
)
```

### Monitoring and Alerting

**Key Metrics to Track:**
- Database size growth rate
- Query performance by unique mode
- Cache hit rates by query type
- API response time percentiles
- Storage utilization alerts

**Performance SLA Targets:**
- **Search query execution:** < 50ms (P95)
- **Full HTTP request:** < 70ms (P95) 
- **Cache hit rate:** > 80%
- **Database growth:** < 10GB/month

### Rollback Strategy

**Emergency Performance Rollback:**
```sql
-- Quick rollback to unique:cards only mode
UPDATE magic.cards SET card_deprecated = true
WHERE card_printing_id NOT IN (
    SELECT DISTINCT ON (card_oracle_id) card_printing_id
    FROM magic.cards
    ORDER BY card_oracle_id, release_date DESC NULLS LAST
);

-- Hide deprecated cards in queries
ALTER TABLE magic.cards ADD CONSTRAINT active_cards_only
CHECK (card_deprecated = false OR card_deprecated IS NULL);
```

## Recommendations

### Phase 1 Implementation (Recommended)
- **Target:** Default Cards dataset (3.13x growth)
- **Timeline:** Q1 2026
- **Risk:** Medium - manageable size increase
- **Value:** High - covers vast majority of user needs

### Infrastructure Preparation
1. Provision additional 8GB database storage
2. Upgrade cache memory allocation to 2GB
3. Implement query performance monitoring
4. Set up database partition strategy

### Success Criteria
- Search performance remains under 50ms P95
- Database size stays under 10GB total
- Cache hit rate maintains above 80%
- Zero data consistency issues

This analysis supports moving forward with Phase 1 (Default Cards) implementation while preparing infrastructure for the increased data volume.