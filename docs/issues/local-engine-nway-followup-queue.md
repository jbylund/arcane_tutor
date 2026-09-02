# N-Way Estimator Follow-Up Queue

Tracks what's left from the `And`-arm cardinality-estimation arc (Rounds 33-47), in the order we
intend to tackle it. This doc is the queue, not the depth — the round-by-round numbers live in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md),
and the architecture/design rationale lives in
[local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md).
Update this doc as items get picked up or finished — move a finished item to "Completed" with a
one-line pointer to the round that shipped it, don't duplicate its details here.

## Active queue (in order)

1. **Generalize `SubtypeArithBox`'s residual scan.** Its shape gate (`arith_children.len() + 1 ==
   v.len()`) still requires the WHOLE query to be exactly "one subtype leaf + N arith leaves," the
   same restriction Round 42 already removed for `SubtypePairIndexes`. Confirmed live: `t:elf cmc>=5
   usd<10` gets zero benefit from this mechanism today. Round 46 already migrated it onto the shared
   `scan_two_bucket_exact` helper with the old gate preserved — relaxing the gate to scan the residual
   is now close to mechanical, with two prior rounds of direct precedent for exactly this move.
2. **Fix the estimators Round 46's census flagged.** Zero of the six exact mechanisms produce an
   inconsistent `cards<=artworks<=printings` triple, but `arith_tuple_count` folds as
   `Candidate::Estimate` (a scaled, not exact, printing conversion, no artwork at all) and is
   structurally invisible to the assert. It already has the real matching card IDs via
   `arith_tuple_ids` (built for the `ArithIdProbe` merge) — an exact printing/artwork derivation from
   those IDs is buildable, not just a self-consistent scaled one.
3. **Loosen `covered` for the independence registry — subset-identity based, not leaf-occupancy
   based.** Today, once leaf `A` is covered by *any* exact pairing (say `(A,B)`), it's permanently
   unavailable to independence for *any other* partner (e.g. `(A,C)`) — even though that's a
   genuinely different, safe-to-try subset. The only real danger is an estimate competing against an
   exact answer for the *identical* subset (Round 40's class-priority finding); a leaf merely being
   used elsewhere isn't. The fix narrows the check from "has this leaf been touched by anything" to
   "has this exact pair already been exactly answered" — real infrastructure, not just an accuracy
   tweak, since it's the same subset-tracking primitive a general search would need regardless of
   whether that search is ever built as a generic loop or stays a fixed sequence of mechanisms.
4. **Fix the harness's own query-generation nondeterminism.** Found during Round 46: the identical
   `--seed 0` run against the identical corpus produced 9 different generated queries across two
   separate engine loads — a related instance of Round 47's own root cause (Rust `HashMap` iteration
   order leaking into something that should be deterministic), this time in
   `scripts/nway_estimate_truth_survey.py`/its `CorpusVocab` mining, not the engine itself. Lower
   engine-correctness stakes than Round 47 was, but the same "silently pollutes future comparisons"
   risk.
5. **Measure the residual-size distribution for real 5+-leaf queries.** Still unmeasured since before
   this session started. This is the actual answer to "is the general bounded partition search worth
   building at all" — if real residuals rarely exceed 2-3 leaves, the "notice one bad case, build one
   validated mechanism" pattern (5 real gaps closed this way so far: Rounds 34, 40, 42, 44, 45) may
   just *be* the right architecture, not a placeholder for a general one.
6. **Decide on / scope the actual general bounded partition search**, informed by #5's findings and
   built on #3's subset-tracking primitive. Not attempted until the above are in.

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
