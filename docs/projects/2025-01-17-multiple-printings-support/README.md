# Multiple Card Printings and Unique Search Modes Support

**Project Start Date**: 2025-01-17  
**Status**: Design Phase  
**Priority**: High  

## Table of Contents

1. [Project Overview](#project-overview)
2. [Current State Analysis](#current-state-analysis)
3. [Data Size Estimates](#data-size-estimates)
4. [Database Schema Changes](#database-schema-changes)
5. [API Design](#api-design)
6. [Performance Requirements](#performance-requirements)
7. [Implementation Phases](#implementation-phases)
8. [Risk Assessment](#risk-assessment)
9. [Migration Strategy](#migration-strategy)

## Project Overview

This project extends Scryfall OS to support ingesting all printings of Magic: The Gathering cards and provides users with flexible search modes to view results by unique cards, unique artwork, or all printings.

### Goals

- **Primary**: Ingest all printings from Scryfall's `default_cards` dataset instead of just `oracle_cards`
- **Secondary**: Support three unique search modes:
  - `unique=cards` (default) - One result per unique card (Oracle ID)
  - `unique=art` - One result per unique artwork
  - `unique=prints` - All printings/versions of matching cards
- **Tertiary**: Maintain sub-50ms search latency and sub-70ms HTTP response time

### Success Criteria

- All ~800k+ card printings successfully ingested and searchable
- Three unique modes working correctly with proper deduplication
- Performance targets maintained under increased data load
- Backwards compatibility with existing API clients

## Current State Analysis

### Existing Data Model

Currently, Scryfall OS uses the `oracle_cards` bulk data set which contains:
- **~92k cards** (164MB compressed)
- One card per Oracle ID (unique game piece)
- Most recent/recognizable printing selected by Scryfall
- Single `magic.cards` table with unique constraint on `card_name`

### Current Database Schema

The existing `magic.cards` table structure:
```sql
CREATE TABLE magic.cards (
    card_name text NOT NULL,
    cmc integer,
    mana_cost_text text,
    raw_card_blob jsonb NOT NULL,
    -- ... additional card attributes
    CONSTRAINT cards_name_unique UNIQUE (card_name)
);
```

### Limitations of Current Approach

1. **Missing Printings**: Users cannot search for specific printings, alternate arts, or promotional versions
2. **Limited Context**: No set information, collector numbers, or printing-specific attributes
3. **Incomplete Coverage**: Some cards may be missing if they only exist in older or non-English printings

## Data Size Estimates

Based on Scryfall bulk data analysis:

| Dataset | Cards | Size (MB) | Ratio vs Oracle |
|---------|--------|-----------|----------------|
| oracle_cards | ~92k | 164 | 1.0x |
| default_cards | ~800k+ | 514 | 8.7x cards, 3.1x size |
| unique_artwork | ~450k | 236 | 4.9x cards, 1.4x size |

### Storage Requirements

- **Current**: ~164MB for Oracle cards
- **Proposed**: ~514MB for all default printings
- **Net Increase**: ~350MB (3.1x current storage)

### Performance Impact Estimate

With ~8.7x more rows:
- **Index Size**: Proportional increase (~8.7x)
- **Query Performance**: Minimal impact due to indexed searches
- **Memory Usage**: Increased cache requirements for hot data

## Database Schema Changes

### New Schema Design

Replace single cards table with normalized structure:

```sql
-- Core card definitions (Oracle IDs)
CREATE TABLE magic.oracle_cards (
    oracle_id uuid PRIMARY KEY,
    name text NOT NULL,
    mana_cost text,
    cmc integer,
    type_line text,
    oracle_text text,
    power text,
    toughness text,
    loyalty text,
    -- Computed attributes for searching
    colors jsonb NOT NULL DEFAULT '{}'::jsonb,
    color_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
    keywords jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Constraints similar to current cards table
    CONSTRAINT colors_valid CHECK (colors <@ '{"W":true,"U":true,"B":true,"R":true,"G":true,"C":true}'::jsonb)
);

-- Individual printings
CREATE TABLE magic.card_printings (
    id uuid PRIMARY KEY,
    oracle_id uuid NOT NULL REFERENCES magic.oracle_cards(oracle_id),
    name text NOT NULL, -- Can vary slightly from oracle name
    set_code text NOT NULL,
    collector_number text NOT NULL,
    collector_number_int integer, -- Numeric version for comparisons
    rarity text NOT NULL,
    artist text,
    flavor_text text,
    watermark text,
    border_color text,
    frame text,
    layout text,
    
    -- Pricing and market data
    prices jsonb DEFAULT '{}'::jsonb,
    
    -- Image and artwork identifiers
    illustration_id uuid, -- Groups cards with same artwork
    image_status text,
    image_uris jsonb,
    
    -- Legality and format information
    legalities jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Full Scryfall response for completeness
    scryfall_data jsonb NOT NULL,
    
    -- Metadata
    released_at date,
    created_at timestamp with time zone DEFAULT NOW(),
    updated_at timestamp with time zone DEFAULT NOW(),
    
    CONSTRAINT unique_printing UNIQUE (set_code, collector_number),
    CONSTRAINT valid_rarity CHECK (rarity IN ('common', 'uncommon', 'rare', 'mythic', 'special', 'bonus'))
);

-- Indexes for performance
CREATE INDEX idx_card_printings_oracle_id ON magic.card_printings(oracle_id);
CREATE INDEX idx_card_printings_name_gin ON magic.card_printings USING gin(to_tsvector('english', name));
CREATE INDEX idx_card_printings_set_code ON magic.card_printings(set_code);
CREATE INDEX idx_card_printings_artist_gin ON magic.card_printings USING gin(to_tsvector('english', artist));
CREATE INDEX idx_card_printings_illustration_id ON magic.card_printings(illustration_id);
CREATE INDEX idx_card_printings_rarity ON magic.card_printings(rarity);

-- View for backwards compatibility
CREATE VIEW magic.cards AS
SELECT DISTINCT ON (oracle_id)
    oc.name as card_name,
    oc.cmc,
    oc.mana_cost as mana_cost_text,
    oc.colors as card_colors,
    oc.color_identity as card_color_identity,
    oc.keywords as card_keywords,
    oc.oracle_text,
    cp.scryfall_data as raw_card_blob
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
ORDER BY oracle_id, cp.released_at DESC;
```

### Migration Considerations

- **Backwards Compatibility**: Maintain `magic.cards` view for existing queries
- **Data Migration**: Script to populate new tables from `default_cards` bulk data
- **Index Strategy**: Carefully planned indexes to maintain query performance

## API Design

### New Search Parameter

Add `unique` parameter to `/search` endpoint:

```
GET /search?q=lightning%20bolt&unique=cards    # Default - one per Oracle ID
GET /search?q=lightning%20bolt&unique=art      # One per unique artwork
GET /search?q=lightning%20bolt&unique=prints   # All printings
```

### Response Format Changes

Enhanced response structure:

```json
{
  "cards": [
    {
      "oracle_id": "...",
      "name": "Lightning Bolt",
      "printing_id": "...",
      "set": "LEA",
      "collector_number": "161",
      "rarity": "common",
      "artist": "Christopher Rush",
      "illustration_id": "...",
      "unique_rank": 1,
      // ... other attributes
    }
  ],
  "unique_mode": "cards",
  "total_cards": 1,
  "total_printings": 847
}
```

### Query Generation Logic

Modify SQL generation based on unique mode:

```sql
-- unique=cards (default)
SELECT DISTINCT ON (oracle_id) 
  cp.*, oc.*
FROM magic.card_printings cp
JOIN magic.oracle_cards oc USING (oracle_id)
WHERE [search_conditions]
ORDER BY oracle_id, cp.released_at DESC;

-- unique=art  
SELECT DISTINCT ON (illustration_id)
  cp.*, oc.*
FROM magic.card_printings cp
JOIN magic.oracle_cards oc USING (oracle_id)
WHERE [search_conditions] AND illustration_id IS NOT NULL
ORDER BY illustration_id, cp.released_at DESC;

-- unique=prints
SELECT cp.*, oc.*
FROM magic.card_printings cp
JOIN magic.oracle_cards oc USING (oracle_id)
WHERE [search_conditions]
ORDER BY cp.released_at DESC;
```

## Performance Requirements

### Target Metrics

- **Search Query Execution**: < 50ms
- **HTTP Response Time**: < 70ms total
- **Throughput**: Handle existing load with 3x data size

### Optimization Strategies

1. **Smart Indexing**: GIN indexes for text search, B-tree for common filters
2. **Query Planning**: DISTINCT ON optimizations for unique modes
3. **Caching**: Maintain existing TTL cache for popular queries  
4. **Connection Pooling**: Scale database connections as needed

### Monitoring Plan

- Add metrics for query execution times by unique mode
- Monitor index usage and query plan changes
- Track memory usage patterns with larger dataset

## Implementation Phases

### Phase 1: Database Schema (Week 1-2)
- [ ] Design and review new schema
- [ ] Create migration scripts
- [ ] Implement backwards compatibility view
- [ ] Test data ingestion with subset

### Phase 2: Data Ingestion (Week 2-3)
- [ ] Modify bulk data import to use `default_cards`
- [ ] Implement incremental updates
- [ ] Create data validation and integrity checks
- [ ] Full dataset import and verification

### Phase 3: API Enhancement (Week 3-4)
- [ ] Add `unique` parameter support
- [ ] Modify query generation logic
- [ ] Update response format
- [ ] Comprehensive API testing

### Phase 4: Performance Optimization (Week 4-5)
- [ ] Performance testing with full dataset
- [ ] Index optimization based on real queries
- [ ] Cache tuning and optimization
- [ ] Load testing and monitoring

### Phase 5: Documentation and Deployment (Week 5-6)
- [ ] Update API documentation
- [ ] Create migration guide
- [ ] Production deployment strategy
- [ ] User communication and rollout

## Risk Assessment

### High Risks
1. **Performance Degradation**: 8x more data may slow queries
   - **Mitigation**: Careful indexing, query optimization, performance testing
2. **Storage Costs**: 3x storage increase
   - **Mitigation**: Monitor costs, optimize data storage formats

### Medium Risks  
3. **Migration Complexity**: Database schema changes are complex
   - **Mitigation**: Thorough testing, backwards compatibility
4. **API Breaking Changes**: New response format may break clients
   - **Mitigation**: Maintain backwards compatibility, versioned responses

### Low Risks
5. **Data Quality Issues**: More data sources may introduce inconsistencies
   - **Mitigation**: Data validation, automated testing

## Migration Strategy

### Pre-Migration
- [ ] Full database backup
- [ ] Performance baseline establishment
- [ ] Rollback procedure documentation

### Migration Steps
1. Deploy new schema alongside existing tables
2. Import `default_cards` data into new structure  
3. Validate data integrity and performance
4. Update API to use new schema with feature flags
5. Gradual rollout to users
6. Remove old tables after validation period

### Rollback Plan
- Maintain old schema during transition period
- Feature flags to quickly revert to old behavior
- Database restore procedures documented and tested

## Project Documentation

This project includes comprehensive documentation across multiple areas:

### Core Design Documents
- **[Database Schema Design](database-schema.md)** - Detailed database changes, new table structures, and migration approach
- **[API Design](api-design.md)** - API enhancements, new parameters, response formats, and backwards compatibility
- **[Performance Analysis](performance-analysis.md)** - Performance impact assessment, optimization strategies, and monitoring plans
- **[Implementation Plan](implementation-plan.md)** - Detailed 6-week implementation timeline with tasks and deliverables

### Key Design Decisions

#### Database Architecture
- **Normalized Schema**: Separate `oracle_cards` (card definitions) and `card_printings` (individual versions) tables
- **Backwards Compatibility**: Maintain existing `magic.cards` view for current API clients
- **Performance First**: Carefully designed indexes to support sub-50ms query times

#### API Design  
- **Unique Modes**: Support for `unique=cards/art/prints` parameter with different deduplication logic
- **Enhanced Response**: Include both oracle data and printing-specific information
- **Backwards Compatible**: Default behavior unchanged, existing clients continue working

#### Data Strategy
- **Bulk Data Migration**: Move from `oracle_cards` (~92k cards) to `default_cards` (~800k printings)
- **Storage Growth**: ~3x storage increase with proportional performance optimization
- **Incremental Updates**: Support for ongoing data synchronization

---

**Next Steps**: Review design with team, validate technical approach, and proceed with Phase 1 implementation as outlined in the [Implementation Plan](implementation-plan.md).