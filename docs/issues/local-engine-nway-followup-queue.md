# N-Way Estimator Follow-Up Queue

Tracks what's left from the `And`-arm cardinality-estimation arc (Rounds 33-55), in the order we
intend to tackle it. This doc is the queue, not the depth — the round-by-round numbers live in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md),
and the architecture/design rationale lives in
[local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md).
Update this doc as items get picked up or finished — move a finished item to "Completed" with a
one-line pointer to the round that shipped it, don't duplicate its details here.

## Active queue (in order)

1. **Backport the `rest_max` triple + space-native independence to `SetSubtypeTable` /
   `ColorSubtypeTable`.** Round 55 shipped both ideas for the new `(subtype, subtype)` table but
   deliberately left these three untouched, so they still rank their top-256 by CARD count alone and
   still scale one card-space `rest_max` into printing space by a global reprint ratio. Round 55's own
   measurement says what that costs: printing-space-native independence+cap beat
   card-space-×-global-ratio at every percentile on the same excluded population (median 0.42x vs
   0.64x, p90 3.27x vs 4.45x, max 21x vs 24.67x). `top_n_union_and_rest_max` already exists and is
   generic over `K` — this is mostly a matter of switching the three `top_n_and_rest_max` call sites
   and teaching `SubtypePairEstimate` to read the triple natively instead of scaling. Watch the
   ordering constraint Round 55 surfaced (the fourth standing principle below):
   `SubtypePairEstimate` is already positioned after its own exact scan, but re-check rather than
   assume.
2. **Generalize "anchored independence" further.** Round 50 shipped this deliberately narrow: only
   `SubtypeArithBox`'s own hit, only a single residual `IndepClass::Price` leaf. Three separate
   directions remain, each its own future round (validate independently, don't bundle):
   - **More residual classes.** Only `Price` has a validated real-data example; other classes
     (`ColorId`, `Cmc`, `Type`, etc., wherever `SubtypeArithBox`'s own residual isn't itself the arith
     dimension) need their own before/after check before being added, mirroring how
     `independence_safe_pair`'s own registry grew one validated class at a time (Round 38 → Round 40).
   - **More anchor mechanisms.** `SubtypePairIndexes`/`ColorCmcTable`'s own exact hits are the same
     shape (an exact joint, blind to whatever residual leaves remain) and would plausibly benefit the
     same way — not attempted, no validated example yet for either.
   - **Combining multiple safe residual classes into one product**, not just one — needs the same
     order-statistics-bias care already documented in the design doc (never try residuals separately
     and pick the smallest) once 2+ classes are each independently validated as safe to anchor.
3. **Measure the residual-size distribution for real 5+-leaf queries.** Still unmeasured since before
   this session started. This is the actual answer to "is the general bounded partition search worth
   building at all" — if real residuals rarely exceed 2-3 leaves, the "notice one bad case, build one
   validated mechanism" pattern (8 real gaps closed this way so far: Rounds 34, 40, 42, 44, 45, 48, 51,
   52) may just *be* the right architecture, not a placeholder for a general one.
4. **Decide on / scope the actual general bounded partition search**, informed by #3's findings and
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
- **`PriceJointTable`'s own boundary interpolation.** Shipped "any overlap counts fully" (no
  interpolation within a partially-overlapping bucket) for all three pairs now — already validated to
  1.00-1.92x on the worst real tail queries checked, so this is a refinement, not a bug. A real,
  measured cost to weigh against it: the tables are already genuinely non-cheap linear scans (64-92%
  cell density depending on the pair, not "far dozen" the way `ColorCmp`'s own much-smaller-scale
  precedent implied) — interpolation would add per-cell work on top of that, not shrink the tables.
- **The 3-way `usd+eur+tix` case.** Explicitly out of scope for both Rounds 53 and 54 — still falls
  through to the plain per-leaf min-fold. Would need a real 3D histogram (far more cells) with no
  validated evidence yet that it's needed beyond what the three pairwise joints already capture for a
  query combining all three. Worth checking directly against the real corpus before building it.
- **Extend the joint-histogram-over-linear-correlation pattern to other dimension pairs**, if any are
  found — no other (non-price) pair has been checked for a similarly strong, exploitable relationship;
  not assumed to exist, not investigated. Also worth re-applying the Round 54 lesson generally: a low
  Pearson r does NOT rule out a real, non-linear, exploitable relationship — a direct joint-histogram
  simulation is the right way to check, not correlation alone.
- **Router picks `PrintingCompose` over the cheaper `GatheredScan` when the predicted total is exactly
  0.** Found while dispatch-pricing (`costbench.plan_self_ns`, the same definition
  `bench_regret_matrix.py` uses) Round 55's own 79 distinct plan-choice flips: `StreamedSelect ->
  GatheredScan` (22.8% of flips) and `PrintingCompose -> StreamedSelect` (2.5%) are clear, large wins
  (median +12.08µs and +10.81µs respectively, both directly dispatch-priced in the same
  `explain_analyze` call). But the single LARGEST bucket, `GatheredScan -> PrintingCompose` (45.6% of
  flips, 36/36 disjoint-subtype-pair queries with `true_total=0`), is a small, consistent REGRESSION:
  median −0.92µs (0.63µs -> 1.58µs), with `GatheredScan` measured as the actual best plan in all 36
  sampled rows. Round 55 made the estimate for these EXACT (a genuine table hit returns
  `SpaceTotals{0,0,0}` for a real disjoint pair, was ~66-184 before) — so this is a router
  mis-ranking exposed by a now-correct estimate, not an estimator bug. Low urgency: the absolute
  magnitude (sub-2µs either way) sits at/near `costbench.py`'s own declared noise floor
  (`NOISE_FLOOR_US = 1.0`), though the 100%-consistent direction across 36 independent queries says
  it's real, not noise. (The remaining 29.1% of flips, `PrintingCompose -> GatheredScan`, could not be
  directly dispatch-priced — `PrintingCompose` no longer runs at all under the corrected estimate, so
  `explain_analyze` never forces a trial for it — but indirect evidence, `GatheredScan` beating
  `StreamedSelect` 3-6x in every one of those rows, points toward a win there too, not measured.)

## Standing principles for anything built here

- **Exact/bound-class candidates need no placement or reservation logic at all** — `.min()`-folding
  any number of them, in any order, over any overlapping subsets, is always sound (Round 42).
- **Estimate-class candidates may only fill a gap no exact mechanism covers for that exact subset,
  never compete by magnitude with one** (Round 40).
- **An estimate-class mechanism must be POSITIONED after every exact mechanism whose leaves it could
  compete for** — not merely made to respect `covered`, which only ever reflects what already ran.
  Round 55 demonstrated this concretely: its fallback, placed (per its own plan) before
  `SubtypeArithBox`, let an undershot independence guess win the arm's min-fold outright over an
  available, tighter-but-larger exact box hit on the same leaves, breaking two pre-existing tests.
  `fold_candidate`'s min-fold is commutative in principle, but an undershooting estimate permanently
  pulls `result` below the truth and no later exact candidate can raise it back. Exact-class
  mechanisms have no such constraint (first principle above).
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
- Round 50: "anchored independence" for `SubtypeArithBox` — exact joint × single residual `Price` rate,
  narrowly scoped (see item #2 above for what's left to generalize).
- Round 51: exact `arith_tuple` (printing, card, artwork) triples, precomputed at build time
  (`ArithTupleIndex.totals`) — closes Round 46's census gap; surfaced the `unique=artwork` acquire-path
  gap, closed by Round 52.
- Round 52: `est.result.card`/`.artwork` wired into `unique=card`/`unique=artwork`'s own acquire path as
  an additional `.min()` tightening (never a replacement for the calibrated baseline — a real 170x
  regression in the first attempt was caught by the corpus sweep before shipping and is now a dedicated
  regression test). Closes Round 51's own `unique=artwork` gap.
- Round 53: `PriceJointTable`, a quantile-bucketed 2D `(usd, eur)` joint — closes the worst-performing
  shape found in a fresh full-corpus survey (`unsafe:usd+eur`, was up to 185x over, now 1.01-1.24x on
  the worst real tail queries). `tix` deliberately untouched (r=0.336, weak correlation). A real,
  measured redundant-computation inefficiency the implementing agent found was fixed before merging,
  not deferred — see the ledger's own "Round 53" section.
- Round 54: generalized `PriceJointTable` to `usd`×`tix`/`eur`×`tix` too — closes the NEXT-worst shapes
  a fresh survey surfaced once Round 53 stopped dominating it (42-87x down to 1.00-1.92x), despite both
  pairs' own weak linear correlation (Pearson r doesn't rule out a real non-linear relationship a joint
  histogram still captures). 3-way `usd+eur+tix` remains out of scope — see the queue's own item above.
- Round 55: `(subtype, subtype)` exact top-256 table (`SubtypePairTable`) + a printing-space-native
  capped-independence fallback — closes `same_family:type+type_realistic`/`_disjoint`'s 0% mechanism
  coverage (100% after; `t:cleric t:spirit` 628 vs true 19 → exact in all three spaces). First use of
  the union-of-3-spaces top-N cutoff and a real per-space `rest_max` triple (item #1 above is the
  backport of both to the three older tables). Surfaced the estimate-placement ordering constraint now
  recorded as the fourth standing principle above.
- Harness fix (no round number, a Python-only fix outside the engine): `client/query_sampler.py`'s
  `_count_row` folded oracle/flavor words via `Counter.update(set(...))` — bare-set iteration is
  hash-seed-randomized per process, so tied-frequency co-occurring words could swap `most_common()`'s
  tie-break across runs. Fixed with `sorted(set(...))`; verified byte-identical output across 5 fresh
  process runs (was 20-32 line diffs before), plus a subprocess-based regression test that fails on the
  pre-fix code and passes after.
