# N-Way Estimator Follow-Up Queue

Tracks what's left from the `And`-arm cardinality-estimation arc (Rounds 33-49), in the order we
intend to tackle it. This doc is the queue, not the depth — the round-by-round numbers live in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md),
and the architecture/design rationale lives in
[local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md).
Update this doc as items get picked up or finished — move a finished item to "Completed" with a
one-line pointer to the round that shipped it, don't duplicate its details here.

## Active queue (in order)

1. **Build the "anchored independence" candidate**: once an exact mechanism (e.g. `SubtypeArithBox`)
   computes a joint count for some leaf subset, and other, residual leaves remain in the same `And`,
   multiply that exact count by the residual leaves' own combined independent solo-selectivity to get
   a tighter `Estimate`-class candidate for the FULL query — `.min()`-fold it alongside the exact bound
   (never replacing it, so correctness can't regress: a solo rate is always ≤1, so the product can only
   tighten). Validated on real data during Round 48's review: `t:elf cmc>=5` alone gives the identical
   241 as the full `t:elf cmc>=5 usd<10` query (confirming the box's count is price-blind); combining
   241 with `usd<10`'s own solo rate (76189/97812 ≈ 0.779) gives ≈188 against true 177 — tighter than
   the box's own 241 (1.36x → 1.06x). Distinct from the now-completed "loosen `covered`" fix (Round 49):
   this doesn't touch `covered`'s semantics at all, it's a new candidate computed inline wherever an
   exact mechanism resolves its own hit. Needs the same guards as any independence-style estimate:
   combine ALL residual leaves into one product (never try them separately and pick the smallest — the
   same order-statistics selection bias flagged in the design doc), and the same price-triple-correlation
   guard already documented in
   [local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)
   (never independence-combine two of `price_usd`/`price_eur`/`price_tix` together).
2. **Fix the estimators Round 46's census flagged.** Zero of the six exact mechanisms produce an
   inconsistent `cards<=artworks<=printings` triple, but `arith_tuple_count` folds as
   `Candidate::Estimate` (a scaled, not exact, printing conversion, no artwork at all) and is
   structurally invisible to the assert. It already has the real matching card IDs via
   `arith_tuple_ids` (built for the `ArithIdProbe` merge) — an exact printing/artwork derivation from
   those IDs is buildable, not just a self-consistent scaled one.
3. **Fix the harness's own query-generation nondeterminism.** Found during Round 46: the identical
   `--seed 0` run against the identical corpus produced 9 different generated queries across two
   separate engine loads — a related instance of Round 47's own root cause (Rust `HashMap` iteration
   order leaking into something that should be deterministic), this time in
   `scripts/nway_estimate_truth_survey.py`/its `CorpusVocab` mining, not the engine itself. Lower
   engine-correctness stakes than Round 47 was, but the same "silently pollutes future comparisons"
   risk.
4. **Measure the residual-size distribution for real 5+-leaf queries.** Still unmeasured since before
   this session started. This is the actual answer to "is the general bounded partition search worth
   building at all" — if real residuals rarely exceed 2-3 leaves, the "notice one bad case, build one
   validated mechanism" pattern (6 real gaps closed this way so far: Rounds 34, 40, 42, 44, 45, 48) may
   just *be* the right architecture, not a placeholder for a general one.
5. **Decide on / scope the actual general bounded partition search**, informed by #4's findings and
   built on Round 49's own subset-tracking primitive (`CoveredState`'s `subsets: Vec<u64>`, already
   shipped). Not attempted until the above are in.

## Lower priority, no urgency

- **`SubtypeArithBox`'s own top-N cutoff harmonized to "include all ties.**" It already has a correct
  deterministic tiebreak (unlike the bug Round 47 fixed elsewhere) — converting it to the same
  no-arbitrary-exclusion philosophy is a reasonable style-consistency idea, not a bug fix.
- **Audit `lib.rs:6307`** (query-planning candidate ranking, sorts by `(rank, sort_k)`) for the same
  class of tie-order-affects-outcome property Round 47 fixed in `build_subtype_pair_tables`. Flagged,
  never confirmed either way.
- **The Round 43 "swept trio"** (`legality`/`color`/`identity`×`price` — worse than either component's
  own baseline via `PlanePopcount` plus a plain-min-folded price leaf, not the double-independence
  question Round 44 fixed) and the two smaller, less-confirmed stars (`identity+pow+set`,
  `cmc+type+usd`). Real, small, not urgent absent evidence they matter for real routing regret.

## Standing principles for anything built here

- **Exact/bound-class candidates need no placement or reservation logic at all** — `.min()`-folding
  any number of them, in any order, over any overlapping subsets, is always sound (Round 42).
- **Estimate-class candidates may only fill a gap no exact mechanism covers for that exact subset,
  never compete by magnitude with one** (Round 40).
- **Multiple estimate-class candidates for the identical target must never be selected by magnitude**
  — picking the smallest of several noisy estimates of one quantity is a real, systematic
  undercounting bias (order-statistics selection bias), not mere looseness. See
  [local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md)'s
  own point 4 under "Three things naive strategies get wrong" for the full argument.

## Completed

- Round 42: `SubtypePairIndexes` generalized past its `v.len() == 2` gate.
- Round 44: exact `(colors|identity) x cmc` table, closing the confirmed-bad independence star.
- Round 45: `set:X`'s own card/artwork floor populated.
- Round 46: `fold_candidate`/`Candidate` structural refactor + the `debug_assert` census.
- Round 47: deterministic top-N (include-all-ties) for `build_subtype_pair_tables`.
- Round 48: `SubtypeArithBox` generalized past its whole-query-shape gate to scan the residual.
- Round 49: `covered` loosened from leaf-occupancy to subset-identity tracking (`CoveredState`) for the
  independence registry — recovers Round 48's own regression and improves the sweep overall.
