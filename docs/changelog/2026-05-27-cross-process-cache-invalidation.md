# Cross-process cache invalidation

Implemented Option B from the [per-worker cache staleness](../issues/per-worker-cache-staleness.md)
design doc. The server runs 10 independent Bjoern worker processes with `SO_REUSEPORT`; each had
its own `_query_cache` and `_all_preferred_cards` that were only cleared on the worker that
handled a mutation, leaving the other nine serving stale data indefinitely.

## What changed

Added a `cache_generation` shared `multiprocessing.Value("i", 0)` passed to every worker at
startup. `_clear_caches()` increments it under a lock. Each worker checks the current generation
on every request via `GenerationCache` (in `api/utils/generation_cache.py`) and discards its
local caches if it has fallen behind. The check is a single integer read in the hot path.

`_all_preferred_cards` and `_search` are now stored as `generation → value` LRU maps (maxsize=1),
so a stale worker transparently rebuilds on its next request after a mutation on any worker.
