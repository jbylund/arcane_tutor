# Scryfall Read-Through Cache Service

This document describes the Scryfall read-through cache service implemented to improve throughput when making requests to the Scryfall API while respecting rate limits.

## Overview

The cache service sits between the Scryfall OS API and the Scryfall API, providing:

1. **Read-through caching**: Returns cached data or fetches fresh data from Scryfall
2. **Rate limiting**: Respects Scryfall's 10 requests/second limit
3. **JSON file storage**: Caches responses in organized directory structure
4. **Graceful fallback**: Falls back to direct requests if cache service is unavailable

## Architecture

```
[Scryfall OS API] → [Cache Service] → [Scryfall API]
                        ↓
                  [File System Cache]
                  data/cache/
                  ├── api.scryfall.com/
                  │   ├── base64_url1.json
                  │   └── base64_url2.json
                  └── scryfall.com/
                      └── base64_url3.json
```

## Directory Structure

The cache uses the requested directory structure: `host/base64_encoded_url.json`

Example:
- URL: `https://api.scryfall.com/bulk-data`
- Cache path: `data/cache/api.scryfall.com/aHR0cHM6Ly9hcGkuc2NyeWZhbGwuY29tL2J1bGstZGF0YQ.json`

## Running with Docker Compose

The cache service is integrated into the docker-compose setup:

```bash
# Start all services including cache
make up

# Cache service will be available on port 18081
curl http://localhost:18081/cache/stats
```

## Running Standalone

For development or testing:

```bash
# Install dependencies
cd cache
pip install -r requirements.txt

# Run in debug mode
python entrypoint.py --port 8081 --debug

# Or with gunicorn for production
gunicorn --bind 0.0.0.0:8081 --workers 2 cache_service:create_app()
```

## API Endpoints

### GET /cache
Retrieve data through the cache.

**Parameters:**
- `url` (required): The Scryfall URL to fetch
- `max_age` (optional): Maximum age of cached data in seconds (default: 3600)

**Example:**
```bash
curl "http://localhost:8081/cache?url=https://api.scryfall.com/bulk-data&max_age=1800"
```

### POST /cache
Clear cache entries.

**Clear specific URL:**
```bash
curl -X POST http://localhost:8081/cache \
  -H "Content-Type: application/json" \
  -d '{"url": "https://api.scryfall.com/bulk-data"}'
```

**Clear all cache:**
```bash
curl -X POST http://localhost:8081/cache \
  -H "Content-Type: application/json" \
  -d '{}'
```

### GET /cache/stats
Get cache statistics.

```bash
curl http://localhost:8081/cache/stats
```

**Example response:**
```json
{
  "cache_entries": 42,
  "total_size_bytes": 15728640,
  "hosts": ["api.scryfall.com", "scryfall.com"]
}
```

## Integration with Scryfall OS API

The main API automatically uses the cache service through the `ScryfallCacheClient`. The cache service URL is configured via environment variable:

```bash
export CACHE_SERVICE_URL=http://cacheservice:8081
```

### New API Endpoints

The main API now includes cache management endpoints:

- `GET /get_cache_stats` - Get cache statistics
- `POST /clear_cache` - Clear cache entries

## Rate Limiting

The cache service implements rate limiting using the `ratelimit` library:
- Maximum 10 requests per second to Scryfall
- Uses `sleep_and_retry` decorator for automatic backoff

## Security Features

- **Domain validation**: Only allows requests to `api.scryfall.com` and `scryfall.com`
- **JSON-only caching**: Only caches JSON responses from Scryfall
- **No credential exposure**: Cache service doesn't handle authentication

## Fallback Behavior

If the cache service is unavailable, the API gracefully falls back to making direct requests to Scryfall, ensuring service reliability.

## Monitoring and Maintenance

### Cache Statistics
Monitor cache performance via the `/cache/stats` endpoint:
- Number of cache entries
- Total cache size in bytes  
- Cached hosts

### Cache Cleanup
The cache service doesn't automatically expire entries. Use the clear cache endpoints for maintenance:

```bash
# Clear old entries for a specific URL
curl -X POST http://localhost:8081/cache \
  -H "Content-Type: application/json" \
  -d '{"url": "https://api.scryfall.com/bulk-data"}'

# Clear entire cache
curl -X POST http://localhost:8081/cache \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Testing

Run the cache integration tests:

```bash
python -m pytest api/tests/test_cache_integration.py -v
```

The test suite includes:
- Cache client initialization and configuration
- Fallback behavior when cache service is unavailable
- Directory structure generation
- Domain validation
- API integration

## Performance Benefits

The cache service provides several performance improvements:

1. **Reduced API calls**: Frequently requested data is served from cache
2. **Rate limit compliance**: Prevents hitting Scryfall's rate limits
3. **Improved response times**: Cached responses are served instantly
4. **Reduced bandwidth**: Large bulk data downloads are cached locally

## Configuration

### Environment Variables

- `CACHE_SERVICE_URL`: URL of the cache service (default: `http://cacheservice:8081`)

### Cache Service Settings

- Default cache directory: `/data/cache`
- Default port: `8081`
- Rate limit: 10 requests/second
- Default cache TTL: 1 hour (3600 seconds)

## Troubleshooting

### Cache service not responding
- Check if the service is running: `curl http://localhost:8081/cache/stats`
- Check logs for rate limiting or Scryfall API errors
- API will automatically fall back to direct requests

### Cache directory permissions
- Ensure the cache directory is writable: `chmod 755 data/cache`
- Check disk space if cache writes are failing

### Rate limiting issues
- Monitor Scryfall API response headers for rate limit status
- The service automatically handles rate limiting with backoff