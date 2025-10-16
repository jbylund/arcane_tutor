# Caching Architecture

## Overview

Scryfall OS implements a multi-layered caching strategy to optimize performance across different components of the application.

## Cache Layers Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         HTTP Request                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: HTTP Middleware Cache (CachingMiddleware)              │
│  - Caches complete HTTP responses                                │
│  - Cache Key: (URI, params, headers)                             │
│  - Type: LRUCache(maxsize=10,000)                                │
│  - Config: CACHE_HTTP_MIDDLEWARE_*                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (cache miss)
┌─────────────────────────────────────────────────────────────────┐
│  APIResource._handle() - Routes to action methods                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2: Method-Level Decorator Caches                          │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ get_where_clause() - Query parsing                        │  │
│  │ Cache: LRUCache(maxsize=10,000)                           │  │
│  │ Config: CACHE_QUERY_PARSING_*                             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ read_sql() - SQL file contents                            │  │
│  │ Cache: {} (infinite dict)                                 │  │
│  │ Config: CACHE_SQL_FILES_*                                 │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ import_data() - Import guard                              │  │
│  │ Cache: {} (once-only guard)                               │  │
│  │ Config: CACHE_IMPORT_GUARD_*                              │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ _search() - Search results                                │  │
│  │ Cache: TTLCache(maxsize=1000, ttl=60)                     │  │
│  │ Config: CACHE_SEARCH_RESULTS_*                            │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3: Internal Method Cache                                  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ _run_query() - Database query results                     │  │
│  │ Cache: self._query_cache = LRUCache(maxsize=1,000)        │  │
│  │ Cache Key: (query, frozen params, explain flag)           │  │
│  │ Config: CACHE_DATABASE_QUERIES_*                          │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PostgreSQL Database                                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Layer 4: File-Based Cache (Parallel to above layers)            │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ get_data() - Bulk card data from Scryfall                 │  │
│  │ Cache: File system (/data/api/ or /tmp/api/)              │  │
│  │ Config: CACHE_BULK_DATA_FILES_*                           │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Cache Characteristics

| Layer | Purpose | Type | Size | TTL | Scope |
|-------|---------|------|------|-----|-------|
| HTTP Middleware | Full response caching | LRU | 10,000 | ∞ | Per worker |
| Query Parsing | SQL WHERE clause generation | LRU | 10,000 | ∞ | Per worker |
| SQL Files | SQL file contents | Dict | ∞ | ∞ | Per worker |
| Import Guard | Prevent duplicate imports | Dict | 1 | ∞ | Per worker |
| Search Results | Card search results | TTL | 1,000 | 60s | Per worker |
| Database Queries | Query results | LRU | 1,000 | ∞ | Per worker |
| Bulk Data Files | Scryfall bulk data | File | 1 | ∞ | Global |

## Cache Flow Example: Search Request

```
1. User requests: GET /search?q=cmc=3&limit=10
   
2. ✓ HTTP Middleware checks cache
   - Key: ("/search", (("q", "cmc=3"), ("limit", "10")), ...)
   - Hit: Return cached response immediately
   - Miss: Continue to step 3

3. ✓ APIResource.search() called
   - Calls _search() with parameters
   
4. ✓ _search() checks its TTL cache
   - Key: (args, sorted kwargs)
   - Hit: Return cached search results
   - Miss: Continue to step 5

5. ✓ get_where_clause() checks parsing cache
   - Key: "cmc=3"
   - Hit: Return cached SQL WHERE clause
   - Miss: Parse query and cache result

6. ✓ _run_query() checks database cache
   - Key: (full SQL, params, explain flag)
   - Hit: Return cached query results
   - Miss: Execute query on database

7. Database executes query (cache miss path)

8. Results flow back up, each layer caching at its level:
   - _run_query() caches query result
   - _search() caches search result
   - HTTP Middleware caches complete response

9. Next identical request hits cache at step 2
```

## Configuration Example

### Disable All Caching (Development/Testing)
```bash
export CACHE_HTTP_MIDDLEWARE_ENABLED=false
export CACHE_QUERY_PARSING_ENABLED=false
export CACHE_SEARCH_RESULTS_ENABLED=false
export CACHE_DATABASE_QUERIES_ENABLED=false
```

### Optimize for High Traffic (Production)
```bash
export CACHE_HTTP_MIDDLEWARE_MAXSIZE=50000
export CACHE_SEARCH_RESULTS_MAXSIZE=5000
export CACHE_SEARCH_RESULTS_TTL=300
export CACHE_DATABASE_QUERIES_MAXSIZE=5000
```

### Disable Only Search Result Cache
```bash
export CACHE_SEARCH_RESULTS_ENABLED=false
```

## Cache Invalidation Strategy

### Automatic Invalidation
- **Search Results Cache**: TTL of 60 seconds (configurable)
- **HTTP Middleware Cache**: Cleared on worker restart
- **Database Query Cache**: Cleared on worker restart

### Manual Invalidation
When cards are loaded (`_load_cards_with_staging`):
```python
if cards_loaded > 0:
    self._query_cache.clear()
    if hasattr(self._search, "cache"):
        self._search.cache.clear()
```

### Full System Cache Clear
Restart all API workers:
```bash
# Using Docker Compose
docker-compose restart api

# Using process manager
kill -HUP <api-pid>
```

## Performance Considerations

### Cache Hit Rates
- **HTTP Middleware**: Very high for repeated identical requests
- **Query Parsing**: Very high for common search patterns
- **Search Results**: High for popular searches within TTL
- **Database Queries**: High for frequently-used queries
- **SQL Files**: 100% after first access

### Memory Usage
Approximate per-worker memory for caches:
- HTTP Middleware: ~50-100 MB (10,000 responses)
- Query Parsing: ~5-10 MB (10,000 WHERE clauses)
- Search Results: ~10-20 MB (1,000 result sets)
- Database Queries: ~10-20 MB (1,000 query results)
- **Total per worker: ~75-150 MB**

With 10 workers: ~750 MB - 1.5 GB total cache memory

## Future Enhancements

1. **Distributed Cache**: Use Redis for shared cache across workers
2. **Cache Metrics**: Track hit rates, sizes, evictions
3. **Smart Invalidation**: Invalidate related caches on data changes
4. **Prewarming**: Pre-populate caches with common queries
5. **Cache Compression**: Compress cached values to save memory
6. **Tiered Caching**: Hot in-memory, warm in Redis, cold in disk
