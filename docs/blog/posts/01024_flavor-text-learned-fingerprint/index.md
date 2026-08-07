---
title: "We Priced a Trigram Index at 9 MB and Shipped 16 Bytes per Document Instead"
date: 2027-02-09
publishDate: 2027-02-09
tags: ["rust", "performance", "indexing", "text-search", "benchmarking"]
summary: "Flavor text owned our search engine's slow tail. Instead of the 9 MB trigram index, we scan the 26k distinct strings at bind time behind a 128-bit learned fingerprint — features selected greedily against a needle workload, with the alphabet backfilled so no query ever goes unfiltered. The worst queries got 8–15x faster for 1 MB."
---

Every text field in our Rust card-search engine had an index except one, and that one owned the slow tail.
Flavor text — the italic quote at the bottom of a Magic card — is the least-searched field we have, and `ft:` queries were the most expensive thing the engine did: 1.4 ms for a bare search, 2.4 ms when one appeared inside an `or`.
The obvious fix was the same trigram index the oracle-text field uses, and we priced it out: about 9 MB of archive, 12% of the whole store, for a field almost nobody queries.
This post is about what we shipped instead: a full scan of the 26,321 distinct flavor texts, hidden behind 16 bytes per text of learned fingerprint — and how choosing which 128 bits took four wrong answers.

## Why flavor text was the worst query

Two compounding problems, one obvious and one structural.

The obvious one: no index means `ft:dream` ran a substring check against every printing — 97,206 `contains` calls per query.
That's wasteful in a specific way: there are only 26,321 *distinct* flavor texts (reprints share them), and the store already interns them — each printing holds an integer id into a shared string table.
We were evaluating the same string up to forty times per query because evaluation ran at printing cardinality instead of distinct-text cardinality.

The structural one is nastier.
Our candidate narrowing is advisory — indexes propose candidates, evaluation verifies them — and an `or` node can only narrow if *every* child can.
One unindexable child voids narrowing for the whole node.
So `(o:flying or ft:dream)` couldn't use the oracle-text trigram index it was 90% composed of: the `ft:` child forced the entire query to a full scan, and the engine's worst measured queries were all `or`-combos with a flavor child.

## The index we didn't build

The oracle-text field solves this with a dedup-CSR trigram index, so we ran its arithmetic against the flavor corpus: 26,321 distinct texts produce 2.08 M trigram postings across 17,666 distinct trigrams — about **9 MB archived**, 12% of the entire 73 MB store.
(We trust this estimate because the same arithmetic reproduces the oracle index's known ~15 MB.)
It would deliver ~0.1–0.2 ms, and it would be the wrong trade: flavor is the least-searched text field, and PostgreSQL-style GIN economics — pay memory for the hottest access path — argue *against* indexing your coldest one.
Later, comparing designs, we re-priced it with heterogeneous 1–3-grams and u16 ids at 5.0 MB; cheaper, same verdict.

The alternative came from a pattern already in the codebase.
Artist predicates evaluate once against the 2.2k distinct artist names at query-bind time and rewrite themselves into resolved id sets ([PR #605](https://github.com/jbylund/sylvan_librarian/pull/605)); per-printing matching becomes an integer binary search.
Flavor is the same shape at 12× the vocabulary: at bind, run the predicate over the 26.3k distinct strings once, rewrite the node into a resolved match set, and add a CSR mapping each distinct text to the printings that carry it (~0.4 MB).
That CSR is the structural fix — matched texts expand to printing candidates, so `ft:` participates in `or` narrowing like any indexed field ([the implementation](https://github.com/jbylund/sylvan_librarian/blob/528c98e/card_engine/src/lib.rs#L1039-L1107)).

One problem left: the bind scan itself is 26.3k substring checks over 2.3 MB of strings, ~0.2–0.4 ms per `ft:` term.
Acceptable for a rare field.
But it turned out we could make most of it disappear for 16 bytes per text.

## Necessary conditions and 26 letters

A substring check has a cheap necessary condition: if `dream` appears in a text, every letter of `dream` appears in that text.
Store a 26-bit letter mask per distinct text, build the needle's mask at bind, and `(text_mask & needle_mask) == needle_mask` filters in one AND-compare — texts missing any needle letter can't match and skip the real check.

How well that works depends entirely on letter frequencies, so we measured them across the distinct texts:

| letter | % of texts | letter | % of texts |
|---|---|---|---|
| e | 98.9 | k | 47.4 |
| t | 97.7 | j | 10.4 |
| a | 97.1 | z | 10.4 |
| s | 96.1 | x | 9.9 |
| v | 51.5 | q | 5.9 |

Standard English, with a cliff after `k`.
The consequence: a needle with a rare letter filters brilliantly (`zombie` passes 6.0% of texts), and a needle of common letters filters barely at all (`dream` passes 65.8%, `death` 80.2%).
Geomean over a realistic needle workload: 48.9% pass.
Half is not nothing, but the bits are badly spent — the `e` bit eliminates 1.1% of texts and occupies the same slot as a bit that could eliminate 85%.

## Four wrong answers about which bits to keep

The letter mask generalizes: any 1-, 2-, or 3-gram is a valid necessary-condition feature, since a text containing the needle contains every n-gram of the needle.
So the design question became: **which 128 features?** (128 because `u128` is still two registers or one vector op per text — the test stays a single compare.)
We selected against the live corpus with a workload of needles sampled from the flavor texts' own vocabulary — always validating on held-out words the selection never saw — and got it wrong in instructive ways:

**Wrong answer #1: maximize entropy.**
Pick features with document frequency near 50% — maximum information per bit.
This packed 14.6 bits of joint entropy into 32 features (25,782 distinct fingerprints over 26,321 texts — nearly a perfect hash of the corpus) and *worsened* the queries that used to work: `zombie` went from 6% pass to 61%, because `z` at 10% df carries little average entropy and got excluded.
Corpus entropy is the wrong objective.
A filter bit only fires when the needle contains its feature, and a rare gram is the best possible filter *in exactly those cases* — its low average information is the point.

**Wrong answer #2: optimize the workload directly, greedily, from scratch.**
Choosing features to minimize pass rate over the training needles fixed the average (24.8% held-out geomean) and introduced holes: needles containing none of the 32 chosen grams passed at 100% — `goblin`, `death`, and `sword` sailed through unfiltered.

**Wrong answer #3: reserve all 26 letters, learn the rest.**
Letters guarantee every needle fires something.
26 letters + 38 residual-trained grams reached 13.3% — but "residual-trained" is the load-bearing word: each gram was selected for what it eliminates *after* the letters have filtered, or the learned bits just duplicate the alphabet.

**Wrong answer #4: keep the common letters.**
When we let letters compete for their seats instead of reserving them, the greedy kept exactly two (`p` and `v`) and spent the other slots on grams.
Geomean improved to 9.0% — and the holes came back (`quest` lost its `q` and regressed 5.8% → 47.9%).
The alphabet's value was never `e`; it was the rare letters the workload undersamples.

**The shipped answer: greedy with a backfill invariant.**
Run the greedy freely, but stop when the remaining slots equal the count of letters not yet chosen, and backfill those letters.
Every possible needle fires at least one bit — the worst case degrades to the letter-mask floor, never to an unfiltered scan — and the greedy spends everything else on residual-trained grams ([the frozen table](https://github.com/jbylund/sylvan_librarian/blob/6b49b42/card_engine/src/lib.rs#L992-L1009), regenerated by [a committed script](https://github.com/jbylund/sylvan_librarian/blob/6b49b42/scripts/generate_flavor_fingerprint.py)).
One more lever mattered: training-set size.
Our first workloads used 300 needles (a runtime-conservative habit from the iteration loop), and the greedy's tail picks were memorizing one- and two-needle coincidences.
Retraining on a 4,500/500 random split of the vocabulary's top 5,000 words closed the train/held-out gap completely and nearly halved the pass rate ([PR #626](https://github.com/jbylund/sylvan_librarian/pull/626)); extending the pool to 10,000 words changed nothing — at 4,500 needles the 128-bit budget is the binding constraint, not the data.
Final held-out geomean: **1.8% pass, zero holes, worst needle 67%**.
The fingerprint pass over all 26.3k texts costs ~25 µs (421 kB, cache-resident), survivors get the real substring check, and needles whose mask is zero skip the filter entirely.

The whole selection problem compresses to one table — note that every improvement came from changing the *objective*, never the mechanism, which stayed a single AND-compare throughout:

| design | bits | held-out pass (geomean) | unfiltered needles |
|---|---|---|---|
| all 26 letters (baseline) | 26 | 48.9% | none |
| max-entropy grams (#1) | 32 | 33.8% | rare-letter needles regress instead |
| workload-greedy from scratch (#2) | 32 | 24.8% | yes — `sword`, `death` pass at 100% |
| all letters + residual-trained grams (#3) | 64 | 13.3% | none |
| letters compete for seats (#4) | 64 | 9.0% | back — 11% of needles |
| rare letters reserved, width doubled | 128 | 3.0% | 3% of needles |
| greedy + letter-backfill invariant | 128 | 3.5% | none; worst needle 86% |
| **same, trained on the 4,500-word split (shipped)** | **128** | **1.8%** | **none; worst needle 67%** |

(The first seven rows share one held-out set from the early 300-needle experiments; the final row is validated on the fresh 500-word split, where the letter-mask baseline measures 50.4% — the shipped table is 28× more selective than the baseline on the same test set.)

Two steps in that table trade in opposite directions, deliberately: the backfill invariant gave back half a point of geomean (3.0% → 3.5%) to buy the guaranteed floor, and widening the training set then bought that back nearly twice over.
For calibration: after the split retrain, training and held-out geomeans agree (2.0% vs 1.8% — no memorization left to tax), and the theoretical floor for any necessary-condition filter — the full 6,552-gram inverted index — is 0.22%.
The fingerprint lands within ~8× of the exact index's selectivity at 1/25th of its memory, and since each surviving text costs only one substring check, that residual gap is worth about 15 µs — which is the entire argument for not spending the 5 MB.

Old-timers will recognize the shape: this is a **signature file**, the pre-inverted-index design from 1980s information retrieval, with two updates — the features are learned against a workload instead of hashed blindly, and the corpus is small enough that the classic verdict ("inverted files beat signature files") flips.
We measured that flip directly: posting lists over the same feature set produce byte-identical candidates (they encode the same necessary condition, column-wise instead of row-wise) at 7× the memory, and the inverted index only pulls ahead by un-capping the vocabulary — the 5 MB the table's bottom line argues against.

## What shipped

[PR #622](https://github.com/jbylund/sylvan_librarian/pull/622): the distinct-text bind scan, the fingerprint prefilter, the CSR narrowing, and SQL-NULL semantics for flavorless printings (a card with no flavor text matches neither `ft:dream` nor its negation, same as before).
Measured on the live 97,206-printing corpus — in-process engine, 20 warmup iterations then a 3-second timed window, `unique=card`, default prefer, edhrec ordering, `limit=100`, M-series MacBook:

| query | before | after | |
|---|---|---|---|
| `ft:dream` | 1.441 ms | 0.089 ms | 16.2× |
| `ft:fire` | 1.465 ms | 0.118 ms | 12.4× |
| `ft:"the dream"` | 1.440 ms | 0.033 ms | 43.6× |
| `(o:flying or ft:dream)` | 2.336 ms | 0.293 ms | 8.0× |
| `t:creature ft:dream` | 0.834 ms | 0.123 ms | 6.8× |
| `ft:zzzqqq` (matches nothing) | 1.565 ms | 0.017 ms | 92× |
| `t:goblin`, `t:creature`, `o:draw` (controls) | 0.061 / 0.251 / 0.199 | 0.063 / 0.248 / 0.208 | unchanged |

Total archive cost: **1.0 MB** (71.6 → 72.6 MB) — the CSR, the dense-id remap, and 16 bytes of fingerprint per distinct text — against the 5–9 MB the trigram index wanted for roughly the same latency.
The engine's former worst queries dropped out of the slow tail entirely; the new worst query is a different `or`-pathology (`o:draw or cn:100`, 1.96 ms) whose fix is a forty-line range index, not a text-search structure.

The honest caveats.
The bind scan still costs ~40–50 µs per `ft:` term per query — fine for a cold field, and the trigram index remains the documented escalation path if flavor search ever becomes hot (drop-in: same CSR, add postings).
The workload is still a proxy — single words of four-plus letters sampled from the corpus's own vocabulary, so phrases (which filter better) and two-to-three-character needles (which bottom out at the letter floor) sit outside the measured geomean, and we haven't validated against a production query log.
When one accumulates, retraining is a one-command rerun of the committed script.
And one `or`-combo didn't move: `(ft:fire or frame:showcase)` improved just 1.4×, because `frame:` is now the unindexable child voiding the narrowing.
The disease is cured per-field, not in general.

Staleness, though, costs nothing but selectivity: texts and needles are masked with the same frozen table, so the superset test stays sound no matter how the corpus drifts — the filter just gets gradually worse at its job, never wrong.
That property is what let us freeze 128 strings into a source file and stop worrying.
The most useful thing we learned is transferable: when a filter's features must appear in the *query* to fire, optimize feature selection against a query workload, not against the corpus — and spend a few bits guaranteeing the floor, because the average case was never the problem.
