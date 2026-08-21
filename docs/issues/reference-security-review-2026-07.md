# Security Review, July 2026 — Scope and Verified-Clean Results

Black-box review of `https://sylvan-librarian.com/` plus a source review of the request path, run from
a laptop on 2026-07-26.

This doc is not an umbrella over the findings — each of those is independently shippable and lives in
its own doc. What it holds is the part that belongs to no single finding: what was checked and found
**clean**, so it does not get re-audited.

Reachability of the mutating endpoints was established with a **read-only** endpoint, deliberately,
rather than by firing state-changing ones at production. Where a finding's impact is reasoned from
source rather than demonstrated, its doc says so.

## Findings

| doc | severity | status |
| --- | --- | --- |
| [done/local-admin-route-split-child-resources.md](./done/local-admin-route-split-child-resources.md) | critical | **fixed** 2026-08-21 across #794, #961–964, #966 — admin routes split behind an authenticated `/_admin` mount; doc declassified and moved to `done/` |
| [done/00781-api-port-exposure.md](./done/00781-api-port-exposure.md) | high | **fixed** 2026-07-27 — loopback bind shipped in #781, nginx upstream repointed; doc declassified and committed |
| [done/00782-error-response-disclosure.md](./done/00782-error-response-disclosure.md) | medium | **fixed** 2026-07-27 in #782 — opaque 500 body plus a regression test; doc declassified and committed |

All findings from this review are now fixed.

A fourth item at the time of the review — nginx hardening, proposing HSTS and rate limiting — was
**dropped rather than fixed**. Both halves were examined and declined on 2026-07-27; the reasoning is
under "Considered and not pursued" below, and the doc was deleted rather than kept as a description of
two things we are deliberately not doing.

A GitHub PAT sitting unignored at the repo root — a separate finding — is **fully resolved**. The file was
moved out of the repo on 2026-07-26 and the token rotated on 2026-07-27. It was verified never to have
been committed on any ref (no history for the path, and no commit contains the value), and `*.pat` is
now gitignored against a recurrence. No doc is kept for it: the write-up would have no value to a
reader once fixed.

The review's one sequencing dependency — the port exposure undermining any proxy-side control — is
moot now that the bind is fixed and the proxy-side work it gated was dropped.

## Checked and clean

Worth recording so these do not get re-audited.

- **No SQL injection.** Both f-string SQL sites are safe: `api/api_resource.py:1069` interpolates the
  module constant `_ENGINE_COLUMNS_FROM_MODULE`, and `:706-709` interpolates a `statement_timeout`
  that is `isinstance`-checked as a non-negative `int` first, with all callers passing internal
  constants. The user-facing `fields=` parameter is allowlisted against `RESULT_FIELD_COLUMNS`
  (`_resolve_result_fields`, `:1160-1181`), and the parser → SQL path is parameterized throughout.
- **TLS.** TLS 1.3, valid Let's Encrypt cert, TLS 1.0 and 1.1 both rejected. HTTP 301s to HTTPS.
- **Postgres is not exposed.** 5432 refused from outside; the compose file publishes no port for it.
- **Security header baseline.** CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`,
  `Permissions-Policy` all present and reasonable.
- **Input validation on the search path is solid.** Malformed `limit`, malformed UTF-8 in `q`, negative
  `num_cards`, and bogus enum values all produced clean 400s or 200s rather than reaching an exception
  handler.
- **CORS `access-control-allow-origin: *` is fine as things stand.** There are no cookies or sessions,
  and admin auth (added 2026-08-21, see below) is a header credential a browser never attaches
  automatically cross-origin, so a wildcard still grants a cross-origin caller nothing it could not
  get with a direct request. This stops being true if admin auth is ever switched to a **cookie** —
  see the auth section of the route-split doc.

## Considered and not pursued

- **Request rate limiting, beyond what is already in place.** Declined 2026-07-27. Two rationales were
  on the table and neither survives. The *write surface* — unauthenticated import and backfill
  endpoints — is real, but a `limit_req` zone slows abuse without gating it; the route split in
  [done/local-admin-route-split-child-resources.md](./done/local-admin-route-split-child-resources.md)
  is the actual control, and now that it has landed there is nothing left for a limit to protect. The *read cost*
  rationale does not hold either: engine-path latency over a 520-query corpus
  (`benchmarks/survey/752-baseline-main.csv`) is 61 μs p50, 174 μs p90, 571 μs p99, 1,401 μs p100, so
  even a small worker pool absorbs thousands of requests per second of the most expensive query
  measured. Saturating that is a bandwidth problem, not a query-cost one, and per-IP limits are the
  wrong tool for it.

  Note for anyone revisiting: the original write-up measured a hostile regex
  (`o:/.*a.*e.*i.*o.*u.*/`, limit 175) at 43–78 ms and concluded the engine copes. Right conclusion,
  unusable number — that is end-to-end `curl` wall-clock, dominated by TLS handshake, proxy hop, and
  serializing ~175 cards. It never isolated engine time, so server capacity cannot be reasoned about
  from it in either direction. Use the benchmark corpus instead.

  What would reopen this: an unbounded-wall-clock path reachable anonymously (the SQL fallback is
  capped by `statement_timeout`, the engine path is not capped but is sub-millisecond). The other
  original trigger — the admin routes staying ungated indefinitely — is now moot; they're gated as of
  #966. In the remaining case the right control is a request-time ceiling or a bounded queue, not
  `limit_req`.

- **HSTS (`Strict-Transport-Security`).** Declined 2026-07-27, **status stale as of 2026-08-21 — see
  note below before treating this as settled.** The response set is otherwise complete — CSP,
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` — and HTTP 301s to
  HTTPS, so what HSTS would close is the plaintext-301 window for return visitors against an *active*
  on-path attacker. Three things made that worth little at the time: browsers default to HTTPS-First
  and auto-upgrade typed navigations, so most of the window was already shut; at the time there was
  nothing to steal behind the origin (no cookies, no auth, no sessions — `localStorage` held only a
  theme preference); and the same attacker can use a lookalike domain, which HSTS does not address.

  Against that, HSTS removes the click-through escape hatch when TLS breaks. The cert is a **wildcard**
  (`*.sylvan-librarian.com`), which forces DNS-01 validation — the most failure-prone renewal path,
  4+ events a year on a 90-day Let's Encrypt cert. Small benefit against a real if unlikely outage mode.

  Two sub-decisions, settled in case this returns: `includeSubDomains` would have been **free** — the
  cert is already wildcard so every subdomain can serve TLS today, `www` already returns 200 over
  HTTPS, and the image CDN is on `cloudfront.net`, a different domain entirely and therefore out of
  scope. The original write-up's worry that it might be "awkward if a plaintext subdomain is ever
  needed" is technically true but not a real constraint. `preload`, by contrast, is a firm no: it bakes
  into browser binaries and cannot be reversed on your own schedule, and it only buys first-ever-visit
  protection.

  Two things to get right if this is ever revisited, because both were initially got wrong:
  - **max-age does not have to relate to certificate lifetime.** HSTS pins the *host to HTTPS*, not a
    certificate; cert pinning was HPKP, removed from browsers in 2018 for causing exactly these
    self-inflicted outages. max-age also refreshes on every successful HTTPS response, so it measures
    time since the host last worked, not time since one visit. A 90-day cert is fine with a 1-year
    max-age.
  - **A short max-age is not a weaker version of the control, it is a no-op.** A 300-second pin only
    helps a visitor returning inside five minutes. It is a deployment-safety rung on the way to a real
    value, so shipping one and never ramping is pure cost. Decide on the destination first.

  An implementation was written and discarded: a `HSTS_MAX_AGE` env read by `SecurityHeadersMiddleware`,
  defaulting to off. That is the right shape if it returns — the app listens on plain HTTP behind the
  proxy and reads no `X-Forwarded-Proto`, so it cannot detect TLS and must not guess (RFC 6797 §7.2
  forbids sending HSTS over non-secure transport). The operator asserts TLS via env, exactly as
  `BIND_ADDR` works. Gating on `X-Forwarded-Proto` is the trap: nginx's config is out of tree, so if it
  does not set the header the result is a silent no-op.

  This becomes worth revisiting the moment the site holds something worth stealing — admin auth, most
  likely, per the note on CORS above.

  **That moment arrived 2026-08-21**: admin Basic Auth shipped in #966. Basic Auth credentials travel
  as a trivially-reversible base64 header, not a cookie, but they are still bytes on the wire — an
  active on-path attacker catching one request in the plaintext-301 window now has something real to
  steal, where before this decision was declined there was nothing behind the origin worth the attack.
  This doesn't re-decide HSTS (that's a real tradeoff call, not a doc-cleanup call), it just means the
  "nothing to steal" leg of the original reasoning no longer holds and the decision should be revisited
  with that in mind rather than treated as still-settled.

## Declassified 2026-08-21

All three findings are now fixed, so this doc is committed as `reference-security-review-2026-07.md`
— nothing here describes a live defect any more, only what was checked and what was fixed and where.
