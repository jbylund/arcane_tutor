# Ingestion Strategy for All Printings Support

**Document:** Ingestion Strategy  
**Project:** 2025-09-28 All Printings  
**Last Updated:** 2025-09-28

## Table of Contents

1. [Current Ingestion Process](#current-ingestion-process)
2. [Required Changes](#required-changes)
3. [Data Volume Analysis](#data-volume-analysis)
4. [Ingestion Pipeline Updates](#ingestion-pipeline-updates)
5. [Migration Strategy](#migration-strategy)
6. [Quality Assurance](#quality-assurance)

## Current Ingestion Process

### Scryfall API Integration
Current process in `APIResource._scryfall_search()`:

```python
filters = [
    "(f:m or f:l or f:c or f:v)",  # Format filters
    "game:paper",                   # Paper cards only
    "unique:prints",               # Get all printings (but dedupe locally)
]
```

### Deduplication Logic
Current implementation drops duplicate printings:

```python
def _get_cards_to_insert(self) -> list[dict[str, Any]]:
    """Get cards from Scryfall, deduplicated by name."""
    cards = self.get_data()
    unique_cards = {}
    
    for card in cards:
        name = card.get("name")
        if name not in unique_cards:
            unique_cards[name] = card  # Only first occurrence kept
            
    return list(unique_cards.values())
```

### Current Data Volume
- Approximately 31,000 unique card names (Oracle cards equivalent)
- Single printing per card name stored
- Estimated database size: ~2GB

## Required Changes

### Remove Deduplication Filter
Keep all printings from Scryfall API response:

```python
def _get_cards_to_insert(self) -> list[dict[str, Any]]:
    """Get all cards from Scryfall (no deduplication).""" 
    cards = self.get_data()
    processed_cards = []
    
    for card in cards:
        processed_card = self._preprocess_card(card)
        if processed_card is not None:
            processed_cards.append(processed_card)
            
    return processed_cards  # Return ALL printings
```

### Update Database Insertion Logic
Modify `_load_cards_with_staging()` to handle multiple printings:

```python
def _load_cards_with_staging(self, cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Load all printings into database."""
    
    # Remove existing deduplication - allow multiple cards with same name
    # Use card_printing_id as unique key instead of card_name
    
    # Update UPSERT logic to use printing_id
    upsert_sql = """
    INSERT INTO magic.cards (card_printing_id, card_name, card_oracle_id, ...)
    VALUES (%(printing_id)s, %(name)s, %(oracle_id)s, ...)
    ON CONFLICT (card_printing_id) DO UPDATE SET
        card_name = EXCLUDED.card_name,
        -- Update other fields
    """
```

## Data Volume Analysis

### Scryfall Bulk Data Statistics

**Current Bulk Data Downloads (2025 estimates):**
- **Oracle Cards:** ~31,000 entries (unique card names)
- **Default Cards:** ~95,000 entries (standard printings, ~3:1 ratio)
- **All Cards:** ~150,000+ entries (includes tokens, special variants)

**Projected Storage Impact:**
- Current database: ~2GB for Oracle cards
- With default cards: ~6GB (3x increase)
- With all cards: ~10GB (5x increase)

### Network and Processing Impact

**API Request Volume:**
- Current: ~31,000 card records per full import
- New: ~95,000-150,000 card records per full import
- Bandwidth: ~3-5x increase in JSON data transfer
- Processing time: Estimated 2-3x longer import duration

**Rate Limiting Considerations:**
- Scryfall allows 10 requests/second
- Current full import: ~45 minutes (estimated)
- New full import: ~90-120 minutes (estimated)
- Consider implementing resume/checkpoint functionality

## Ingestion Pipeline Updates

### Phase 1: Preprocessing Updates

```python
def _preprocess_card(self, card: dict[str, Any]) -> dict[str, Any] | None:
    """Enhanced preprocessing for all printings."""
    
    # Extract new fields for printing support
    processed_card = {
        # Existing fields
        "name": card.get("name"),
        "cmc": card.get("cmc"),
        
        # New printing-specific fields
        "printing_id": card.get("id"),           # Scryfall printing ID
        "oracle_id": card.get("oracle_id"),     # Groups same card across sets
        "set_code": card.get("set"),            # Set code (iko, thb, etc.)
        "collector_number": card.get("collector_number"),
        "released_at": card.get("released_at"), # For prefer:newest/oldest
        "artwork_id": card.get("illustration_id"), # For unique:art
        
        # Enhanced card data
        "rarity": card.get("rarity"),
        "digital": card.get("digital", False),
        "frame": card.get("frame"),
        "border_color": card.get("border_color"),
        
        # Keep full raw blob for compatibility
        "raw_card_blob": card,
    }
    
    # Validation checks
    if not processed_card["printing_id"]:
        logger.warning("Card missing printing_id: %s", card.get("name"))
        return None
        
    if not processed_card["oracle_id"]:
        logger.warning("Card missing oracle_id: %s", card.get("name")) 
        return None
        
    return processed_card
```

### Phase 2: Bulk Import Optimization

```python
def _optimized_bulk_import(self, cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Optimized bulk import for large datasets."""
    
    # Batch processing to handle memory efficiently
    BATCH_SIZE = 5000  # Process in chunks
    total_imported = 0
    
    for i in range(0, len(cards), BATCH_SIZE):
        batch = cards[i:i + BATCH_SIZE]
        
        # Process batch with staging table
        result = self._load_cards_with_staging(batch)
        total_imported += result.get("cards_loaded", 0)
        
        # Progress logging
        progress = min((i + BATCH_SIZE) / len(cards) * 100, 100)
        logger.info("Import progress: %.1f%% (%d/%d cards)", 
                   progress, total_imported, len(cards))
                   
        # Memory cleanup between batches
        gc.collect()
        
    return {
        "status": "success",
        "cards_loaded": total_imported,
        "total_processed": len(cards),
    }
```

### Phase 3: Incremental Update Support

```python
def import_cards_incremental(self, *, since_date: str) -> dict[str, Any]:
    """Import only cards updated since specified date."""
    
    # Use Scryfall's updated search parameter
    search_query = f"date>={since_date}"
    
    # Get updated cards from Scryfall
    updated_cards = self._scryfall_search(query=search_query)
    
    if not updated_cards:
        return {
            "status": "no_updates",
            "message": f"No cards updated since {since_date}",
            "cards_loaded": 0,
        }
    
    # Import updates (will upsert existing printings)
    return self._load_cards_with_staging(updated_cards)
```

## Migration Strategy

### Step 1: Schema Migration
Execute database schema changes from [database-schema.md](./database-schema.md):

1. Add new columns (printing_id, oracle_id, release_date, artwork_id)
2. Populate from existing raw_card_blob data
3. Update constraints and indexes

### Step 2: Data Migration
```python
def migrate_existing_data(self) -> dict[str, Any]:
    """Migrate existing single-printing data to new schema."""
    
    with self._conn_pool.connection() as conn, conn.cursor() as cursor:
        # Update existing records with printing metadata
        cursor.execute("""
            UPDATE magic.cards SET
                card_printing_id = raw_card_blob->>'id',
                card_oracle_id = raw_card_blob->>'oracle_id',
                release_date = (raw_card_blob->>'released_at')::date,
                artwork_id = raw_card_blob->>'illustration_id'
            WHERE card_printing_id IS NULL
        """)
        
        updated_count = cursor.rowcount
        logger.info("Updated %d existing cards with new metadata", updated_count)
        
    return {"status": "success", "updated_cards": updated_count}
```

### Step 3: Full Re-ingestion
```python
def perform_full_reingest(self) -> dict[str, Any]:
    """Perform complete re-ingestion with all printings."""
    
    # Clear existing data (keep as backup)
    backup_table = self._create_backup_table()
    
    try:
        # Import all printings from Scryfall Default Cards bulk data
        search_query = "*"  # Get everything
        all_cards = self._scryfall_search(query=search_query)
        
        # Import without deduplication
        result = self._optimized_bulk_import(all_cards)
        
        # Verify import success
        if result["status"] == "success":
            self._drop_backup_table(backup_table)
        
        return result
        
    except Exception as e:
        # Restore from backup on failure
        logger.error("Re-ingestion failed: %s", e)
        self._restore_from_backup(backup_table)
        raise
```

## Quality Assurance

### Data Validation Checks

```python
def validate_ingestion_quality(self) -> dict[str, Any]:
    """Comprehensive data quality validation."""
    
    with self._conn_pool.connection() as conn, conn.cursor() as cursor:
        validation_results = {}
        
        # Check 1: Printing ID uniqueness
        cursor.execute("""
            SELECT COUNT(*) as total_cards,
                   COUNT(DISTINCT card_printing_id) as unique_printing_ids
            FROM magic.cards
        """)
        result = cursor.fetchone()
        validation_results["printing_id_uniqueness"] = {
            "total_cards": result["total_cards"],
            "unique_printing_ids": result["unique_printing_ids"],
            "has_duplicates": result["total_cards"] != result["unique_printing_ids"]
        }
        
        # Check 2: Oracle ID groupings
        cursor.execute("""
            SELECT card_oracle_id, COUNT(*) as printing_count
            FROM magic.cards
            GROUP BY card_oracle_id
            HAVING COUNT(*) > 10  -- Cards with many printings
            ORDER BY printing_count DESC
            LIMIT 10
        """)
        validation_results["high_printing_count_cards"] = cursor.fetchall()
        
        # Check 3: Data completeness
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN card_printing_id IS NULL THEN 1 END) as missing_printing_id,
                COUNT(CASE WHEN card_oracle_id IS NULL THEN 1 END) as missing_oracle_id,
                COUNT(CASE WHEN release_date IS NULL THEN 1 END) as missing_release_date,
                COUNT(CASE WHEN artwork_id IS NULL THEN 1 END) as missing_artwork_id
            FROM magic.cards
        """)
        validation_results["data_completeness"] = cursor.fetchone()
        
    return validation_results
```

### Performance Testing

```python
def benchmark_query_performance(self) -> dict[str, Any]:
    """Benchmark search performance with new data model."""
    
    test_queries = [
        "lightning bolt",                    # Basic search
        "lightning bolt unique:cards",      # Unique cards (default behavior)
        "lightning bolt unique:prints",     # All printings
        "lightning bolt unique:art",        # Unique artwork
        "cmc:3 unique:prints",              # Numeric search with all printings
    ]
    
    results = {}
    
    for query in test_queries:
        start_time = time.time()
        
        # Execute search
        parsed_query = parse_search_query(query)
        sql, params = generate_sql_query(parsed_query)
        cards = self._execute_search_query(sql, params)
        
        execution_time = time.time() - start_time
        
        results[query] = {
            "execution_time_ms": execution_time * 1000,
            "result_count": len(cards),
            "meets_sla": execution_time < 0.05,  # 50ms SLA
        }
        
    return results
```

### Monitoring and Alerting

```python
def setup_ingestion_monitoring(self) -> None:
    """Set up monitoring for ingestion health."""
    
    # Monitor database size growth
    # Monitor query performance degradation  
    # Monitor import success rates
    # Alert on missing critical data (oracle_id, printing_id)
    # Track cache hit rates for different unique modes
    
    logger.info("Ingestion monitoring configured")
```

This ingestion strategy provides a comprehensive approach to transitioning from single-printing to all-printing support while maintaining system reliability and performance.