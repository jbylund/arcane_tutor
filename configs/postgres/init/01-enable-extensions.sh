#!/bin/bash
set -e

# Enable pg_stat_statements extension
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
EOSQL

echo "PostgreSQL extensions enabled successfully"
