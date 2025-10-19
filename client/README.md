# Client Query Runner

This directory contains a client container that generates random queries and runs them in a loop against the Scryfall OS API. This tool is designed to help identify which database indexes are being utilized and to measure query performance.

## Purpose

The client query runner serves several purposes:

1. **Index Analysis**: By running diverse queries, you can identify which database indexes are being used and which queries benefit from specific indexes
2. **Performance Testing**: Monitor query execution times to identify slow queries that may need optimization
3. **Load Testing**: Generate continuous load on the API to test performance under sustained traffic
4. **Query Coverage**: Exercise various query patterns to ensure comprehensive testing of the search functionality

## Running the Client

### With Docker Compose (Recommended)

The client runs automatically when you start all services:

```bash
# Start all services including the client
docker compose up

# Or start in detached mode
docker compose up -d

# View client logs
docker logs -f scryfallclient

# Stop the client
docker stop scryfallclient
```

### Standalone Docker

You can also build and run the client container separately:

```bash
# Build the client image
docker compose build client

# Run the client with custom configuration
docker run -e API_URL=http://apiservice:8080 \
           -e QUERY_DELAY=0.5 \
           -e BATCH_SIZE=100 \
           scryfallos-client
```

### Local Execution

You can also run the query runner locally without Docker:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements/base.txt

# Run the query runner
python -m client.query_runner
```

## Configuration

The client supports the following environment variables:

- `API_URL`: Base URL for the API (default: `http://apiservice:8080`)
- `QUERY_DELAY`: Delay between queries in seconds (default: `1.0`)
- `BATCH_SIZE`: Number of queries before reporting statistics (default: `50`)

## Query Types

The query runner generates a diverse set of queries including:

- **Color queries**: Single colors, multicolor combinations, color identity
- **CMC queries**: Exact converted mana cost, ranges (>, <, >=, <=)
- **Type queries**: Creature, instant, sorcery, enchantment, artifact, planeswalker, land
- **Rarity queries**: Common, uncommon, rare, mythic
- **Power/Toughness queries**: Specific power and toughness values
- **Combined queries**: Color + type, color + CMC combinations
- **Keyword queries**: Flying, haste, trample, deathtouch, lifelink, vigilance
- **Text search**: Oracle text searches for common game terms
- **Set queries**: Queries for specific set codes
- **Format queries**: Legal in standard, modern, commander, legacy, vintage, pioneer

## Output

The client logs information about each query execution:

```
Query: 'color:r type:creature' | Duration: 0.123s | Cards: 42
Query: 'cmc=3' | Duration: 0.089s | Cards: 156
...
============================================================
Statistics for 50 queries:
  Success rate: 100.0%
  Successful queries: 50
  Failed queries: 0
  Total cards returned: 2,341
  Average duration: 0.105s
  Min duration: 0.067s
  Max duration: 0.234s
============================================================
```

## Using Results for Index Analysis

To analyze which indexes are being used:

1. Enable PostgreSQL query logging in the database configuration
2. Run the client to generate queries
3. Analyze the PostgreSQL logs to see which indexes are hit
4. Use `EXPLAIN ANALYZE` on slow queries to identify optimization opportunities

Example for checking index usage:

```sql
-- Check index usage statistics
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'magic'
ORDER BY idx_scan DESC;
```

## Development

To modify the query patterns, edit `query_runner.py` and update the following functions:

- `_generate_basic_queries()`: Basic color, CMC queries
- `_generate_type_queries()`: Type, rarity, power/toughness queries
- `_generate_combined_queries()`: Multi-criteria queries
- `_generate_text_queries()`: Text search and set queries
