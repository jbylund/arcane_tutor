# N-Way Estimator Follow-Up Queue

Tracks what's left from the `And`-arm cardinality-estimation arc (Rounds 33-60), in the order we
intend to tackle it. This doc is the queue, not the depth — the round-by-round numbers live in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md),
and the architecture/design rationale lives in
[local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md).
Update this doc as items get picked up or finished — move a finished item to "Completed" with a
one-line pointer to the round that shipped it, don't duplicate its details here.

## Active queue (in order)

1. **Stop the two REMAINING reprint-ratio leaf arms undershooting.** Round 61 did the `Legality` arm
   (0.647-1.040x over all 23 formats; 14 of 14 outvoted rows recovered — see Completed below). Two of
   the three arms Round 59 demoted are still guessing `card_count * n_printings / n_cards`:
   - **Broadcast/devotion** (`is_broadcast_leaf_shape`). Its card popcount is a real `eval_planes`
     pass; devotion is synthesized from mana cost, so `ValueTotals` has no column for it and there may
     be no cheap exact counterpart. Measure the error before scoping — the `Legality` arm's own error
     turned out to be exactly `corpus_depth / that value's depth`, and the same identity applies here,
     so a devotion bucket's reprint depth against the corpus's is the whole answer.
   - **Bare cmc/power/toughness** (`bare_numeric_field_count`). The highest-traffic of the three. Its
     defensive `arith_tuple_totals` fallback ALREADY carries an exact printing/artwork triple; the
     question is whether the primary sorted-index path can reach the same numbers as cheaply.
   - Reuse Round 61's method, both halves of it: read the leaf's own node out of `explain`'s
     `and_trace` tree (a bare leaf routes past the arm entirely and reads exact on any build — see the
     ledger's Round 61 section), and split the timing by a control subset of queries the change cannot
     touch, not by a same-binary canary.
   - `scripts/check_bound_class_soundness.py` should stay green throughout.
2. **Anchor `Independence` and the `Or` arm now that their legality input is exact.** Round 61's only
   regressions were structural and predictable: `Independence`'s `round(a * b / n)` already over-predicts
   on correlated pairs and a too-small `a` had been cancelling part of that, so `star:legality+*+usd`
   worsened (+0.009 to +0.023 mean abs-log-ratio) and every newly-broken straddle is one of those rows
   crossing 1,024 upward. The `Or` arm's `add` has the same shape one level up (`OR:legality+*`, +0.008
   to +0.015). This is Round 56's finding recurring: an estimate-class combiner whose inputs became
   exact needs its own anchor. **Do not reach for a fudge factor** — Round 56 swept one on real data and
   rejected it.
3. **Replace the three presence/equality proxies with explicit structural signals.** Each reads a
   VALUE to answer a STRUCTURAL question, which is the same anti-pattern in three places:
   - `est.candidate.printing() == est.result.printing()` — "did any mechanism tighten this?" `result <=
     candidate` always holds, so inequality does imply tightening, but equality conflates "nothing
     tightened" with "tightened to coincidentally the same number". Round 58 made it worse unnoticed:
     `printing()` is `best()`, so it now compares two min-across-channel collapses.
   - `est.result.card.best().is_some()` (~17155) — "did an `And` produce a card number?"
   - `est.result.card.best() == Some(domain_cards)` (~16915) — "is the domain exact?"
   All three are answerable directly: `covered` already tracks what tightened, and the trace records
   which mechanisms hit. **They must be fixed together and before domain-seeding**, which makes all
   three vacuous. Touches plan-cost features, so it needs the pairwise-ordering and feature-accuracy
   guards, not just the estimate survey.
4. **Seed every `SpaceEstimate` with the domain instead of `UNKNOWN`.** The domain size is a true upper
   bound, so a space can start `{ guaranteed: n_cards, estimate: n_cards }` and only ever tighten. That
   deletes every `Option`, makes `printing()` infallible by construction rather than by `expect`, and
   removes the "absence means unknown, never zero" footgun that caused BOTH laundering bugs found so far
   (Round 59's `And` seed, and `narrow_floor`'s still-live read). Round 60 measured how normal absence
   currently is: **41,838 of 147,660** tree nodes have `printing_guaranteed` absent while `printing` is
   present. Blocked on #3.
5. **Untangle `narrow_floor`.** It reads `s.card.best()` and writes `result_space.card.lower_guaranteed(f)`
   — a child's GUESS becoming the query's BOUND, the same laundering Round 59 fixed in the `And` seed.
   Latent today (nothing writes a card-space estimate-only value yet); item #1 could unmask it. It is
   also doing two jobs: its stated purpose is to give card/artwork the free per-leaf min-fold printing
   already has, but its breadth filter is justified by what `narrow_rec` will actually narrow to — a
   plan-cost concern, not an answer-cardinality one. Mathematically a broad leaf's count IS a sound
   bound (`|A n B| <= |A|`), so the filter makes it deliberately weaker than the tightest sound bound,
   for a reason belonging to a different question. It also computes a `min` (an upper bound) while being
   named a floor. Round 60 left a candidate set — **4,317 root nodes** with `card_guaranteed` tighter
   than any child's — but that set also contains legitimate `Candidate::Exact` joints, so separating
   them is the round's actual work. Easiest after #4, when bounds are always present.
6. **Backport the `rest_max` triple + space-native independence to `SetSubtypeTable` /
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
7. **Generalize "anchored independence" further** (item #2 is one concrete instance of this, promoted
   ahead of the general work because Round 61 created it). Rounds 50 and 56 shipped two anchors
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
8. **Measure the residual-size distribution for real 5+-leaf queries.** Still unmeasured since before
   this session started. This is the actual answer to "is the general bounded partition search worth
   building at all" — if real residuals rarely exceed 2-3 leaves, the "notice one bad case, build one
   validated mechanism" pattern (8 real gaps closed this way so far: Rounds 34, 40, 42, 44, 45, 48, 51,
   52) may just *be* the right architecture, not a placeholder for a general one.
9. **Decide on / scope the actual general bounded partition search**, informed by #8's findings and
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

- ~~**The `Legality` leaf's own solo printing estimate undershoots**~~ — **fixed by Round 61.** The
  recorded error was "5-13%"; the full 23-format measurement was 0.647-1.040x, and `banned:`/
  `restricted:` were 0.40x/0.24x. The rows this bullet describes (an exact `LegalityDateTotals` value
  losing the `.min()` fold) are gone: 14 of 14 recovered at seed 0, 13 of 13 at seed 61, 0 newly
  outvoted. Kept here, struck through rather than deleted, because the underlying idiom survives in the
  two sibling leaf arms — active item #1.
- **The query sampler never generates `banned:`/`restricted:`.** `client/query_sampler.py` hardcodes
  the legality family to the `f:` operator (line ~246) and builds its vocabulary only from formats whose
  status is `legal` (line ~591), so **no survey in this arc has ever exercised those queries** — despite
  the engine handling them correctly (`banned:modern` returns exactly 403, matching the corpus) and the
  corpus holding 7,066 such rows. Any pruning argument about banned/restricted (including Round 57's
  selectivity floor) therefore rests on population size, not on measured routing impact. Worth teaching
  the sampler before more legality estimator work. Round 61 is a live example of the gap: those two
  statuses had the WORST leaf error of any legality query (0.40x / 0.24x, against 0.647x for the worst
  `f:`) and no survey row would ever have shown it — it took a hand-written spot check and a dedicated
  Rust test.
- **`not_legal` legality keys are unreachable by construction.** `filter.rs`'s binding maps only
  `f`/`format`/`legal` -> `LEGALITY_LEGAL`, `banned` -> `LEGALITY_BANNED`, `restricted` ->
  `LEGALITY_RESTRICTED`; negation is a `Not` wrapper, not a `not_legal` status. Round 57 hit this twice
  (18 above-floor `not_legal` keys, plus 9 phantom keys from unassigned format slots reading `not_legal`
  for every printing). Remember it before adding any other `legality x X` table.

- **Card-space independence for `legality x released`** — the replacement for Round 58's rejected
  occupancy idea, still unvalidated. `date_cards x legal_cards / n_cards`, using `RangeCardCounts`'
  exact distinct-CARD count for the window. Hand-checked at both reprint-depth extremes it points the
  right way where occupancy structurally cannot (`f:alchemy year<2011` needs ~139 of 11,250
  window-cards; `date:2019-11-07 f:gladiator` needs ~840 of ~927 — a legal-card fraction of ~0.012 vs
  ~0.9 that independence supplies and occupancy cannot). But Round 57 rejected independence for this
  pair in PRINTING space on 250x per-format density skew, so card space needs its own validated round.
  Artwork's 62 regressed rows are a SEPARATE estimator: `artwork_estimate`'s `capacity_cards` uses the
  uncalibrated `balls_into_bins`, so there is no divisor there to skip.
- **Do not re-propose skipping `COMPOSE_CARD_ESTIMATE_BIAS` for an exact `k`.** Measured and rejected in
  Round 58 (22 rows recovered against 163 newly regressed; a narrowed single-date variant was 7-for-7
  with worse absolute error). The constant corrects printing->card CLUSTERING, not `k`'s accuracy, and
  the two are independent — skipping it asserts the answer set's reprint depth is 1.0, which is false
  for all but the narrowest windows. See the ledger's Round 58 section for the depth table.
- **Deduplicate `exact_domain_{cards,artworks}` against `guaranteed.{card,artwork}`** — Round 58 found
  they are provably the same computation (both min-over-`Exact`-candidates, nothing else touches
  either), while `exact_domain_printing` genuinely differs (`guaranteed.printing` is seeded from the
  leaf fold). Safe today; left visible because a future divergence would be silent.
- **`ExactDomain.artwork` carries a new `#[allow(dead_code)]`** (Round 58). Its two consumers read only
  `.card`/`.printing`; sharing `SpaceEstimate` had been masking that. Drop the field or find its
  consumer.

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
- **A number derived from `best()` may NEVER be written to `guaranteed`.** `best()` is
  `min(guaranteed, estimate)` and can resolve from the estimate channel, so wrapping it in
  `SpaceMeasure::known()` launders a guess into the proven-bound channel. Round 59 found exactly this in
  the `And` arm's own seed, where it made the root's `guaranteed` read 36 against a true 100. Recorded on
  `SpaceMeasure` itself. Corollary: `printing.guaranteed.is_some()` is NOT an invariant (an `Or` of two
  estimate-only leaves leaves it absent); `printing.best().is_some()` is.
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
  narrowly scoped (see item #7 above for what's left to generalize).
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
  the union-of-3-spaces top-N cutoff and a real per-space `rest_max` triple (item #6 above is the
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
  date-DEFINED" justification for the registry exclusion, and surfaced both the card/artwork under-bias
  and the exactly-right-but-outvoted rows that Rounds 59-61 chased (Round 61 closed the latter).
- Round 58 (phase 1 only): `SpaceMeasure { guaranteed, estimate }` per space, so a proven bound and a
  best guess stop competing for one `.min()` slot. Byte-identical at three independent seeds. Phase 2
  (the `COMPOSE_CARD_ESTIMATE_BIAS` skip) was measured to fail and deliberately not taken — preserved
  unmerged on `r58-phase2-measured-bad` (`e1d4fba7`, `e5f75f45`). Makes the five workarounds listed in
  the ledger's Round 58 section retirable; Round 61 retired the first of them.
- Round 59: `guaranteed` made honest — three leaf arms demoted to estimate-only, `LegalityDateTotals`
  and `PriceJointTable` promoted via a new `Candidate::PrintingBound`, and the `And` arm's seed fixed so
  it no longer launders a `best()`-derived number into `guaranteed`. Byte-identical at two independent
  seeds. Shipped `scripts/check_bound_class_soundness.py` (a standing check that no bound-class
  mechanism predicts below truth) and finally fixed the `ARITH_TUPLE_BLOWUP_CARDS` release-clippy error
  — **both clippy profiles are clean for the first time in this arc**. Recovered 0 rows by design;
  Round 61 shipped the fix it identified.
- Round 60: `and_trace` reports both channels — `SpaceEstimate` embedded in every trace struct,
  channels derived once at the fold from `Candidate::spaces()`, Python keys flattened and strictly
  additive. Behaviour-neutral (15 semantic fields x 54,279 rows, 0 differing; 673,776 space-slots, 0
  fidelity violations). Made `check_bound_class_soundness.py` read the engine's own channels with its
  name map kept as a cross-check. Costs ~12% on the DIAGNOSTIC path only (six extra `PyDict::set_item`
  per dict, isolated by a probe); production untouched.
- Round 61: the `Legality` leaf reports `ValueTotals::legality`'s exact printing count instead of the
  reprint-ratio guess (0.647-1.040x over all 23 formats, 16 under truth; `banned:`/`restricted:` worse
  still at 0.40x/0.24x). Recovers **14 of 14** exactly-right-but-outvoted `unsafe:legality+released`
  rows at seed 0 (13 of 13 at seed 61) — the rows Round 59 could not reach. One shared
  `legality_space_totals` lookup answers printings and artworks together, which makes the arm **5.7%
  faster** than trunk rather than 9.3% slower; the two-call form's cost was found only by a control
  subset, not by the same-binary canary (see the ledger's Round 61 section). Left the two sibling
  reprint-ratio arms (devotion, bare cmc/pow/tou) alone — active item #1 is what remains of them. Its
  only regressions are structural and are now active item #2.
- Harness fix (no round number, a Python-only fix outside the engine): `client/query_sampler.py`'s
  `_count_row` folded oracle/flavor words via `Counter.update(set(...))` — bare-set iteration is
  hash-seed-randomized per process, so tied-frequency co-occurring words could swap `most_common()`'s
  tie-break across runs. Fixed with `sorted(set(...))`; verified byte-identical output across 5 fresh
  process runs (was 20-32 line diffs before), plus a subprocess-based regression test that fails on the
  pre-fix code and passes after.
