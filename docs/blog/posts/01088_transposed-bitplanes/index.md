---
title: "We Replaced 31,000 Filter Calls with 492 Words of AND"
date: 2027-02-23
publishDate: 2027-02-23
tags: ["rust", "performance", "query-engines", "bitmap-indexes", "benchmarking"]
summary: "After streaming removed the ranking work, our color queries spent 0.25 ms delivering a one-cycle predicate: 31.5k filter-tree dispatches, each loading a 250-byte struct to read one byte. We transposed the low-cardinality dimensions into bitplanes — one 4 kB bitset per (dimension, value) — and the match phase became a word loop. Targeted queries: 1.89x geomean, 3.3x on the best rows, 103 kB total."
---

When we [split selection into a match phase and an order phase](../01056_streamed-selection-sort-permutations/), the profile of a broad color query collapsed to something almost embarrassing: `c:g` evaluates as `bits & mask == mask` — one AND and a compare on a u8 — and the query still cost 0.25 ms.
All of it was delivery.
The driver loop ran 31,508 iterations of filter-tree dispatch, and each iteration loaded a 250-byte card struct's cache line to read one byte of it.
This post is about deleting that loop: transpose the low-cardinality dimensions into bitplanes — one bitset per (dimension, value) — and evaluate the filter as word-wide boolean algebra over 4 kB arrays.
Targeted queries got 1.89× faster (geomean over 19 configurations, 3.3× on the best rows), the control set held at 0.99×, and the whole index is 103 kB ([PR #633](https://github.com/jbylund/sylvan_librarian/pull/633)).

## Selectivity Was Useless Without an Index

Colors never got an index.
Types had postings, text had trigrams, numerics had range indexes — but a color test was "one instruction, why bother."
The consequence shows up in the baseline: `c:g` matches 6,407 cards and costs 0.257 ms; `c=uw` matches **370** and costs 0.221 ms.
Selectivity bought nothing, because every color query paid the same full scan — the per-card *visit* was the cost, not the per-card work.

That framing is the previous post's lesson one level down.
Streaming removed the work after matching; what remained is that testing one bit per card still drags a cache line per card through the core.
At 31.5k cards × ~250 bytes, a color query streams ~8 MB of struct memory to consume 4 kB of information.

## One Bitset per Value, Plain Words, No Compression

The fix is the oldest index in the database book: a [bitmap index](https://en.wikipedia.org/wiki/Bitmap_index) — the [five-bit color mask each card already carries](../00576_bitmap-fields-color-identity/), transposed, so that bit *i* of the "green" plane says card *i* is green.
We build [26 planes](https://github.com/jbylund/sylvan_librarian/blob/6b63c6a/card_engine/src/planes.rs#L46-L67) at store-reload time: six color bits × {colors, color identity} plus the fourteen card-type bits.
At 31,508 cards a plane is 492 u64 words — 4 kB, half a page — and the full set is 103 kB on a 75 MB archive.

We deliberately skipped roaring/WAH compression.
Compressed bitmaps trade ALU work for memory traffic, which wins when bitmaps are millions of bits and mostly cold.
Ours are 492 words and cache-resident; the entire evaluation is a single pass with hardware popcount at the end.
Plain words are also what keeps the algebra trivial — no run-length decoding inside an AND loop.

## Every Operator Compiles, Including the Weird Ones

Scryfall color syntax is set comparison, and all six operators are mask algebra over the planes ([compile_plane](https://github.com/jbylund/sylvan_librarian/blob/6b63c6a/card_engine/src/planes.rs#L146-L182)):

| query | compiles to |
|---|---|
| `c>=uw` | W ∧ U |
| `c=uw` | W ∧ U ∧ ¬B ∧ ¬R ∧ ¬G ∧ ¬C |
| `-c:g` | ¬(G) |
| `id:g` | ¬W ∧ ¬U ∧ ¬B ∧ ¬R ∧ ¬C |

That last row is the fun one: `id:g` is commander-identity *subset* semantics — "fits in a green deck" — so it constrains only what's **outside** the mask.
The green plane is never read, and colorless cards match, which is exactly why Sol Ring belongs in the result.

The compiler is allowed to emit complement (`Not`) only because these nodes are two-valued.
The engine's filter evaluation is four-valued — SQL-style `Null` for missing attributes, plus a printing-dependent marker — and complementing a `Null`-capable node would turn "unknown" into "true."
Color and type comparisons never return either non-boolean state, so plane algebra reproduces their truth exactly; anything else refuses to compile and stays on the normal path.
[split_planes](https://github.com/jbylund/sylvan_librarian/blob/6b63c6a/card_engine/src/planes.rs#L195-L221) then consumes what compiled: a fully-expressible tree is consumed whole, a top-level `And` partitions into plane-children and residual children, and an `Or` mixing the two stays entirely residual — a mask OR-ed with "evaluate this per card" narrows nothing.
For mixed queries like `t:creature c:g o:draw`, the bitmap intersects the text index's candidates and the residual runs only over survivors.

## The Invariant That Held for 31,507 of 31,508 Cards

Midway through we almost got clever.
A card's colors are a subset of its color identity — identity is colors plus rules-text mana symbols — so why store both sets of planes?
Before building on that, we checked it against the corpus, and it is true for every card except one: **Fallaji Wayfarer**, whose "this card is all colors" ability makes its *colors* WUBRG while its mana cost keeps its *identity* at {G}.
One card out of 31,508, and it would have silently corrupted every identity query that leaned on the shared planes.
The deduplication would have saved 20 kB.
Fallaji Wayfarer is now a named fixture in the parity suite, and both sets of planes are built independently from the actual masks.

## Results

Same protocol as the series: live 97,206-printing corpus, 20 warmup iterations then a 3 s timed window per configuration, M-series MacBook, totals cross-checked against the pre-change build on every row.

Targeted configurations (plane-expressible filters plus mixed filters using the bitmap as a candidate mask) — **geomean 1.89×** over 19 configs:

| query | matches | before | after | |
|---|---|---|---|---|
| `c>=uw` | 658 | 0.228 ms | 0.068 ms | 3.34× |
| `c=uw` | 370 | 0.221 ms | 0.066 ms | 3.33× |
| `c:r or c:g` | 12,391 | 0.394 ms | 0.125 ms | 3.14× |
| `c:g` (unique=printing) | 18,621 | 0.253 ms | 0.082 ms | 3.07× |
| `c:g` | 6,407 | 0.257 ms | 0.089 ms | 2.87× |
| `c:g t:creature` | 4,166 | 0.196 ms | 0.084 ms | 2.33× |
| `t:creature c:g o:draw` | 384 | 0.204 ms | 0.111 ms | 1.84× |
| `t:creature` | 17,317 | 0.148 ms | 0.149 ms | 1.00× |

Controls (dimensions we haven't planed, plus selective queries the planner must leave alone): **geomean 0.99×** over 11 configs — `f:modern` 1.01×, `name:soldier` 0.96×, the mixed-`Or` shape 0.98×.

Two rows deserve honesty.
The *selective* color queries won biggest — `c=uw` at 3.34× — which surprised us until it didn't: colors had no index of any kind, so those 370-match queries had been paying the full-scan tax hardest.
And `t:creature` didn't move at all: type postings already narrowed it to the same 17.3k candidates, and its remaining 0.149 ms is the match-counting and page-emission floor, which no representation of the *filter* can touch.
The type planes earn their keep in conjunctions instead, where they fold into the bitmap for free rather than paying a postings union.

## Planes Don't Have a Crossover

The reason this representation fits where postings failed us is that its cost is flat.
[Last month we measured](../00992_index-selectivity-crossover/) broad postings *losing* to full scans — a 31k-entry candidate list costs its length in gather-and-sort, and poisons intersections its selective partner was already winning.
A plane costs 4 kB and one word-loop pass whether its popcount is 12 or 31,000.
There is no density at which it loses, so there is no threshold to tune — and a broad conjunct AND-ed into a bitmap can only shrink the candidate set, never bloat it.

Sparse planes are not waste, either; they have dense complements.
`-t:world` matches 31,482 cards and is cheap *because* the 26-card World plane exists to invert.
The rule we've settled on: for a closed universe (14 type bits, 6 color bits), plane everything; for an open vocabulary (~1,500 subtypes and keywords), promote only the dense values and leave the rest on postings.
Next up is legality, the dimension we'd been stuck on precisely because `f:legacy` is 99.7%-legal — un-indexable by any structure whose cost scales with its match count, which is no longer a property our index has.

The previous post ended with the lesson that the fastest per-row work is the row you never touch.
This one is the corollary: the fastest way to never touch a row is to have answered for it and 63 of its neighbors in one instruction.
