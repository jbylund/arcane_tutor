# API Changes Design for Unique and Prefer Support

**Document:** API Changes Design  
**Project:** 2025-09-28 All Printings  
**Last Updated:** 2025-09-28

## Table of Contents

1. [Current API Behavior](#current-api-behavior)
2. [Required Scryfall Compatibility](#required-scryfall-compatibility)
3. [Query Parser Updates](#query-parser-updates)
4. [SQL Generation Changes](#sql-generation-changes)
5. [API Endpoint Modifications](#api-endpoint-modifications)
6. [Performance Optimization](#performance-optimization)

## Current API Behavior

### Search Query Processing
Current flow in `APIResource._scryfall_search()`:
```python
filters = [
    "(f:m or f:l or f:c or f:v)",
    "game:paper", 
    "unique:prints",  # Forces all printings from Scryfall
]
full_query = f"({query}) {' '.join(filters)}"
```

### Deduplication Logic
In `APIResource._get_cards_to_insert()`:
```python
# Current: Deduplicates by card_name
unique_cards = {}
for card in cards:
    name = card.get("name")
    if name not in unique_cards:
        unique_cards[name] = card
return list(unique_cards.values())
```

## Required Scryfall Compatibility

### Unique Parameters
- **`unique:cards`** (default) - Show one printing per Oracle card identity
- **`unique:art`** - Show one card per unique artwork
- **`unique:prints`** - Show all printings (current ingestion behavior)

### Prefer Parameters  
- **`prefer:newest`** (default) - When showing unique results, prefer newest printing
- **`prefer:oldest`** - When showing unique results, prefer oldest printing

### Query Examples
```
lightning bolt                    # unique:cards prefer:newest (default)
lightning bolt unique:prints     # Show all Lightning Bolt printings
lightning bolt unique:art        # One per artwork
lightning bolt prefer:oldest     # Oldest Lightning Bolt printing
s:lea unique:prints              # All Alpha printings
```

## Query Parser Updates

### New Parser Components

Add to `parsing_f.py`:

```python
def create_display_parsers() -> dict[str, ParserElement]:
    """Create parsers for unique and prefer parameters."""
    
    # Unique parameter values
    unique_values = Literal("cards") | Literal("art") | Literal("prints")
    unique_parser = Keyword("unique") + Suppress(":") + unique_values
    
    # Prefer parameter values  
    prefer_values = Literal("newest") | Literal("oldest")
    prefer_parser = Keyword("prefer") + Suppress(":") + prefer_values
    
    return {
        "unique": unique_parser,
        "prefer": prefer_parser,
    }
```

### AST Node Updates

Add new node types in `nodes.py`:

```python
@dataclass
class UniqueNode(QueryNode):
    """Represents unique parameter (cards/art/prints)."""
    value: str  # "cards", "art", "prints"
    
    def __str__(self) -> str:
        return f"unique:{self.value}"

@dataclass  
class PreferNode(QueryNode):
    """Represents prefer parameter (newest/oldest)."""
    value: str  # "newest", "oldest"
    
    def __str__(self) -> str:
        return f"prefer:{self.value}"

@dataclass
class DisplayOptions:
    """Container for display-related query parameters."""
    unique: str = "cards"     # Default: unique:cards
    prefer: str = "newest"    # Default: prefer:newest
```

### Query Object Updates

Modify `Query` class:

```python
@dataclass
class Query(QueryNode):
    """Root query node containing search criteria and display options."""
    root: QueryNode
    display_options: DisplayOptions = field(default_factory=DisplayOptions)
    
    def extract_display_options(self) -> None:
        """Extract unique/prefer parameters from query tree."""
        # Implementation to find and extract UniqueNode and PreferNode
        # Remove them from main query tree and store in display_options
```

## SQL Generation Changes

### Core SQL Logic Updates

Modify `generate_sql_query()` in `parsing_f.py`:

```python
def generate_sql_query(parsed_query: Query) -> tuple[str, dict]:
    """Generate SQL with unique/prefer support."""
    
    base_query, params = _generate_base_query(parsed_query.root)
    
    # Apply unique/prefer logic
    if parsed_query.display_options.unique == "cards":
        sql = _apply_unique_cards_logic(base_query, parsed_query.display_options.prefer)
    elif parsed_query.display_options.unique == "art":
        sql = _apply_unique_art_logic(base_query, parsed_query.display_options.prefer)
    else:  # unique:prints
        sql = base_query  # Show all printings, no deduplication
        
    return sql, params

def _apply_unique_cards_logic(base_query: str, prefer: str) -> str:
    """Apply unique:cards with prefer logic."""
    order_direction = "DESC" if prefer == "newest" else "ASC"
    
    return f"""
    SELECT DISTINCT ON (card_oracle_id) *
    FROM ({base_query}) cards
    ORDER BY card_oracle_id, 
             release_date {order_direction} NULLS LAST,
             card_printing_id
    """

def _apply_unique_art_logic(base_query: str, prefer: str) -> str:
    """Apply unique:art with prefer logic."""  
    order_direction = "DESC" if prefer == "newest" else "ASC"
    
    return f"""
    SELECT DISTINCT ON (card_oracle_id, artwork_id) *
    FROM ({base_query}) cards  
    ORDER BY card_oracle_id, artwork_id,
             release_date {order_direction} NULLS LAST,
             card_printing_id
    """
```

### Window Function Alternative

For better performance on large result sets:

```python
def _apply_unique_cards_window(base_query: str, prefer: str) -> str:
    """Apply unique:cards using window functions (better for large results)."""
    order_direction = "DESC" if prefer == "newest" else "ASC"
    
    return f"""
    SELECT * FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY card_oracle_id 
                   ORDER BY release_date {order_direction} NULLS LAST,
                            card_printing_id
               ) as rn
        FROM ({base_query}) cards
    ) ranked_cards
    WHERE rn = 1
    """
```

## API Endpoint Modifications

### Search Endpoint Updates

Modify search endpoint in `api_resource.py`:

```python
def on_post(self, req: Request, resp: Response) -> None:
    """Handle search requests with unique/prefer support."""
    
    # Parse query with display options
    parsed_query = parse_search_query(query_string)
    
    # Extract display options for response metadata
    display_options = parsed_query.display_options
    
    # Generate SQL with unique/prefer logic
    sql, params = generate_sql_query(parsed_query)
    
    # Execute query
    cards = self._execute_search_query(sql, params)
    
    # Include display options in response
    response_data = {
        "cards": cards,
        "total_count": len(cards),
        "display_options": {
            "unique": display_options.unique,
            "prefer": display_options.prefer,
        },
        "query": str(parsed_query.root),  # Exclude display options from echoed query
    }
```

### Backward Compatibility

Ensure existing API behavior remains unchanged:

```python
def _ensure_backward_compatibility(self, query: str) -> str:
    """Ensure queries without unique/prefer work as before."""
    
    # If no unique specified, default to unique:cards (existing behavior)
    if "unique:" not in query:
        query += " unique:cards"
        
    # If no prefer specified, default to prefer:newest  
    if "prefer:" not in query:
        query += " prefer:newest"
        
    return query
```

## Performance Optimization

### Caching Strategy

```python
from cachetools import TTLCache

class SearchCache:
    """Caching layer for search results with unique/prefer awareness."""
    
    def __init__(self):
        # Separate caches for different unique modes
        self.cards_cache = TTLCache(maxsize=1000, ttl=3600)      # unique:cards
        self.art_cache = TTLCache(maxsize=500, ttl=3600)        # unique:art  
        self.prints_cache = TTLCache(maxsize=2000, ttl=3600)    # unique:prints
        
    def get_cache_key(self, query: Query) -> str:
        """Generate cache key including display options."""
        base_key = str(query.root)
        display_key = f"{query.display_options.unique}:{query.display_options.prefer}"
        return f"{base_key}#{display_key}"
```

### Index Usage Optimization

Ensure queries use appropriate indexes:

```python
def _optimize_query_for_unique_mode(sql: str, unique_mode: str) -> str:
    """Add query hints for optimal index usage."""
    
    if unique_mode == "cards":
        # Ensure oracle_id + release_date index is used
        return sql.replace("ORDER BY", "/*+ INDEX(cards idx_cards_oracle_id_release) */ ORDER BY")
    elif unique_mode == "art":
        # Ensure artwork_id index is used  
        return sql.replace("ORDER BY", "/*+ INDEX(cards idx_cards_artwork_id) */ ORDER BY")
    
    return sql
```

### Query Planner Analysis

Monitor query performance:

```python
def _analyze_query_performance(self, sql: str, params: dict) -> None:
    """Log slow queries for optimization."""
    
    start_time = time.time()
    with self._conn_pool.connection() as conn, conn.cursor() as cursor:
        # Get query plan for analysis
        cursor.execute(f"EXPLAIN ANALYZE {sql}", params)
        plan = cursor.fetchall()
        
        execution_time = time.time() - start_time
        
        if execution_time > 0.05:  # 50ms threshold
            logger.warning("Slow query detected: %s ms", execution_time * 1000)
            logger.debug("Query plan: %s", plan)
```

## Testing Strategy

### Unit Tests

```python
class TestUniquePreferParsing(unittest.TestCase):
    """Test parsing of unique and prefer parameters."""
    
    def test_unique_cards_parsing(self):
        query = parse_search_query("lightning unique:cards")
        self.assertEqual(query.display_options.unique, "cards")
        
    def test_prefer_oldest_parsing(self):
        query = parse_search_query("bolt prefer:oldest")  
        self.assertEqual(query.display_options.prefer, "oldest")
        
    def test_combined_display_options(self):
        query = parse_search_query("cmc:3 unique:art prefer:newest")
        self.assertEqual(query.display_options.unique, "art")
        self.assertEqual(query.display_options.prefer, "newest")
```

### Integration Tests

```python
class TestUniquePreferBehavior(unittest.TestCase):
    """Test end-to-end unique/prefer behavior."""
    
    def test_unique_cards_returns_one_per_oracle_id(self):
        # Test that unique:cards returns only one printing per Oracle card
        pass
        
    def test_prefer_newest_returns_latest_printing(self):
        # Test that prefer:newest returns most recent printing
        pass
        
    def test_unique_prints_returns_all_printings(self):
        # Test that unique:prints returns all available printings
        pass
```

This API design maintains backward compatibility while adding powerful new functionality for controlling card display preferences.