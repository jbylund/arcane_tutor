# Plan for Unified Caching Configuration

**Date:** 2025-10-16  
**Issue:** Make a Plan for Enabling/Disabling Caching via Config

## Current State Analysis

The application currently has three distinct caching layers:

### 1. CachingMiddleware (HTTP Response Layer)
- **Location:** `api/middlewares/caching_middleware.py`
- **Type:** LRUCache with maxsize=10,000
- **Scope:** Caches complete HTTP responses at the middleware level
- **Key:** Based on `(relative_uri, sorted params, selected headers)`
- **Use case:** Full HTTP response caching for GET requests

### 2. Method-Level Decorator Caching
Multiple methods in `APIResource` use `@cached` decorator:

- **`get_where_clause()`** (line 65)
  - Cache: `LRUCache(maxsize=10_000)`
  - Purpose: Cache parsed query SQL generation
  
- **`read_sql()`** (line 127)
  - Cache: `{}` (simple dict, effectively infinite)
  - Purpose: Cache SQL file contents
  
- **`import_data()`** (line 496)
  - Cache: `{}` (simple dict, effectively once-only)
  - Purpose: Prevent duplicate data imports
  
- **`_search()`** (line 574)
  - Cache: `TTLCache(maxsize=1000, ttl=60)`
  - Purpose: Cache search results for 60 seconds

### 3. Internal Method Caching
- **Location:** `APIResource._run_query()` (lines 289-329)
- **Type:** `self._query_cache = LRUCache(maxsize=1_000)`
- **Scope:** Caches database query results
- **Key:** Based on `(query, frozen params, explain flag)`
- **Use case:** Database query result caching

### 4. File-Based Caching
- **Location:** `APIResource.get_data()` (lines 357-396)
- **Type:** File system cache in `/data/api/` or `/tmp/api/`
- **Purpose:** Cache bulk card data downloaded from Scryfall

## Proposed Solution: Unified Cache Configuration

### Design Principles
1. **Single source of truth** for cache configuration
2. **Environment variable** support for container deployments
3. **Backward compatible** with existing behavior
4. **Granular control** over individual cache layers
5. **Runtime reconfigurable** where feasible

### Configuration Structure

```python
# api/config/cache_config.py

from dataclasses import dataclass
import os
from typing import Optional

@dataclass
class CacheLayerConfig:
    """Configuration for a single cache layer."""
    enabled: bool = True
    maxsize: Optional[int] = None
    ttl: Optional[int] = None  # seconds, None = no expiration
    
@dataclass
class CacheConfig:
    """Unified cache configuration for the application."""
    
    # HTTP middleware caching
    http_middleware: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
        maxsize=10_000,
    )
    
    # Query parsing cache (get_where_clause)
    query_parsing: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
        maxsize=10_000,
    )
    
    # SQL file reading cache (read_sql)
    sql_files: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
        maxsize=None,  # Infinite - SQL files don't change
    )
    
    # Data import guard cache (import_data)
    import_guard: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
        maxsize=None,  # Once-only guard
    )
    
    # Search results cache (_search)
    search_results: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
        maxsize=1_000,
        ttl=60,  # 60 seconds
    )
    
    # Database query cache (_run_query)
    database_queries: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
        maxsize=1_000,
    )
    
    # File-based bulk data cache (get_data)
    bulk_data_files: CacheLayerConfig = CacheLayerConfig(
        enabled=True,
    )
    
    @classmethod
    def from_environment(cls) -> "CacheConfig":
        """Load cache configuration from environment variables.
        
        Environment variables follow the pattern:
        CACHE_{LAYER}_{SETTING}
        
        Examples:
        - CACHE_HTTP_MIDDLEWARE_ENABLED=false
        - CACHE_SEARCH_RESULTS_MAXSIZE=2000
        - CACHE_SEARCH_RESULTS_TTL=120
        """
        config = cls()
        
        # Map layer names to config attributes
        layer_mapping = {
            "HTTP_MIDDLEWARE": "http_middleware",
            "QUERY_PARSING": "query_parsing",
            "SQL_FILES": "sql_files",
            "IMPORT_GUARD": "import_guard",
            "SEARCH_RESULTS": "search_results",
            "DATABASE_QUERIES": "database_queries",
            "BULK_DATA_FILES": "bulk_data_files",
        }
        
        for env_name, attr_name in layer_mapping.items():
            # Check for enabled flag
            enabled_key = f"CACHE_{env_name}_ENABLED"
            if enabled_key in os.environ:
                enabled = os.environ[enabled_key].lower() in ("true", "1", "yes")
                layer_config = getattr(config, attr_name)
                layer_config.enabled = enabled
            
            # Check for maxsize
            maxsize_key = f"CACHE_{env_name}_MAXSIZE"
            if maxsize_key in os.environ:
                maxsize = int(os.environ[maxsize_key])
                layer_config = getattr(config, attr_name)
                layer_config.maxsize = maxsize
            
            # Check for TTL
            ttl_key = f"CACHE_{env_name}_TTL"
            if ttl_key in os.environ:
                ttl = int(os.environ[ttl_key])
                layer_config = getattr(config, attr_name)
                layer_config.ttl = ttl
        
        return config
```

### Implementation Steps

#### Step 1: Create Configuration Module
- Create `api/config/` package
- Create `api/config/cache_config.py` with `CacheConfig` class
- Add environment variable parsing
- Add validation and defaults

#### Step 2: Update CachingMiddleware
```python
# api/middlewares/caching_middleware.py

class CachingMiddleware:
    def __init__(
        self: CachingMiddleware, 
        cache: MutableMapping | None = None,
        config: CacheLayerConfig | None = None,
    ) -> None:
        """Initialize with optional config."""
        if config is None:
            # Load from environment for backward compatibility
            from api.config.cache_config import CacheConfig
            full_config = CacheConfig.from_environment()
            config = full_config.http_middleware
        
        self.config = config
        
        if not config.enabled:
            # Disable caching by using a dummy cache
            self.cache = {}
            self._caching_disabled = True
            return
        
        self._caching_disabled = False
        
        if cache is None:
            cache = LRUCache(maxsize=config.maxsize or 10_000)
        self.cache = cache
    
    def process_request(self, req, resp):
        """Check cache, respecting enabled flag."""
        if self._caching_disabled:
            return
        # ... existing logic
```

#### Step 3: Update APIResource Decorators
Replace static `@cached` decorators with dynamic cache creation:

```python
# api/api_resource.py

class APIResource:
    def __init__(self, ...):
        # Load cache configuration
        from api.config.cache_config import CacheConfig
        self._cache_config = CacheConfig.from_environment()
        
        # Create caches based on config
        self._setup_caches()
        
    def _setup_caches(self):
        """Initialize all caches based on configuration."""
        
        # Query parsing cache
        if self._cache_config.query_parsing.enabled:
            cfg = self._cache_config.query_parsing
            self._where_clause_cache = LRUCache(maxsize=cfg.maxsize or 10_000)
        else:
            self._where_clause_cache = None
        
        # Search results cache
        if self._cache_config.search_results.enabled:
            cfg = self._cache_config.search_results
            self._search_cache = TTLCache(
                maxsize=cfg.maxsize or 1000,
                ttl=cfg.ttl or 60,
            )
        else:
            self._search_cache = None
        
        # ... similar for other caches
```

Then update methods to check cache existence:

```python
def get_where_clause(self, query: str) -> tuple[str, dict]:
    """Generate SQL with optional caching."""
    if self._where_clause_cache is not None:
        cached = self._where_clause_cache.get(query)
        if cached is not None:
            return cached
    
    # Generate result
    parsed_query = parse_scryfall_query(query)
    result = generate_sql_query(parsed_query)
    
    if self._where_clause_cache is not None:
        self._where_clause_cache[query] = result
    
    return result
```

#### Step 4: Update _run_query Internal Cache
```python
def _run_query(self, *, query, params=None, explain=True, statement_timeout=10_000):
    """Run query with configurable caching."""
    params = params or {}
    
    use_cache = self._cache_config.database_queries.enabled
    
    if use_cache:
        # ... existing cache key generation
        cached_val = self._query_cache.get(cachekey)
        if cached_val is not None:
            return copy.deepcopy(cached_val)
    
    # ... execute query ...
    
    if use_cache:
        self._query_cache[cachekey] = result
    
    return copy.deepcopy(result)
```

#### Step 5: Update api_worker.py
Pass config to middleware initialization:

```python
@classmethod
def get_api(cls, import_guard, schema_setup_event):
    """Create API with configured middleware."""
    from api.config.cache_config import CacheConfig
    cache_config = CacheConfig.from_environment()
    
    api = falcon.App(
        middleware=[
            TimingMiddleware(),
            CachingMiddleware(config=cache_config.http_middleware),
            CompressionMiddleware(),
        ],
    )
    # ...
```

#### Step 6: Testing
Create comprehensive tests:

```python
# api/tests/test_cache_config.py

def test_cache_config_defaults():
    """Test default cache configuration."""
    config = CacheConfig()
    assert config.http_middleware.enabled is True
    assert config.search_results.ttl == 60

def test_cache_config_from_environment(monkeypatch):
    """Test loading config from environment."""
    monkeypatch.setenv("CACHE_HTTP_MIDDLEWARE_ENABLED", "false")
    monkeypatch.setenv("CACHE_SEARCH_RESULTS_MAXSIZE", "2000")
    
    config = CacheConfig.from_environment()
    assert config.http_middleware.enabled is False
    assert config.search_results.maxsize == 2000

def test_caching_middleware_respects_config():
    """Test middleware respects disabled config."""
    config = CacheLayerConfig(enabled=False)
    middleware = CachingMiddleware(config=config)
    
    # Make request - should not cache
    # ... test logic

def test_api_resource_caching_disabled(monkeypatch):
    """Test APIResource with caching disabled."""
    monkeypatch.setenv("CACHE_DATABASE_QUERIES_ENABLED", "false")
    
    resource = APIResource()
    # Run query twice - should not use cache
    # ... test logic
```

#### Step 7: Documentation
Update README and create configuration guide:

```markdown
## Cache Configuration

Scryfall OS uses multiple caching layers for performance. Each layer can be
configured via environment variables:

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_HTTP_MIDDLEWARE_ENABLED` | `true` | Enable HTTP response caching |
| `CACHE_HTTP_MIDDLEWARE_MAXSIZE` | `10000` | Max cached responses |
| `CACHE_SEARCH_RESULTS_ENABLED` | `true` | Enable search result caching |
| `CACHE_SEARCH_RESULTS_MAXSIZE` | `1000` | Max cached searches |
| `CACHE_SEARCH_RESULTS_TTL` | `60` | Cache TTL in seconds |
| `CACHE_DATABASE_QUERIES_ENABLED` | `true` | Enable DB query caching |
| `CACHE_DATABASE_QUERIES_MAXSIZE` | `1000` | Max cached queries |

### Example: Disable All Caching (Development)

```bash
export CACHE_HTTP_MIDDLEWARE_ENABLED=false
export CACHE_SEARCH_RESULTS_ENABLED=false
export CACHE_DATABASE_QUERIES_ENABLED=false
python api/entrypoint.py --port 8080 --workers 4
```

### Example: Increase Search Cache (Production)

```bash
export CACHE_SEARCH_RESULTS_MAXSIZE=5000
export CACHE_SEARCH_RESULTS_TTL=300
python api/entrypoint.py --port 8080 --workers 10
```

### Docker Compose Configuration

```yaml
services:
  api:
    environment:
      - CACHE_HTTP_MIDDLEWARE_ENABLED=true
      - CACHE_SEARCH_RESULTS_MAXSIZE=2000
      - CACHE_DATABASE_QUERIES_MAXSIZE=2000
```
```

## Benefits of This Approach

1. **Single Strategy**: All caching uses the same configuration mechanism
2. **Flexible**: Can enable/disable individual layers without code changes
3. **Environment-aware**: Easy to configure for dev/staging/production
4. **Backward Compatible**: Defaults match current behavior
5. **Observable**: Can log cache configuration at startup
6. **Testable**: Easy to test with and without caching
7. **Documented**: Clear documentation of all cache layers

## Migration Path

1. ✅ Create plan document (this document)
2. Create `api/config/cache_config.py` module
3. Update `CachingMiddleware` to accept config
4. Update `APIResource` to use config for decorators
5. Update `APIResource._run_query` to respect config
6. Update `api_worker.py` to pass config to middleware
7. Add comprehensive tests
8. Update README with configuration guide
9. Optional: Add cache metrics/monitoring endpoints

## Risks and Mitigations

### Risk: Breaking Existing Deployments
**Mitigation**: Default configuration matches current behavior exactly.

### Risk: Performance Regression
**Mitigation**: Config loading happens once at startup, no runtime overhead.

### Risk: Complex Configuration
**Mitigation**: Provide sensible defaults and clear documentation with examples.

### Risk: Cache Inconsistency
**Mitigation**: Document cache layers and when to clear each cache.

## Future Enhancements

1. **Cache Monitoring**: Add endpoint to view cache statistics
2. **Cache Warming**: Add functionality to pre-populate caches
3. **Cache Invalidation**: Add API endpoints to clear specific caches
4. **Distributed Caching**: Support Redis for multi-worker cache sharing
5. **Cache Metrics**: Integrate with monitoring systems (Prometheus, etc.)
6. **Smart TTL**: Dynamic TTL based on data freshness requirements

## Alternatives Considered

### Alternative 1: Configuration File
**Pros**: More structured, can validate entire config at once  
**Cons**: Harder to configure in containers, need file mounting  
**Decision**: Environment variables are more container-friendly

### Alternative 2: Database-Stored Configuration
**Pros**: Can update without restart, centralized  
**Cons**: Adds dependency, more complex, cache config affects DB access  
**Decision**: Too complex for this use case

### Alternative 3: Keep Separate Caching Strategies
**Pros**: No changes needed  
**Cons**: Inconsistent, hard to configure, maintains status quo problem  
**Decision**: Does not address the issue

## Conclusion

This plan provides a comprehensive approach to unifying the caching strategy
under a single configuration system. The implementation is straightforward,
backward compatible, and provides the flexibility requested in the issue.
