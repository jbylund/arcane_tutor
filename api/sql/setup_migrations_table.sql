CREATE TABLE IF NOT EXISTS migrations (
    file_name text not null,
    file_sha256 text not null,
    date_applied timestamp default now(),
    file_contents text not null
)