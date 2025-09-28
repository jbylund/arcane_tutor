# Database Schema Design for All Printings Support

**Document:** Database Schema Design  
**Project:** 2025-09-28 All Printings  
**Last Updated:** 2025-09-28

## Table of Contents

1. [Current Schema Limitations](#current-schema-limitations)
2. [Proposed Schema Changes](#proposed-schema-changes)
3. [Migration Strategy](#migration-strategy)
4. [Performance Considerations](#performance-considerations)
5. [Indexing Strategy](#indexing-strategy)

## Current Schema Limitations

### Primary Constraint
The existing `UNIQUE INDEX idx_cards_name ON magic.cards (card_name)` prevents storing multiple printings of the same card.

### Current Structure
```sql
CREATE TABLE magic.cards (
    card_name text NOT NULL,
    card_set_code text,           -- Single set per card
    collector_number text,        -- Single collector number
    raw_card_blob jsonb NOT NULL,
    -- ... other columns
);
```

### Issues with Current Model
- Cannot store multiple printings of same card name
- Set-specific data (artwork, rarity variations) lost
- No support for unique:art vs unique:prints distinction
- Limited historical printing data

## Proposed Schema Changes

### Option 1: Add Printing ID (Recommended)

Modify the primary key structure to support multiple printings:

```sql
-- New composite primary key approach
DROP INDEX idx_cards_name;
ALTER TABLE magic.cards ADD COLUMN card_printing_id text;
ALTER TABLE magic.cards ADD COLUMN card_oracle_id text; -- Groups printings of same card

-- Create new primary key on printing_id (Scryfall's unique print identifier)
ALTER TABLE magic.cards ADD CONSTRAINT pk_cards_printing PRIMARY KEY (card_printing_id);

-- Index for grouping cards by oracle identity
CREATE INDEX idx_cards_oracle_id ON magic.cards (card_oracle_id);
CREATE INDEX idx_cards_name_oracle ON magic.cards (card_name, card_oracle_id);

-- Add release date for prefer:newest/oldest support
ALTER TABLE magic.cards ADD COLUMN release_date date;
CREATE INDEX idx_cards_release_date ON magic.cards (release_date) WHERE release_date IS NOT NULL;

-- Add artwork identifier for unique:art support  
ALTER TABLE magic.cards ADD COLUMN artwork_id text;
CREATE INDEX idx_cards_artwork_id ON magic.cards (artwork_id) WHERE artwork_id IS NOT NULL;
```

### Option 2: Separate Printings Table (Alternative)

Keep Oracle cards in main table, printings in separate table:

```sql
-- Keep current magic.cards for Oracle data
-- Add new table for all printings
CREATE TABLE magic.card_printings (
    printing_id text PRIMARY KEY,
    oracle_id text NOT NULL REFERENCES magic.cards(card_oracle_id),
    card_name text NOT NULL,
    set_code text,
    collector_number text,
    release_date date,
    artwork_id text,
    raw_printing_blob jsonb NOT NULL,
    -- Printing-specific columns only
);
```

**Decision: Option 1 is recommended** for simplicity and performance.

## Migration Strategy

### Phase 1: Schema Preparation
```sql
-- 1. Add new columns to existing table
ALTER TABLE magic.cards ADD COLUMN card_printing_id text;
ALTER TABLE magic.cards ADD COLUMN card_oracle_id text;  
ALTER TABLE magic.cards ADD COLUMN release_date date;
ALTER TABLE magic.cards ADD COLUMN artwork_id text;

-- 2. Populate oracle_id and printing_id from raw_card_blob
UPDATE magic.cards SET 
    card_oracle_id = raw_card_blob->>'oracle_id',
    card_printing_id = raw_card_blob->>'id',
    release_date = (raw_card_blob->>'released_at')::date,
    artwork_id = raw_card_blob->>'illustration_id'
WHERE raw_card_blob IS NOT NULL;
```

### Phase 2: Constraint Changes
```sql
-- 3. Create new indexes before dropping old constraint
CREATE UNIQUE INDEX idx_cards_printing_id ON magic.cards (card_printing_id);
CREATE INDEX idx_cards_oracle_id ON magic.cards (card_oracle_id);

-- 4. Drop old unique constraint on card_name
DROP INDEX idx_cards_name;

-- 5. Add new primary key constraint
ALTER TABLE magic.cards DROP CONSTRAINT IF EXISTS pk_cards;
ALTER TABLE magic.cards ADD CONSTRAINT pk_cards_printing PRIMARY KEY (card_printing_id);
```

### Phase 3: Data Population
```sql
-- Ingest all printings from Scryfall using existing ingestion pipeline
-- Remove unique:prints filter from _scryfall_search method
-- Allow duplicate card names during ingestion
```

### Rollback Strategy
```sql
-- Emergency rollback procedure
-- 1. Remove duplicate printings, keeping most recent per card_name
DELETE FROM magic.cards WHERE card_printing_id NOT IN (
    SELECT DISTINCT ON (card_name) card_printing_id 
    FROM magic.cards 
    ORDER BY card_name, release_date DESC NULLS LAST
);

-- 2. Restore original constraints
ALTER TABLE magic.cards DROP CONSTRAINT pk_cards_printing;
CREATE UNIQUE INDEX idx_cards_name ON magic.cards (card_name);

-- 3. Drop new columns
ALTER TABLE magic.cards DROP COLUMN card_printing_id;
ALTER TABLE magic.cards DROP COLUMN card_oracle_id;
ALTER TABLE magic.cards DROP COLUMN release_date;
ALTER TABLE magic.cards DROP COLUMN artwork_id;
```

## Performance Considerations

### Query Pattern Changes

**Current:** Single card per name
```sql
SELECT * FROM magic.cards WHERE card_name = 'Lightning Bolt';
-- Returns 1 row
```

**New:** Multiple printings per name
```sql
-- Get all printings
SELECT * FROM magic.cards WHERE card_name = 'Lightning Bolt';
-- Returns N rows

-- Get single "canonical" printing (prefer newest)
SELECT * FROM magic.cards 
WHERE card_name = 'Lightning Bolt'
ORDER BY release_date DESC NULLS LAST
LIMIT 1;
```

### Storage Impact
- **Estimated size increase:** 3x (based on Oracle vs Default Cards ratio)
- **Index overhead:** Additional indexes for oracle_id, artwork_id, release_date
- **Cache efficiency:** May need larger cache sizes for reasonable hit rates

## Indexing Strategy

### Core Indexes
```sql
-- Primary access patterns
CREATE UNIQUE INDEX idx_cards_printing_id ON magic.cards (card_printing_id);
CREATE INDEX idx_cards_name ON magic.cards (card_name);
CREATE INDEX idx_cards_oracle_id ON magic.cards (card_oracle_id);

-- For unique:art support
CREATE INDEX idx_cards_artwork_id ON magic.cards (artwork_id) WHERE artwork_id IS NOT NULL;

-- For prefer:newest/oldest support  
CREATE INDEX idx_cards_release_date ON magic.cards (release_date) WHERE release_date IS NOT NULL;

-- Composite index for common queries
CREATE INDEX idx_cards_name_release_date ON magic.cards (card_name, release_date DESC);
```

### Query-Specific Indexes
```sql
-- Set-based queries
CREATE INDEX idx_cards_set_code_name ON magic.cards (card_set_code, card_name);

-- Collector number queries
CREATE INDEX idx_cards_collector_name ON magic.cards (collector_number, card_name);

-- Performance optimization for unique queries
CREATE INDEX idx_cards_oracle_id_release ON magic.cards (card_oracle_id, release_date DESC);
```

### Partial Indexes for Performance
```sql
-- Index only cards with release dates (for prefer logic)
CREATE INDEX idx_cards_released_only ON magic.cards (card_name, release_date DESC) 
WHERE release_date IS NOT NULL;

-- Index only cards with artwork IDs (for unique:art)
CREATE INDEX idx_cards_artwork_only ON magic.cards (card_oracle_id, artwork_id)
WHERE artwork_id IS NOT NULL;
```

## Data Integrity Constraints

```sql
-- Ensure printing_id is unique and not null
ALTER TABLE magic.cards ALTER COLUMN card_printing_id SET NOT NULL;

-- Ensure oracle_id exists for grouping
ALTER TABLE magic.cards ALTER COLUMN card_oracle_id SET NOT NULL;

-- Validate date formats
ALTER TABLE magic.cards ADD CONSTRAINT valid_release_date 
CHECK (release_date IS NULL OR release_date > '1990-01-01');

-- Ensure raw data consistency
ALTER TABLE magic.cards ADD CONSTRAINT consistent_printing_id
CHECK (card_printing_id = raw_card_blob->>'id');
```

This schema design provides the foundation for supporting all printings while maintaining query performance and data integrity.