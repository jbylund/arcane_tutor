# N-Way Estimator Follow-Up Queue

Tracks what's left from the `And`-arm cardinality-estimation arc (Rounds 33-57), in the order we
intend to tackle it. This doc is the queue, not the depth — the round-by-round numbers live in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md),
and the architecture/design rationale lives in
[local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md).
Update this doc as items get picked up or finished — move a finished item to "Completed" with a
one-line pointer to the round that shipped it, don't duplicate its details here.

## Active queue (in order)

1. **Calibrated balls-into-bins occupancy: derive card/artwork from an EXACT printing count.** The
   agreed next round. Round 57 made `P` exact for `legality x released` and, in doing so, EXPOSED the
   `* n_cards / n_printings` global-ratio idiom's under-bias instead of accidentally masking it: 173
   card/artwork rows regressed on ratio (zero in printing space), e.g. `date:2019-11-07 f:gladiator`
   [card] 927 -> 468 against a true 840, where the printing answer is now exactly 840. Occupancy
   (`n(1 - (1 - 1/n)^P)` with a fitted effective-bin multiplier) gives **813-829 (0.97-0.99x)** on that
   same query and is insensitive to the constant across k=0.40-1.00. Measured over 400 sampled date
   ranges: cards mean |log| **0.188** (median 1.089x, p90 1.498x), artworks **0.119** (median 1.063x,
   p90 1.294x) — against the naive scale's 0.764 and its systematic **0.474x median undershoot**, the
   dangerous direction.
   - **Not legality-specific, and that is the point.** It converts any exact printing count into
     card/artwork estimates, so it targets every consumer of that idiom — which makes it the
     higher-leverage half of item #2 below.
   - **Two blockers to settle in the plan.** `Candidate::Estimate` carries only `printing`, so
     delivering estimated card/artwork values needs a structural decision. And the calibration sampled
     date ranges uniformly over date INDICES, which is not the real query-shape mix (`year:Y` interior
     ranges are common), so `k` must be refit against a realistic width distribution or it is tuned to
     the wrong population — the population-parity trap
     `.claude/rules/benchmark-methodology-review.md` warns about.
2. **Backport the `rest_max` triple + space-native independence to `SetSubtypeTable` /
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
3. **Generalize "anchored independence" further.** Rounds 50 and 56 shipped two anchors
   (`SubtypeArithBox`, `ColorCmcTable`), both with a single residual `IndepClass::Price` leaf, sharing
   one `anchored_price_residual` helper. Three directions remain, each its own future round (validate
   independently, don't bundle):
   - **More residual classes.** Only `Price` has a validated real-data example; other classes
     (`ColorId`, `Cmc`, `Type`, etc., wherever the anchor's own residual isn't itself the anchored
     dimension) need their own before/after check before being added, mirroring how
     `independence_safe_pair`'s own registry grew one validated class at a time (Round 38 → Round 40).
   - **`SubtypePairIndexes` as a third anchor** — the one remaining candidate named in the original
     item, still without a validated example. Adding it is now mostly wiring, since Round 56 hoisted
     the shared helper both existing anchors call.
   - **Combining multiple safe residual classes into one product**, not just one — needs the same
     order-statistics-bias care already documented in the design doc (never try residuals separately
     and pick the smallest) once 2+ classes are each independently validated as safe to anchor.
   - Also cheap and already measured: Round 56's `any_price_source` precheck (skip the anchor loop
     entirely when no `Price`-classified source exists anywhere, worth ~21% of `and_estimate_ns` on
     `(color, cmc)`-with-no-price queries) was deliberately NOT applied to Round 50's own site, which
     measured unregressed as-is. The same guard would help it too.
4. **Measure the residual-size distribution for real 5+-leaf queries.** Still unmeasured since before
   this session started. This is the actual answer to "is the general bounded partition search worth
   building at all" — if real residuals rarely exceed 2-3 leaves, the "notice one bad case, build one
   validated mechanism" pattern (8 real gaps closed this way so far: Rounds 34, 40, 42, 44, 45, 48, 51,
   52) may just *be* the right architecture, not a placeholder for a general one.
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
  question Round 44 fixed). The two smaller stars once listed here are now resolved rather than
  pending: `cmc+type+usd` is partly addressed by Round 56 (its `*+cmc+usd` sibling shapes improved),
  and `identity+pow+set` is explicitly **not worth chasing** — measured post-Round-55 as the survey's
  worst shape by median ratio (1.08 abs-log, 17-34x on individual queries) while contributing ZERO
  routing-relevant rows, since its absolute errors are 30-100 against a 1,024 boundary. Kept here only
  so the ratio tables don't re-nominate it.
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

- **The `Legality` leaf's own solo printing estimate undershoots by 5-13%**, and `min()` only lowers,
  so an exactly-correct joint can lose the fold to it. Measured on Round 57's target shape: of 300
  printing rows, 29 get the exact value from `LegalityDateTotals` and still lose, because the leaf's
  `legal_cards * n_printings / n_cards` is smaller than the truth. All are 35k-53k, far above the 1,024
  boundary, so routing is unaffected today — but it is the same idiom item #1 targets, and fixing that
  would also unblock these.
- **The query sampler never generates `banned:`/`restricted:`.** `client/query_sampler.py` hardcodes
  the legality family to the `f:` operator (line ~246) and builds its vocabulary only from formats whose
  status is `legal` (line ~591), so **no survey in this arc has ever exercised those queries** — despite
  the engine handling them correctly (`banned:modern` returns exactly 403, matching the corpus) and the
  corpus holding 7,066 such rows. Any pruning argument about banned/restricted (including Round 57's
  selectivity floor) therefore rests on population size, not on measured routing impact. Worth teaching
  the sampler before more legality estimator work.
- **`not_legal` legality keys are unreachable by construction.** `filter.rs`'s binding maps only
  `f`/`format`/`legal` -> `LEGALITY_LEGAL`, `banned` -> `LEGALITY_BANNED`, `restricted` ->
  `LEGALITY_RESTRICTED`; negation is a `Not` wrapper, not a `not_legal` status. Round 57 hit this twice
  (18 above-floor `not_legal` keys, plus 9 phantom keys from unassigned format slots reading `not_legal`
  for every printing). Remember it before adding any other `legality x X` table.

## Standing principles for anything built here

- **Exact/bound-class candidates need no placement or reservation logic at all** — `.min()`-folding
  any number of them, in any order, over any overlapping subsets, is always sound (Round 42).
- **Estimate-class candidates may only fill a gap no exact mechanism covers for that exact subset,
  never compete by magnitude with one** (Round 40).
- **Rank candidate work by ABSOLUTE error that crosses a decision boundary, never by ratio.** An
  estimate can be 34x over and completely harmless. Of 40,371 `root=and` survey rows, only **2.5%**
  can flip a plan choice at all (straddle `STREAM_MIN_MATCHES` = 1,024 with >=200 absolute AND >=10%
  relative error), and **83% of those are over-estimates**. `star:identity+pow+set` reads as the
  survey's worst shape by median abs-log-ratio (1.08; individual queries 17-34x over) and contributes
  **zero** such rows, because its absolute errors are 30-100. This is the same principle the engine
  already encodes in `PAIR_MIN_PRINTINGS`/`STREAM_MIN_MATCHES` ("worth pairing only if broad enough
  that an estimate about it can change a routing decision"); apply it when CHOOSING a target, not just
  when building one. It picked Round 56's target and correctly vetoed the shape the ratio tables
  ranked first.
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
- Round 56: anchored independence for `ColorCmcTable` (second anchor after Round 50's), sharing one
  hoisted `anchored_price_residual` helper. Routing-relevant misses 1,016 -> 880 (-13.4%), over-side
  -141 / under-side +5, all 146 plan flips in the intended direction, monotone (0 of 1,229 changed
  predictions increased). Fudge factor swept and REJECTED on real data — see the ledger's Round 56
  section before proposing one again.
- Round 57: `LegalityDateTotals`, an exact per-`(format, status)` prefix sum over the `released_at`
  axis — closes `unsafe:legality+released` (0/900 -> 813/900 coverage; printing median 1.02x -> 1.00x,
  p90 3.64x -> 1.00x, max 16.81x -> 1.00x) at +148.8 KB. Retracted the design doc's own "legality is
  date-DEFINED" justification for the registry exclusion, and surfaced the card/artwork under-bias that
  is now item #1.
- Harness fix (no round number, a Python-only fix outside the engine): `client/query_sampler.py`'s
  `_count_row` folded oracle/flavor words via `Counter.update(set(...))` — bare-set iteration is
  hash-seed-randomized per process, so tied-frequency co-occurring words could swap `most_common()`'s
  tie-break across runs. Fixed with `sorted(set(...))`; verified byte-identical output across 5 fresh
  process runs (was 20-32 line diffs before), plus a subprocess-based regression test that fails on the
  pre-fix code and passes after.
