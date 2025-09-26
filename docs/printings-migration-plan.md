# Migration Plan: Card Printings Implementation

**Date**: 2025-01-27  
**Related**: `/docs/changelog/2025-01-27-card-printings-support.md`

## Migration Strategy Overview

This document outlines the step-by-step technical migration from the current single-card-per-name model to a comprehensive printings model.

## Phase 1: Schema Preparation

### Step 1.1: Create New Schema
Create new migration file: `api/db/2025-01-27-01-printings-schema.sql`

```sql
-- Create new printings table
CREATE TABLE magic.card_printings (
    -- Core identifiers
    scryfall_id uuid PRIMARY KEY,
    oracle_id uuid NOT NULL,
    illustration_id uuid,
    
    -- Printing metadata
    card_name text NOT NULL,
    set_code text NOT NULL,
    collector_number text NOT NULL,
    rarity text NOT NULL,
    release_date date,
    artist text,
    
    -- Core card data (duplicated for performance)
    oracle_text text,
    mana_cost_text text,
    mana_cost_jsonb jsonb,
    cmc integer,
    card_types jsonb NOT NULL DEFAULT '[]',
    card_subtypes jsonb DEFAULT '[]',
    card_colors jsonb NOT NULL DEFAULT '{}',
    card_color_identity jsonb NOT NULL DEFAULT '{}',
    card_keywords jsonb NOT NULL DEFAULT '{}',
    
    -- Game stats
    creature_power integer,
    creature_power_text text,
    creature_toughness integer,
    creature_toughness_text text,
    planeswalker_loyalty integer,
    planeswalker_loyalty_text text,
    
    -- Printing-specific data
    flavor_text text,
    watermark text,
    frame_style text,
    border_color text,
    card_layout text,
    
    -- Pricing
    price_usd numeric(10,4),
    price_eur numeric(10,4), 
    price_tix numeric(10,4),
    
    -- Legalities (duplicated from oracle)
    card_legalities jsonb DEFAULT '{}',
    
    -- Full Scryfall data
    raw_card_blob jsonb NOT NULL,
    
    -- Metadata
    created_at timestamp DEFAULT NOW(),
    updated_at timestamp DEFAULT NOW()
);
```

### Step 1.2: Create Performance Indexes  
Create migration file: `api/db/2025-01-27-02-printings-indexes.sql`

```sql
-- Primary indexes
CREATE UNIQUE INDEX idx_printings_scryfall_id ON magic.card_printings (scryfall_id);
CREATE INDEX idx_printings_oracle_id ON magic.card_printings (oracle_id);
CREATE INDEX idx_printings_name ON magic.card_printings (card_name);
CREATE INDEX idx_printings_set_collector ON magic.card_printings (set_code, collector_number);

-- Query optimization indexes
CREATE INDEX idx_printings_cmc ON magic.card_printings (cmc) WHERE cmc IS NOT NULL;
CREATE INDEX idx_printings_colors ON magic.card_printings USING GIN (card_colors);
CREATE INDEX idx_printings_identity ON magic.card_printings USING GIN (card_color_identity);
CREATE INDEX idx_printings_types ON magic.card_printings USING GIN (card_types);
CREATE INDEX idx_printings_keywords ON magic.card_printings USING GIN (card_keywords);
CREATE INDEX idx_printings_legalities ON magic.card_printings USING GIN (card_legalities);

-- Text search indexes
CREATE INDEX idx_printings_oracle_text ON magic.card_printings USING GIN (to_tsvector('english', oracle_text));
CREATE INDEX idx_printings_name_trgm ON magic.card_printings USING GIN (card_name gin_trgm_ops);
CREATE INDEX idx_printings_artist_trgm ON magic.card_printings USING GIN (artist gin_trgm_ops);

-- Artwork grouping
CREATE INDEX idx_printings_illustration ON magic.card_printings (illustration_id) WHERE illustration_id IS NOT NULL;

-- Date and pricing indexes
CREATE INDEX idx_printings_release_date ON magic.card_printings (release_date) WHERE release_date IS NOT NULL;
CREATE INDEX idx_printings_price_usd ON magic.card_printings (price_usd) WHERE price_usd IS NOT NULL;
```

### Step 1.3: Create Backward Compatibility View
Create migration file: `api/db/2025-01-27-03-compatibility-view.sql`

```sql
-- Backward compatibility view - shows most recent printing per Oracle ID
CREATE VIEW magic.cards AS
SELECT DISTINCT ON (oracle_id)
    oracle_id::text as card_name, -- Use oracle_id as surrogate key
    card_name as actual_card_name,
    cmc,
    mana_cost_text,
    mana_cost_jsonb,
    oracle_text,
    card_types,
    card_subtypes,
    card_colors,
    card_color_identity,
    card_keywords,
    creature_power,
    creature_power_text,
    creature_toughness,
    creature_toughness_text,
    card_legalities,
    raw_card_blob
FROM magic.card_printings
ORDER BY oracle_id, release_date DESC, set_code DESC;
```

## Phase 2: Data Population

### Step 2.1: Extract Existing Card Names
```python
def extract_existing_card_names(self) -> list[str]:
    """Extract all unique card names from current database."""
    with self._conn_pool.connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT DISTINCT card_name FROM magic.cards ORDER BY card_name")
        return [row['card_name'] for row in cursor.fetchall()]
```

### Step 2.2: Fetch All Printings via Scryfall API
```python
def populate_all_printings(self) -> dict[str, Any]:
    """Populate printings table with all Scryfall data."""
    existing_names = self.extract_existing_card_names()
    logger.info(f"Found {len(existing_names)} existing card names")
    
    total_imported = 0
    failed_names = []
    
    for card_name in existing_names:
        try:
            # Search for all printings of this card
            printings = self._scryfall_search(
                query=f'name:"{card_name}"',
                unique="prints"  # Get all printings
            )
            
            if printings:
                result = self._load_printings_with_staging(printings)
                total_imported += result.get('cards_loaded', 0)
                logger.info(f"Loaded {len(printings)} printings for '{card_name}'")
            
        except Exception as e:
            logger.error(f"Failed to load printings for '{card_name}': {e}")
            failed_names.append(card_name)
            
        # Rate limiting
        time.sleep(0.1)
    
    return {
        'total_imported': total_imported,
        'failed_names': failed_names,
        'success_rate': (len(existing_names) - len(failed_names)) / len(existing_names)
    }
```

### Step 2.3: Bulk Data Alternative (Faster)
```python
def import_from_scryfall_bulk_data(self) -> dict[str, Any]:
    """Import all printings from Scryfall bulk data (faster for full import)."""
    import requests
    import gzip
    
    # Get bulk data URL
    bulk_response = requests.get("https://api.scryfall.com/bulk-data")
    bulk_data = bulk_response.json()
    
    default_cards_url = None
    for item in bulk_data['data']:
        if item['type'] == 'default_cards':
            default_cards_url = item['download_uri']
            break
    
    if not default_cards_url:
        raise ValueError("Could not find default_cards bulk data URL")
    
    # Download and process bulk data
    logger.info(f"Downloading bulk data from {default_cards_url}")
    response = requests.get(default_cards_url, stream=True)
    
    cards_processed = 0
    with gzip.GzipFile(fileobj=response.raw) as gz_file:
        for line in gz_file:
            card_data = orjson.loads(line)
            
            # Filter for paper cards only
            if card_data.get('games', []):
                if 'paper' in card_data['games']:
                    self._insert_single_printing(card_data)
                    cards_processed += 1
                    
                    if cards_processed % 10000 == 0:
                        logger.info(f"Processed {cards_processed:,} printings")
    
    return {'total_imported': cards_processed}
```

## Phase 3: API Updates

### Step 3.1: Update Search Endpoint
Update `api/api_resource.py`:

```python
def on_get_search(self, req: falcon.Request, resp: falcon.Response) -> None:
    """Enhanced search with unique parameter support."""
    query = req.get_param("q", default="")
    unique_mode = req.get_param("unique", default="cards")
    page = int(req.get_param("page", default="1"))
    page_size = min(int(req.get_param("page_size", default="75")), 175)
    
    # Validate unique parameter
    valid_modes = {"cards", "art", "prints", "none"}
    if unique_mode not in valid_modes:
        unique_mode = "cards"  # Default fallback
    
    try:
        results = self._search_with_unique_mode(query, unique_mode, page, page_size)
        resp.media = results
        resp.status = falcon.HTTP_200
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        resp.status = falcon.HTTP_500
        resp.media = {"error": "Search failed", "details": str(e)}

def _search_with_unique_mode(self, query: str, unique_mode: str, page: int, page_size: int) -> dict:
    """Execute search with specified unique mode."""
    # Parse user query
    parsed_query = parse_scryfall_query(query)
    
    # Generate base SQL for printings table
    base_sql = generate_sql_query(parsed_query, base_table="magic.card_printings")
    
    # Apply unique mode logic
    if unique_mode == "cards":
        # Distinct on Oracle ID (functional reprints grouped)
        distinct_sql = f"""
            SELECT DISTINCT ON (oracle_id) *
            FROM ({base_sql}) printings
            ORDER BY oracle_id, release_date DESC, set_code DESC
        """
    elif unique_mode == "art":
        # Distinct on illustration ID (same artwork grouped) 
        distinct_sql = f"""
            SELECT DISTINCT ON (illustration_id) *
            FROM ({base_sql}) printings  
            ORDER BY illustration_id, release_date DESC, set_code DESC
        """
    elif unique_mode in ("prints", "none"):
        # All individual printings
        distinct_sql = f"""
            SELECT * FROM ({base_sql}) printings
            ORDER BY release_date DESC, set_code, collector_number
        """
    
    # Apply pagination
    offset = (page - 1) * page_size
    paginated_sql = f"{distinct_sql} LIMIT {page_size} OFFSET {offset}"
    
    # Execute query
    with self._conn_pool.connection() as conn, conn.cursor() as cursor:
        cursor.execute(paginated_sql)
        results = cursor.fetchall()
        
        # Get total count (expensive but needed for pagination)
        count_sql = f"SELECT COUNT(*) FROM ({distinct_sql}) counted"
        cursor.execute(count_sql)
        total_cards = cursor.fetchone()[0]
    
    return {
        "object": "list",
        "total_cards": total_cards,
        "has_more": offset + len(results) < total_cards,
        "next_page": f"/search?q={urllib.parse.quote(query)}&unique={unique_mode}&page={page+1}" if offset + len(results) < total_cards else None,
        "data": [self._format_card_result(card) for card in results]
    }
```

### Step 3.2: Update Query Parser
Update `api/parsing/db_info.py`:

```python
# Add printing-specific fields to DB_COLUMNS
DB_COLUMNS.extend([
    FieldInfo("scryfall_id", FieldType.TEXT, ["id"], ParserClass.TEXT),
    FieldInfo("oracle_id", FieldType.TEXT, ["oracleid"], ParserClass.TEXT), 
    FieldInfo("set_code", FieldType.TEXT, ["set", "s"], ParserClass.TEXT),
    FieldInfo("collector_number", FieldType.TEXT, ["number", "cn"], ParserClass.TEXT),
    FieldInfo("release_date", FieldType.DATE, ["date"], ParserClass.DATE),
    FieldInfo("illustration_id", FieldType.TEXT, ["art"], ParserClass.TEXT),
    FieldInfo("artist", FieldType.TEXT, ["artist", "a"], ParserClass.TEXT),
])
```

## Phase 4: Testing and Validation

### Step 4.1: Data Integrity Tests
```python
def test_printing_data_integrity(self):
    """Test that all printings data is correctly imported."""
    with self._conn_pool.connection() as conn, conn.cursor() as cursor:
        # Test 1: Verify no duplicate Scryfall IDs
        cursor.execute("SELECT scryfall_id, COUNT(*) FROM magic.card_printings GROUP BY scryfall_id HAVING COUNT(*) > 1")
        duplicates = cursor.fetchall()
        assert len(duplicates) == 0, f"Found duplicate scryfall_ids: {duplicates}"
        
        # Test 2: Verify Oracle ID grouping  
        cursor.execute("SELECT oracle_id, COUNT(DISTINCT card_name) FROM magic.card_printings GROUP BY oracle_id HAVING COUNT(DISTINCT card_name) > 1")
        oracle_issues = cursor.fetchall()
        # This might be expected for some cards, but log for review
        
        # Test 3: Verify essential fields are populated
        cursor.execute("SELECT COUNT(*) FROM magic.card_printings WHERE card_name IS NULL OR set_code IS NULL")
        missing_data = cursor.fetchone()[0]
        assert missing_data == 0, f"Found {missing_data} records with missing essential data"
```

### Step 4.2: Performance Tests
```python
def test_search_performance(self):
    """Test that search performance is acceptable with printings table."""
    import time
    
    test_queries = [
        "lightning bolt",
        "cmc:3 c:r",
        'name:"black lotus"',
        "t:creature power>5",
        "format:standard",
    ]
    
    for query in test_queries:
        for unique_mode in ["cards", "art", "prints"]:
            start_time = time.time()
            results = self._search_with_unique_mode(query, unique_mode, 1, 50)
            duration = time.time() - start_time
            
            assert duration < 2.0, f"Query '{query}' with unique={unique_mode} took {duration:.2f}s (>2s threshold)"
            assert len(results['data']) > 0 or results['total_cards'] == 0, f"Query '{query}' returned unexpected results"
```

## Phase 5: Deployment and Cutover

### Step 5.1: Deployment Checklist
- [ ] Deploy new schema migrations in production
- [ ] Populate printings table using bulk import (maintenance window)
- [ ] Validate data integrity and performance  
- [ ] Update API endpoints to use new table
- [ ] Monitor error rates and performance metrics
- [ ] Enable new unique parameter functionality

### Step 5.2: Rollback Plan
```sql
-- If needed, rollback to old schema
-- 1. Switch API back to magic.cards table
-- 2. Drop new printings table
DROP TABLE IF EXISTS magic.card_printings CASCADE;
DROP VIEW IF EXISTS magic.cards CASCADE;

-- 3. Restore original cards table and index
-- (from backup or re-import)
```

## Success Metrics

After deployment, validate:

1. **Data Coverage**: `SELECT COUNT(*) FROM magic.card_printings` should be ~500k+ cards
2. **Unique Modes**: All 4 modes return different result counts for same query
3. **Performance**: 95th percentile response time <2s for typical queries
4. **Accuracy**: Spot check results match official Scryfall API

## Timeline Estimate

- **Schema & Migration Prep**: 3-4 days
- **Data Population & Validation**: 2-3 days  
- **API Updates**: 3-4 days
- **Testing & Performance Tuning**: 2-3 days
- **Deployment & Monitoring**: 1-2 days

**Total**: ~2-3 weeks for core implementation