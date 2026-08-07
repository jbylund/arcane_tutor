# Token-Gated Cache Bypass Header

Add a request header that makes `CachingMiddleware` skip the response cache, gated by a shared
secret, so a benchmark can force a real miss deterministically and *prove* it happened.

Prerequisite for [local-scryfall-latency-comparison.md](./local-scryfall-latency-comparison.md).

## Why

A latency benchmark that compares us against another service has to know which arm of the
comparison it is measuring. Right now a caller has no supported way to say "do the work" — the
middleware decides, and the only signal back is `X-Cache: hit|miss` after the fact.

That matters less today than it will later. Measured against the live site (from a laptop, not a CI
runner):

| | value |
|---|---|
| wall clock, cold connection | 28 ms |
| wall clock, warm connection | ~7 ms |
| `Server-Timing: total` | 0.6–2.8 ms |

Compute is a small share of the response, so bypassing the cache moves the total by a millisecond or
two. The reason to build this anyway is methodological, not numerical: a measurement you only trust
while the numbers are flattering is not a measurement. If a regression ever made a query cost 500 ms,
cached-vs-uncached would become the dominant term, and that is precisely the moment the benchmark
needs to have been correct all along.

## Design

`X-Bypass-Cache: <token>` on the request.

**Gate it on a secret.** An open bypass is a cheap amplifier against a public deployment: any caller
could make every request skip the cache and do full engine and database work. The token lives in
settings (env-provided), and the comparison is `hmac.compare_digest` — not `==` — so the check does
not leak the token through timing. Absent or wrong token means the header is ignored entirely, and
the request is served normally. No error, no signal that the feature exists.

**Skip the read *and* the write.** The obvious implementation only skips the lookup, which leaves the
benchmark inserting an entry per sample. With `LRUCache(maxsize=10_000)` a handful of daily queries
would not meaningfully evict real traffic, but the benchmark should not perturb the thing it
measures, and a bypassed response is by definition not the response a normal caller would have
stored.

**Make it assertable.** Set `X-Cache: bypass` rather than reusing `miss`. The benchmark then checks
the header on every sample and fails loudly if the bypass did not take effect. Without this, a
misconfigured token degrades silently into "our cached responses vs their uncached ones" — a
comparison that looks spectacular and means nothing. That failure is invisible in the output, which
is what makes it worth designing against rather than trusting.

## Implementation sketch

Both hooks are in [api/middlewares/caching_middleware.py](../../api/middlewares/caching_middleware.py):

- `process_request` — after the `settings.enable_cache` and `CACHEABLE_METHODS` guards, check the
  header. On a valid token, set `req.context["cache_bypass"] = True`, set `X-Cache: bypass`, and
  return without probing the cache.
- `process_response` — return early when `req.context.get("cache_bypass")` is set, alongside the
  existing `cache_hit` check.

`_cache_key` needs no change: the bypass never reaches a lookup, and the header must **not** join the
key, or every distinct token value would carve out its own cache namespace.

Note that `X-Cache` is already in `_UNCACHEABLE_HEADER_PREFIXES`, so a stored entry can never replay
a `bypass` value to a later caller. That invariant is what makes the header trustworthy as an
assertion target, and it is already in place.

## Tests

`api/middlewares/tests/` covers the middleware directly. Worth asserting:

- valid token → cache is neither read nor written, response carries `X-Cache: bypass`
- absent / wrong token → behaves exactly as today, no `bypass` value ever emitted
- bypass on a request whose key is already cached → the stored entry survives untouched
- the token is not part of the cache key — two different valid tokens hit the same entry

## Open questions

- **Should the bypass be rate-limited independently of the token?** A leaked token otherwise
  re-creates the amplifier it was meant to prevent. Cheapest answer may be a per-token request
  budget, but there is no rate-limiting layer in the middleware stack today.
- **Is a single shared token enough**, or should this be per-caller so one can be revoked without
  rotating for everyone? One caller is the near-term reality; more than one is speculative.
- **Should `Cache-Control: no-cache` on the request do the same thing for authenticated callers?**
  It is the standards-blessed spelling, but overloading it risks a browser or proxy sending it
  unintentionally, so a custom header is the safer default.
