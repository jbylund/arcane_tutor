# Near-Live Latency Comparison Against Scryfall

A small suite of queries run daily against both sylvan-librarian.com and api.scryfall.com,
published as a table through the existing badge pipeline.

Depends on [local-cache-bypass-header.md](./local-cache-bypass-header.md) for a deterministic,
assertable cache miss on our side.

## The measurement is mostly a methodology problem

A naive "time both, print the ratio" produces a number that is wrong by two orders of magnitude in
whichever direction flatters the author. Exploratory measurements below are all from one laptop on
one evening, `n` of 3–4 per cell — enough to establish which confounds matter, not enough to publish.

### Handshake is the largest removable term

`conn` and `tls` go to exactly zero on a reused connection.

| | new connection | reused |
|---|---|---|
| sylvan-librarian.com | 28 ms total | ~7 ms |
| api.scryfall.com | ~100 ms TTFB | ~20 ms |

So: warm the connection, discard the first sample. Both endpoints speak HTTP/2.

### Payload size dominates, and nearly fooled us

The first wide-query comparison looked like a 200× win. It was mostly serialization.

Same query, `t:beast cmc>3 pow>2`:

| | cards returned | fields/card | payload |
|---|---|---|---|
| sylvan-librarian.com | 100 | 9 | 41 KB |
| api.scryfall.com | 175 | 67 | 895 KB |

Re-running with narrow queries that return a handful of cards collapses the gap by an order of
magnitude on their side:

| query shape | ours (TTFB) | Scryfall origin (TTFB) |
|---|---|---|
| wide (100–175 cards) | 5.0–6.8 ms | 1,049–1,677 ms |
| narrow (1–5 cards) | 5.2–6.6 ms | 78–85 ms |

Our own time barely moves between the two, which is itself the interesting result — but a published
table built only on wide queries would be claiming credit for returning 9 fields instead of 67.

**Design consequence:** the suite must hold both shapes and report them separately, and the field
count must be stated. Matching payloads exactly is not possible — Scryfall has no field-selection
parameter — so the honest move is to disclose rather than normalize.

### Cache state has to be matched, and is second-order

Their edge and our in-process LRU are different mechanisms; there is no configuration in which they
are equivalent. What is achievable is labelling each arm truthfully:

- **origin vs origin** — our `X-Bypass-Cache` against a `cf-cache-status: MISS`. Forcing their miss
  means a novel query each run, which drifts the workload; a generator producing
  equivalent-difficulty-but-unseen queries is the fix.
- **warm vs warm** — our cache hit against `cf-cache-status: HIT`. This is what users actually
  experience and is the fairer headline.

Cache-layer differences are a few ms against a query-processing gap of tens to hundreds. Worth
getting right for correctness of labelling, not because it moves the number.

### Server compute can be differenced out

We publish `Server-Timing` (`handler`, `parse`, `engine_query`, `engine_collect`, `total`); Scryfall
does not. Compute is recoverable for both by differencing a known-cached against a known-uncached
request on one warm connection — network cancels, the remainder is server work.

The reason to trust that on their side is that it can be **calibrated on ours, where ground truth
exists**: our observed hit-vs-miss delta of ~1–2 ms matches the 0.6–2.8 ms `Server-Timing` reports
independently. Validate the technique where it can be checked, then apply it where it cannot.

### Confounds that cannot be removed, only disclosed

- **Load.** Scryfall serves the Magic community; we serve approximately nobody. This is the single
  largest uncontrolled variable and no amount of measurement technique touches it.
- **Geography.** Their Cloudflare edge is global; we are one origin. The result depends on where the
  runner sits, and a CI runner is not where our users sit either.
- **Corpus.** `total_cards` differs for identical queries (326 vs 340 on one sample) — different
  printing and language coverage, so the engines are not answering quite the same question.

## Publishing

Reuse what already exists: `scripts/gen_badges.py` writes JSON and SVG, `.github/workflows/badges.yml`
publishes to S3 behind CloudFront daily. This wants a sibling script on the same schedule and the
same upload step, not new infrastructure.

Output as an SVG table rather than shields endpoints — this is a grid of numbers, not a status chip.

**Scryfall's terms constrain the presentation.** Their docs require a `User-Agent` naming the
application and an `Accept` header, and note hard rate limits; a daily handful of queries is well
within courteous use, and samples should be spaced. Their guidelines also say their name may not be
used in a way implying endorsement — so a factual table is fine, their logo in our README is not, and
the framing should avoid reading as a marketing claim. Given the load confound above, that is also
just accurate.

## Open questions

- **Where does it run from?** A CI runner is convenient and reproducible; it is also a datacenter,
  which is nobody's browser. Reporting the vantage point may be enough.
- **How many samples per query** before the number is worth publishing? The wide-query spread
  (1,049–1,677 ms) is ~45% of the median, so a single sample per day would be mostly noise.
- **Does a failure to reach either service publish a gap, or hold the last good value?** The badge
  pipeline's existing answer is "skip and keep the previous upload", which likely applies here too.
- **Should the query suite live in the repo as a fixture** so a change to it is reviewable, rather
  than being generated fresh each run?
