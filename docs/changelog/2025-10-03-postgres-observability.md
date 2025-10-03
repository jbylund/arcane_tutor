# PostgreSQL Observability Improvements

**Date:** 2025-10-03

## Overview

This document describes the PostgreSQL observability improvements implemented to enhance database monitoring and performance analysis.

## Features Implemented

### 1. pg_stat_statements Extension

The `pg_stat_statements` extension has been enabled to track execution statistics for all SQL statements executed by the server. This provides valuable insights into query performance and frequency.

**Configuration:**
- `pg_stat_statements.track = all` - Track all statements including those in nested functions
- `pg_stat_statements.max = 10000` - Track up to 10,000 distinct queries
- `pg_stat_statements.track_utility = on` - Track utility commands (CREATE, ALTER, etc.)
- `pg_stat_statements.track_planning = on` - Track query planning time

**Usage:**
```sql
-- View top 10 slowest queries by total time
SELECT 
    query,
    calls,
    total_exec_time,
    mean_exec_time,
    max_exec_time
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;

-- View most frequently executed queries
SELECT 
    query,
    calls,
    mean_exec_time
FROM pg_stat_statements
ORDER BY calls DESC
LIMIT 10;

-- Reset statistics
SELECT pg_stat_statements_reset();
```

### 2. auto_explain Extension

The `auto_explain` extension automatically logs execution plans for slow queries, helping identify performance bottlenecks.

**Configuration:**
- `auto_explain.log_min_duration = 30` - Log plans for queries taking more than 30ms
- `auto_explain.log_analyze = on` - Include actual run times and row counts
- `auto_explain.log_buffers = on` - Include buffer usage statistics
- `auto_explain.log_timing = on` - Include timing information for each node
- `auto_explain.log_triggers = on` - Include trigger statistics
- `auto_explain.log_verbose = on` - Include verbose output
- `auto_explain.log_nested_statements = on` - Log nested statements
- `auto_explain.sample_rate = 1` - Log all qualifying queries (100%)

### 3. Enhanced Logging Configuration

**Log File Location:**
- Logs are written to `/var/log/postgresql/` inside the container
- This directory is mounted from `./data/postgres/logs/` on the host
- Log filename pattern: `postgresql-YYYY-MM-DD.log`

**What Gets Logged:**
- All queries taking longer than 15ms (`log_min_duration_statement = 15`)
- Query plans for queries taking longer than 30ms (via auto_explain)
- Connection and disconnection events
- Checkpoints and lock waits
- Autovacuum activities
- Query IDs for correlation (`%Q` in log_line_prefix)

**Log Line Format:**
```
timestamp [pid] user@database query_id message
```

### 4. Performance Monitoring

**Additional settings enabled:**
- `compute_query_id = on` - Compute unique query identifiers
- `track_io_timing = on` - Track I/O timing statistics

## Directory Structure

The implementation requires the following directory structure:

```
data/
├── api/                    # API service data
└── postgres/
    └── logs/              # PostgreSQL log files (mounted to /var/log/postgresql)
```

This directory is automatically created by running `make datadir`.

## Setup

1. **Create required directories:**
   ```bash
   make datadir
   ```

2. **Start services:**
   ```bash
   make up
   ```

3. **View logs:**
   ```bash
   tail -f data/postgres/logs/postgresql-$(date +%Y-%m-%d).log
   ```

## Accessing Statistics

### From psql:

```bash
# Connect to database
make dbconn

# View pg_stat_statements
SELECT * FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;

# View current activity
SELECT * FROM pg_stat_activity;

# View table statistics
SELECT * FROM pg_stat_user_tables;

# View index usage
SELECT * FROM pg_stat_user_indexes;
```

### From Application Code:

The API can query `pg_stat_statements` to expose performance metrics:

```python
def get_query_stats(self):
    """Get query performance statistics from pg_stat_statements."""
    query = """
        SELECT 
            query,
            calls,
            total_exec_time,
            mean_exec_time,
            max_exec_time,
            stddev_exec_time
        FROM pg_stat_statements
        ORDER BY total_exec_time DESC
        LIMIT 100
    """
    return self._run_query(query=query)["result"]
```

## Future Enhancements

### Prometheus Integration

The issue mentions eventually connecting to Prometheus for monitoring. This can be achieved using:

1. **postgres_exporter** - Export PostgreSQL metrics to Prometheus
   ```yaml
   # docker-compose.yml addition
   postgres-exporter:
     image: prometheuscommunity/postgres-exporter
     environment:
       DATA_SOURCE_NAME: "postgresql://user:pass@postgres:5432/dbname?sslmode=disable"
     ports:
       - 9187:9187
   ```

2. **Key metrics to expose:**
   - Query execution times from pg_stat_statements
   - Index usage from pg_stat_user_indexes
   - Table bloat metrics
   - Connection pool statistics
   - Cache hit ratios

### Index Monitoring

To identify unused and missing indexes:

```sql
-- Unused indexes (never scanned)
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0
ORDER BY idx_scan;

-- Missing indexes (sequential scans on large tables)
SELECT 
    schemaname,
    tablename,
    seq_scan,
    seq_tup_read,
    idx_scan,
    seq_tup_read / NULLIF(seq_scan, 0) as avg_seq_tup_read
FROM pg_stat_user_tables
WHERE seq_scan > 0
ORDER BY seq_tup_read DESC;
```

## Performance Impact

The observability features have minimal performance impact:
- `pg_stat_statements`: ~1-2% overhead for query tracking
- `auto_explain`: Only logs queries exceeding 30ms threshold
- `log_min_duration_statement`: Only logs queries exceeding 15ms threshold

## References

- [pg_stat_statements Documentation](https://www.postgresql.org/docs/current/pgstatstatements.html)
- [auto_explain Documentation](https://www.postgresql.org/docs/current/auto-explain.html)
- [PostgreSQL Logging Configuration](https://www.postgresql.org/docs/current/runtime-config-logging.html)
