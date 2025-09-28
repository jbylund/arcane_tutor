# Database Schema Design for Multiple Printings Support

This document details the database schema changes required to support multiple card printings and unique search modes.

## Current Schema Limitations

The existing `magic.cards` table has several limitations:

1. **Single Printing per Card**: Only stores one printing per card name
2. **Missing Printing Information**: No set codes, collector numbers, or printing-specific data
3. **No Artwork Tracking**: Cannot distinguish between different artworks of the same card
4. **Limited Scalability**: Name-based uniqueness prevents multiple versions

## New Schema Design

### Core Tables

#### `magic.oracle_cards` - Card Definitions

Stores the canonical definition of each unique Magic card:

```sql
CREATE TABLE magic.oracle_cards (
    oracle_id uuid PRIMARY KEY,
    name text NOT NULL,
    mana_cost text,
    cmc integer,
    type_line text NOT NULL,
    oracle_text text,
    
    -- Creature attributes
    power text,
    toughness text,
    
    -- Planeswalker attributes  
    loyalty text,
    
    -- Computed search attributes (denormalized for performance)
    colors jsonb NOT NULL DEFAULT '{}'::jsonb,
    color_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
    keywords jsonb NOT NULL DEFAULT '{}'::jsonb,
    card_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    card_subtypes jsonb NOT NULL DEFAULT '[]'::jsonb,
    
    -- Metadata
    created_at timestamp with time zone DEFAULT NOW(),
    updated_at timestamp with time zone DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT colors_valid CHECK (
        colors <@ '{"W":true,"U":true,"B":true,"R":true,"G":true,"C":true}'::jsonb
    ),
    CONSTRAINT color_identity_valid CHECK (
        color_identity <@ '{"W":true,"U":true,"B":true,"R":true,"G":true,"C":true}'::jsonb
    ),
    CONSTRAINT keywords_is_object CHECK (jsonb_typeof(keywords) = 'object'),
    CONSTRAINT card_types_is_array CHECK (jsonb_typeof(card_types) = 'array'),
    CONSTRAINT card_subtypes_is_array CHECK (jsonb_typeof(card_subtypes) = 'array')
);
```

#### `magic.card_printings` - Individual Printings

Stores each individual printing/version of cards:

```sql
CREATE TABLE magic.card_printings (
    id uuid PRIMARY KEY,
    oracle_id uuid NOT NULL REFERENCES magic.oracle_cards(oracle_id) ON DELETE CASCADE,
    
    -- Basic printing information
    name text NOT NULL, -- May differ slightly from oracle name
    set_code text NOT NULL,
    set_name text NOT NULL,
    collector_number text NOT NULL,
    collector_number_int integer, -- Extracted numeric part for comparisons
    rarity text NOT NULL,
    
    -- Visual and production attributes
    artist text,
    flavor_text text,
    watermark text,
    border_color text,
    frame text,
    layout text NOT NULL,
    
    -- Artwork and image data
    illustration_id uuid, -- Groups printings with same artwork
    image_status text,
    image_uris jsonb DEFAULT '{}'::jsonb,
    
    -- Market and pricing data
    prices jsonb DEFAULT '{}'::jsonb,
    
    -- Format legality (may vary by printing)
    legalities jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Complete Scryfall data for extensibility
    scryfall_data jsonb NOT NULL,
    
    -- Temporal data
    released_at date,
    
    -- Metadata
    created_at timestamp with time zone DEFAULT NOW(),
    updated_at timestamp with time zone DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT unique_printing UNIQUE (set_code, collector_number),
    CONSTRAINT valid_rarity CHECK (rarity IN ('common', 'uncommon', 'rare', 'mythic', 'special', 'bonus')),
    CONSTRAINT valid_border_color CHECK (border_color IN ('black', 'white', 'borderless', 'silver', 'gold')),
    CONSTRAINT image_uris_is_object CHECK (jsonb_typeof(image_uris) = 'object'),
    CONSTRAINT prices_is_object CHECK (jsonb_typeof(prices) = 'object'),
    CONSTRAINT legalities_is_object CHECK (jsonb_typeof(legalities) = 'object'),
    CONSTRAINT scryfall_data_is_object CHECK (jsonb_typeof(scryfall_data) = 'object')
);
```

### Performance Indexes

Carefully designed indexes to support fast searches across different modes:

```sql
-- Oracle cards indexes
CREATE INDEX idx_oracle_cards_name_gin ON magic.oracle_cards 
    USING gin(to_tsvector('english', name));
CREATE INDEX idx_oracle_cards_oracle_text_gin ON magic.oracle_cards 
    USING gin(to_tsvector('english', oracle_text));
CREATE INDEX idx_oracle_cards_cmc ON magic.oracle_cards(cmc);
CREATE INDEX idx_oracle_cards_colors_gin ON magic.oracle_cards USING gin(colors);
CREATE INDEX idx_oracle_cards_color_identity_gin ON magic.oracle_cards USING gin(color_identity);
CREATE INDEX idx_oracle_cards_keywords_gin ON magic.oracle_cards USING gin(keywords);

-- Card printings indexes
CREATE INDEX idx_card_printings_oracle_id ON magic.card_printings(oracle_id);
CREATE INDEX idx_card_printings_name_gin ON magic.card_printings 
    USING gin(to_tsvector('english', name));
CREATE INDEX idx_card_printings_set_code ON magic.card_printings(set_code);
CREATE INDEX idx_card_printings_artist_gin ON magic.card_printings 
    USING gin(to_tsvector('english', artist));
CREATE INDEX idx_card_printings_illustration_id ON magic.card_printings(illustration_id) 
    WHERE illustration_id IS NOT NULL;
CREATE INDEX idx_card_printings_rarity ON magic.card_printings(rarity);
CREATE INDEX idx_card_printings_collector_number_int ON magic.card_printings(collector_number_int) 
    WHERE collector_number_int IS NOT NULL;
CREATE INDEX idx_card_printings_released_at ON magic.card_printings(released_at);
CREATE INDEX idx_card_printings_flavor_text_gin ON magic.card_printings 
    USING gin(to_tsvector('english', flavor_text))
    WHERE flavor_text IS NOT NULL;
```

### Backwards Compatibility View

Maintains compatibility with existing queries:

```sql
CREATE VIEW magic.cards AS
SELECT 
    oc.name as card_name,
    oc.cmc,
    oc.mana_cost as mana_cost_text,
    oc.mana_cost as mana_cost_jsonb, -- TODO: Parse into JSONB
    cp.scryfall_data as raw_card_blob,
    oc.card_types,
    oc.card_subtypes,
    oc.colors as card_colors,
    oc.color_identity as card_color_identity,
    oc.keywords as card_keywords,
    oc.oracle_text,
    
    -- Creature attributes
    CASE 
        WHEN oc.power ~ '^[0-9]+$' THEN oc.power::integer 
        ELSE NULL 
    END as creature_power,
    oc.power as creature_power_text,
    CASE 
        WHEN oc.toughness ~ '^[0-9]+$' THEN oc.toughness::integer 
        ELSE NULL 
    END as creature_toughness,
    oc.toughness as creature_toughness_text,
    
    -- Printing-specific attributes from most recent printing
    cp.artist as card_artist,
    cp.rarity as card_rarity,
    cp.set_code as card_set,
    cp.collector_number,
    CASE 
        WHEN cp.collector_number ~ '^[0-9]+' THEN 
            substring(cp.collector_number from '^([0-9]+)')::integer
        ELSE NULL
    END as collector_number_int,
    cp.legalities as card_legalities,
    cp.layout as card_layout,
    cp.border_color as card_border,
    cp.watermark as card_watermark,
    
    -- Default empty JSONB for oracle tags (backwards compatibility)
    '{}'::jsonb as card_oracle_tags
    
FROM magic.oracle_cards oc
JOIN LATERAL (
    SELECT * FROM magic.card_printings cp_inner 
    WHERE cp_inner.oracle_id = oc.oracle_id 
    ORDER BY cp_inner.released_at DESC, cp_inner.set_code 
    LIMIT 1
) cp ON true;

-- Create unique index on the view for backwards compatibility
CREATE UNIQUE INDEX idx_cards_view_card_name ON magic.cards(card_name);
```

## Query Patterns by Unique Mode

### `unique=cards` (Default Mode)

Returns one result per unique card (Oracle ID):

```sql
-- Base query structure
SELECT DISTINCT ON (oc.oracle_id)
    oc.*,
    cp.set_code,
    cp.collector_number,
    cp.rarity,
    cp.artist,
    cp.illustration_id,
    cp.scryfall_data
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE [search_conditions]
ORDER BY oc.oracle_id, cp.released_at DESC, cp.set_code;
```

### `unique=art` Mode

Returns one result per unique artwork:

```sql
-- Base query structure  
SELECT DISTINCT ON (cp.illustration_id)
    oc.*,
    cp.set_code,
    cp.collector_number,
    cp.rarity,
    cp.artist,
    cp.illustration_id,
    cp.scryfall_data
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE [search_conditions] 
    AND cp.illustration_id IS NOT NULL
ORDER BY cp.illustration_id, cp.released_at DESC, cp.set_code;
```

### `unique=prints` Mode

Returns all matching printings:

```sql
-- Base query structure
SELECT 
    oc.*,
    cp.set_code,
    cp.collector_number, 
    cp.rarity,
    cp.artist,
    cp.illustration_id,
    cp.scryfall_data
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE [search_conditions]
ORDER BY oc.name, cp.released_at DESC, cp.set_code;
```

## Migration Considerations

### Data Population Strategy

1. **Bulk Import from default_cards**: Process Scryfall's `default_cards` dataset
2. **Oracle ID Deduplication**: Group cards by `oracle_id` for oracle_cards table
3. **Illustration ID Handling**: Preserve `illustration_id` for artwork grouping
4. **Data Validation**: Ensure referential integrity and constraint compliance

### Migration Steps

1. Create new tables alongside existing schema
2. Import data from `default_cards` bulk data
3. Validate data integrity and performance
4. Create backwards compatibility view
5. Test existing API endpoints
6. Switch to new schema with feature flags
7. Remove old tables after validation period

### Performance Validation

- Compare query execution times before/after migration
- Validate index usage patterns
- Monitor memory usage with larger dataset
- Test unique mode query performance

## Storage Estimates

### Current Schema
- **oracle_cards**: ~92k rows, ~164MB
- **Total**: ~164MB

### New Schema  
- **oracle_cards**: ~92k rows, ~180MB (additional computed columns)
- **card_printings**: ~800k rows, ~550MB (detailed printing data)
- **Indexes**: ~200MB (estimated for all indexes)
- **Total**: ~930MB (5.7x increase)

### Optimization Opportunities

- Compress large JSONB fields
- Partition printing table by set or date
- Use materialized views for common aggregations
- Archive old/unused printings to separate tables