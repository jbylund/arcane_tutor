# Rust in-process query engine replaces PostgreSQL in the search hot path (#490, #502, #505)

Search is now served by a Rust/PyO3 in-memory filter engine instead of PostgreSQL. Queries are
still parsed in Python; the AST is serialized to JSON, evaluated entirely in Rust against an
in-memory card store, and the top-N result dicts cross back over the FFI boundary. The SQL
path is retained in parallel as a transparent fallback — any engine exception logs a warning
and the request is served from PostgreSQL, and a cold (empty) store serves from SQL while a
background reload populates the engine.

Across a representative query mix the engine is **~76x faster** than the SQL path (geometric
mean 0.20 ms vs 14.9 ms against ~96k cards), with individual queries ranging from 20x
(`power+toughness>8`) to 190x (`t:merfolk and name:tide`). Full benchmark table in
[docs/prs/00490-rust-filter-extension.md](../prs/00490-rust-filter-extension.md).

## How it landed

**#490 — Rust in-process filter engine.** `QueryEngine` (PyO3, in
[card_engine/](../../card_engine/)) holds the card corpus on the Rust heap with prebuilt
indexes: name/oracle trigram maps, B-trees over cmc/power/toughness, and posting lists for
card types, subtypes, oracle tags, and is-tags. AND queries intersect candidate sets, OR
queries union them, and colors/types are bitfields so containment checks are single
instructions. Each AST node gained a `to_json()` method for serialization across the boundary.

**#502 — Shared-memory card store (rkyv + mmap).** The store moved out of per-worker memory
into a single rkyv archive in tmpfs (`/dev/shm/sylvan_librarian_cards`) that every worker mmaps
read-only and queries with zero deserialization. This collapsed ~800 MB–1 GB of duplicated
per-worker RSS into one shared copy, and only one worker (cross-process flock + atomic rename
publish) pays the reload cost. Details in
[docs/prs/joe/shared-memory-card-store.md](../prs/joe/shared-memory-card-store.md).

**#505 — Streaming engine reload.** The DB → store reload became a staged streaming pipeline
(`reload_begin` / `add_batch` / `reload_commit`): a server-side cursor feeds 2,000-row batches
into Rust, and the archive is serialized directly to file instead of through a heap buffer.
Building-worker peak memory dropped from ~1.3 GB to ~350 MB, which is what made it safe to
enable the engine in memory-constrained deployments. Details in
[docs/prs/joe/00505-engine-incremental-loading.md](../prs/joe/00505-engine-incremental-loading.md).

## Operational notes

- The engine is gated by `ENABLE_ENGINE` (now `true` in all environments).
- The Rust wheel builds in a dedicated `rust-builder` Docker stage via maturin; locally,
  `make engine` builds and installs the extension, and the test targets depend on it.
- Bulk imports trigger an engine reload to keep the store current; queries keep serving the
  old archive until the atomic rename publishes the new one.
