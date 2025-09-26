# Card Printings and Unique Search Modes Support

**Issue Date**: 2025-01-27  
**Priority**: High  
**Category**: Core Feature Enhancement  
**Estimated Effort**: Large (4-6 weeks)

## Executive Summary

This ticket outlines the implementation of comprehensive card printing support in Scryfall OS, transitioning from a single-card-per-name model to supporting all printings of each Magic card with different uniqueness modes in search results.

## Problem Statement

Currently, Scryfall OS stores only one printing per card name (enforced by `CREATE UNIQUE INDEX idx_cards_name ON magic.cards (card_name)`), which severely limits functionality compared to the official Scryfall API that supports all printings of each card. This prevents users from:

1. Finding specific printings by set, collector number, or artwork
2. Comparing prices across different printings  
3. Building collection management tools that track specific printings
4. Supporting different uniqueness modes (unique cards, unique artwork, all printings)

### Evidence

- Current database constraint: `CREATE UNIQUE INDEX idx_cards_name ON magic.cards (card_name)`
- API hardcodes `"unique:prints"` filter when fetching from Scryfall API
- Lightning Bolt example: Only 1 printing stored vs 55+ available printings on Scryfall

## Current State Analysis

### Database Schema
```sql
-- Current schema focuses on card-level data
CREATE TABLE magic.cards (
    card_name text NOT NULL,
    -- ... other card properties
    raw_card_blob jsonb NOT NULL -- Contains full Scryfall JSON
);
CREATE UNIQUE INDEX idx_cards_name ON magic.cards (card_name); -- BLOCKS multiple printings
```

### API Ingestion Process
- `_scryfall_search()` method hardcodes `"unique:prints"` filter
- Import processes use card name as primary identifier
- No support for set-specific or printing-specific queries

### Missing Scryfall API Compatibility
Official Scryfall supports:
- `unique:cards` (default) - One result per Oracle ID (functional reprint)  
- `unique:art` - One result per unique artwork
- `unique:prints` - All printings individually
- No unique parameter - Shows all printings

## Proposed Solution

### Phase 1: Database Schema Redesign

#### 1.1 New Printing-Centric Schema
```sql
-- New printings table to store all individual printings
CREATE TABLE magic.card_printings (
    -- Printing-specific identifiers
    scryfall_id uuid PRIMARY KEY,           -- Unique per printing
    oracle_id uuid NOT NULL,                -- Groups functional reprints  
    illustration_id uuid,                   -- Groups same artwork
    
    -- Printing-specific data
    card_name text NOT NULL,
    set_code text NOT NULL,
    collector_number text NOT NULL,
    rarity text NOT NULL,
    release_date date,
    
    -- Printing-specific properties
    artist text,
    flavor_text text,
    frame_style text,
    border_color text,
    watermark text,
    
    -- Pricing (printing-specific)
    price_usd numeric,
    price_eur numeric,
    price_tix numeric,
    
    -- Shared card data (duplicated for query performance)
    oracle_text text,
    mana_cost_text text,
    mana_cost_jsonb jsonb,
    cmc integer,
    card_types jsonb NOT NULL,
    card_subtypes jsonb,
    card_colors jsonb NOT NULL,
    card_color_identity jsonb NOT NULL,
    card_keywords jsonb NOT NULL,
    
    -- Power/toughness/loyalty for printings
    creature_power integer,
    creature_power_text text,
    creature_toughness integer,
    creature_toughness_text text,
    planeswalker_loyalty integer,
    planeswalker_loyalty_text text,
    
    -- Full Scryfall JSON for reference
    raw_card_blob jsonb NOT NULL,
    
    -- Metadata
    created_at timestamp DEFAULT NOW(),
    updated_at timestamp DEFAULT NOW()
);

-- Essential indexes for performance
CREATE UNIQUE INDEX idx_printings_scryfall_id ON magic.card_printings (scryfall_id);
CREATE INDEX idx_printings_oracle_id ON magic.card_printings (oracle_id);
CREATE INDEX idx_printings_name ON magic.card_printings (card_name);
CREATE INDEX idx_printings_set_collector ON magic.card_printings (set_code, collector_number);
CREATE INDEX idx_printings_illustration ON magic.card_printings (illustration_id) WHERE illustration_id IS NOT NULL;
```

#### 1.2 Legacy Support Table (Optional)
```sql
-- View for backward compatibility with single-card queries
CREATE VIEW magic.cards AS
SELECT DISTINCT ON (oracle_id)
    oracle_id,
    card_name,
    oracle_text,
    mana_cost_text,
    mana_cost_jsonb,
    cmc,
    card_types,
    card_subtypes,
    card_colors,
    card_color_identity,
    card_keywords,
    creature_power,
    creature_power_text,
    creature_toughness,
    creature_toughness_text,
    raw_card_blob
FROM magic.card_printings
ORDER BY oracle_id, release_date DESC;
```

### Phase 2: API Parameter Support

#### 2.1 Search Endpoint Enhancement
```python
class APIResource:
    def on_get_search(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Enhanced search with unique parameter support."""
        query = req.get_param("q", default="")
        unique_mode = req.get_param("unique", default="cards")  # cards, art, prints, none
        
        # Validate unique parameter
        valid_modes = {"cards", "art", "prints", "none"}
        if unique_mode not in valid_modes:
            raise falcon.HTTPBadRequest(
                title="Invalid unique parameter",
                description=f"unique must be one of: {', '.join(valid_modes)}"
            )
        
        # Generate appropriate SQL based on unique mode
        if unique_mode == "cards":
            base_query = "SELECT DISTINCT ON (oracle_id) * FROM magic.card_printings"
            order_clause = "ORDER BY oracle_id, release_date DESC"
        elif unique_mode == "art":
            base_query = "SELECT DISTINCT ON (illustration_id) * FROM magic.card_printings"
            order_clause = "ORDER BY illustration_id, release_date DESC"
        elif unique_mode in ("prints", "none"):
            base_query = "SELECT * FROM magic.card_printings"
            order_clause = "ORDER BY release_date DESC, set_code, collector_number"
        
        # Apply user query filters to base query
        parsed_query = parse_scryfall_query(query)
        sql_query = generate_sql_query(parsed_query, base_table="magic.card_printings")
        
        # Execute and return results
        # ... (implementation details)
```

#### 2.2 Enhanced Query Parser
```python
# Update db_info.py to support printing-specific fields
DB_COLUMNS.extend([
    FieldInfo("set_code", FieldType.TEXT, ["set", "s"], ParserClass.TEXT),
    FieldInfo("collector_number", FieldType.TEXT, ["number", "cn"], ParserClass.TEXT),
    FieldInfo("release_date", FieldType.DATE, ["date"], ParserClass.DATE),
    FieldInfo("illustration_id", FieldType.TEXT, ["art"], ParserClass.TEXT),
    FieldInfo("frame_style", FieldType.TEXT, ["frame"], ParserClass.TEXT),
    # ... other printing-specific fields
])
```

### Phase 3: Data Migration and Ingestion

#### 3.1 Migration Strategy
1. **Create new schema** alongside existing tables
2. **Populate from existing data**: Extract all unique card names and fetch all printings
3. **Validate data integrity**: Ensure all current functionality works with new schema  
4. **Switch over**: Update API to use new tables
5. **Remove old schema**: Drop old tables after validation period

#### 3.2 Enhanced Ingestion Process
```python
def _scryfall_search(self, *, query: str, unique: str = "prints") -> list[dict[str, Any]]:
    """Search Scryfall API with configurable unique parameter."""
    filters = [
        "(f:m or f:l or f:c or f:v)",  # Format filters
        "game:paper",
    ]
    
    # Only add unique filter if not "none"
    if unique != "none":
        filters.append(f"unique:{unique}")
    
    full_query = f"({query}) {' '.join(filters)}"
    # ... rest of implementation
```

#### 3.3 Bulk Data Import Enhancement
```python
def import_all_printings_bulk(self) -> dict[str, Any]:
    """Import all printings from Scryfall bulk data."""
    # Download Scryfall bulk data (all cards)
    bulk_data_url = "https://api.scryfall.com/bulk-data"
    # Process all printings and insert into new schema
    # Much faster than API pagination for full dataset
```

### Phase 4: UI and Search Experience

#### 4.1 Search Interface Updates
- Add unique mode selector in web interface
- Display printing-specific information (set, collector number, artist)
- Show artwork thumbnails for art-based uniqueness
- Price comparison across printings

#### 4.2 Result Display Enhancements
```html
<!-- Enhanced card display template -->
<div class="card-result printing-aware">
    <div class="card-name">Lightning Bolt</div>
    <div class="printing-info">
        <span class="set-info">CLU #141</span>
        <span class="rarity uncommon">Uncommon</span>
        <span class="artist">Christopher Rush</span>
    </div>
    <div class="pricing">
        <span class="price-usd">$0.25</span>
        <span class="price-eur">€0.20</span>
    </div>
</div>
```

## Implementation Plan

### Milestone 1: Database Schema (Week 1-2)
- [ ] Design new card_printings table schema
- [ ] Create migration scripts
- [ ] Add comprehensive indexes for performance  
- [ ] Create backward-compatibility view

### Milestone 2: Data Migration (Week 2-3)
- [ ] Build printing extraction from existing data
- [ ] Implement bulk Scryfall data import
- [ ] Validate data integrity and completeness
- [ ] Performance testing with full dataset

### Milestone 3: API Enhancement (Week 3-4)
- [ ] Add unique parameter support to search endpoint
- [ ] Update query parser for printing-specific fields
- [ ] Enhance SQL query generation
- [ ] Comprehensive API testing

### Milestone 4: UI and Testing (Week 5-6)
- [ ] Update web interface for unique mode selection
- [ ] Enhanced result display with printing info
- [ ] Comprehensive integration testing
- [ ] Performance optimization and monitoring

## Technical Considerations

### Performance Impact
- **Storage**: ~55x increase in storage (55 printings average per card name)
- **Query Performance**: Properly indexed, should maintain sub-second response times
- **Memory Usage**: Connection pooling and query optimization critical

### Data Consistency
- Regular sync with Scryfall bulk data (daily/weekly)
- Handling of reprints, errata, and card updates
- Validation of Oracle ID stability across printings

### Backward Compatibility  
- Maintain existing API endpoints during transition period
- Gradual migration of dependent systems
- Clear deprecation timeline for old schema

## Success Metrics

1. **Feature Completeness**: Support all 4 unique modes (cards, art, prints, none)
2. **Data Coverage**: >95% of printings available on Scryfall
3. **Performance**: <2s response time for typical queries
4. **User Adoption**: Track usage of printing-specific features
5. **API Compatibility**: 100% compatibility with Scryfall search syntax

## Risks and Mitigation

### High Risk
- **Storage costs**: 55x data increase - *Mitigation*: Efficient compression, cloud storage scaling
- **Performance degradation**: Complex queries - *Mitigation*: Extensive indexing, query optimization
- **Data sync complexity**: Keeping 500k+ cards updated - *Mitigation*: Robust ETL pipeline, error handling

### Medium Risk  
- **Migration complexity**: Schema changes - *Mitigation*: Gradual rollout, thorough testing
- **UI/UX complexity**: Multiple view modes - *Mitigation*: User research, iterative design

## Dependencies

- PostgreSQL 12+ (JSONB performance optimizations)
- Scryfall API access (rate limiting considerations)
- Storage infrastructure scaling
- Updated test suite for new functionality

## Future Enhancements

1. **Collection Tracking**: Personal collection management with specific printings
2. **Price History**: Historical pricing data per printing
3. **Condition Tracking**: Support for card conditions (NM, SP, etc.)
4. **Advanced Filtering**: Filter by artist, frame style, etc.
5. **Printing Statistics**: Analytics on printing popularity, price trends

---

**Next Steps**: Review and approve this design document, then begin implementation starting with Milestone 1.