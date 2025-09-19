CREATE UNIQUE INDEX IF NOT EXISTS idx_migrations_filename ON migrations (file_name);
CREATE INDEX IF NOT EXISTS idx_migrations_file_sha256 ON migrations USING HASH (file_sha256);