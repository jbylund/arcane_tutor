# N-Way Estimator Follow-Up Queue

Tracks what's left from the `And`-arm cardinality-estimation arc (Rounds 33-65), in the order we
intend to tackle it.

**The queue's topic shifted on 2026-09-05, and items 1-2 are a different kind of work from 3-4.** The
arc existed to unblock a joint refit of the COST MODEL, which had been blocked on bad cardinality
estimates. Measured against realized counters, that block is gone: `matches` and `eval_domain` now read
**1.00 at the median on every acquire route**, with a p90/p10 spread of 1.0 on `plane` and `candidates`
— the route carrying 96.31% of real weighted query time. What the refit is now blocked on is a
DIFFERENT quantity: how many printings a plan actually walks. Those features were never a function of
result cardinality, and four rounds of estimator work moved them barely at all (`scan_units` pooled p50
0.67 -> 0.65, spread 12.4 -> 12.1 across Rounds 62-64). Items 1-2 are cost-feature calibration; items
3-4 are the estimator's remaining internal hygiene. This doc is the queue, not the depth — the round-by-round numbers live in
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md),
and the architecture/design rationale lives in
[local-engine-nway-compose-independence-search.md](local-engine-nway-compose-independence-search.md).
Update this doc as items get picked up or finished — move a finished item to "Completed" with a
one-line pointer to the round that shipped it, don't duplicate its details here.

## Active queue (in order)

**Scope note, measured 2026-09-04 — read this before scheduling anything here.** This arm governs
about **2% of real weighted query time**. Over the 14,473-query weighted real-traffic corpus
(`benchmarks/wild-queries/wild-corpus.jsonl`), acquire routes split:

| route | queries | mean | weighted TIME share |
|---|---|---|---|
| `candidates` | 14,338 | 6 us | **96.31%** |
| `printing_compose` | 69 | 29 us | **1.95%** |
| `plane` | 64 | 27 us | 1.69% |

Only **39 of 14,473** real queries reach the `And` arm at all. 71.4% are not conjunctions; another
28.3% ARE conjunctions (4,102 of them) that never reach this arm, because a name/text leaf or an `Or`
makes them non-composable and they route through `candidates` instead. Compose queries are ~5x slower
each, and there are still too few for that to matter.

So: an item here needs a reason beyond "the estimate is inaccurate". A cheap fix with a measured
payoff still clears the bar; a large build does not. Correctness and maintainability arguments are
fine — say so explicitly rather than implying a latency win. Round 64 was the last item whose case
rested on a measured accuracy payoff, and a 2026-09-05 sweep of every estimate-class mechanism found
the whole lot contributes **9 routing-relevant errors in 9,777 survey rows (0.09%)** — see the
anchored-independence bullet below for the table. **There is no known ESTIMATE accuracy headroom left
to chase** — items 3-4 are the estimator's internal hygiene, and items 1-2 are cost-FEATURE calibration,
a different quantity that this scope note's time-share table does not govern (see the header note).

Two caveats. The corpus is one sample (2026-08-02) in which bare name lookups dominate by design, and
a power-user profile skews composable (`f:modern` plus other filters lands squarely in that 0.27%) —
representativeness is the load-bearing assumption of the whole note. And a mean time share misses
TAIL risk: a mis-routed compose query can be pathologically slow (Round 63 hit a 0.2us-priced plan
against a measured 199.3us), which is a cost-model correctness concern that this table cannot see.

**Planned revisit:** estimates and query planning are to be re-examined over the UNIFORM sampler as
well, and that will inform what else gets done here. Treat this ordering as provisional until then.

1. **`compose_scan_printings` needs a SECOND DIMENSION, not a better constant** — investigated
   2026-09-05, and the original framing of this item ("looks like a discrete arithmetic relationship,
   maybe a bug") was wrong. The feature is
   `compose_scan_printings = printing_matches * COMPOSE_GATHER_SPAN_PER_MATCH`, and that constant is
   **1.47** — so the 1.47 seen at p50 and p70 in `bench_feature_accuracy` is the constant showing
   through, not a coincidence. Not a bug. The real finding is what the constant is standing in for.
   - **CORRECTED 2026-09-05 — an earlier version of this item reported a "realized value of the
     constant" and that measurement divided by the wrong quantity.** `feats.matches`, which is what
     `acquire["matches"]` reports, is `result_total` — the MODE-APPROPRIATE estimate. The feature uses
     `printing_matches` (printing space, whatever the mode). So `printings_examined / acquire["matches"]`
     in card mode is *printings examined per matching CARD*, not the constant's own realized value, and
     the apparent bullseye (median 1.48 against a declared 1.47) was a coincidence between two
     different quantities. `printing_matches` is not exposed, so the constant's centring is UNMEASURED
     and needs the instrumented build below.
   - **What that measurement does establish**, over Gather-paging `PrintingCompose` rows — printings
     examined per matching card/artwork, which the cost model does need and which no current feature
     carries:

     | unique | n | p10 | median | p90 | max | p90/p10 |
     |---|---|---|---|---|---|---|
     | card | 87 | 0.19 | 1.48 | 15.67 | 1,367 | **84.6** |
     | artwork | 92 | 0.33 | 2.31 | 15.53 | 1,035 | **47.7** |

     An 84x spread in a per-query quantity is the finding: whatever scalar sits in its place can only
     match a median. Card and artwork also differ (1.48 vs 2.31), so one scalar for both is wrong
     independently of where it is centred.
   - **The error is population-dependent enough to CHANGE SIGN.** The `bench_feature_accuracy`
     population reads this feature over-counting 1.47x; the population measured here reads ~0.99. Treat
     any single-number verdict on it as an artifact of its sample.
   - **The quantity being approximated is the MATCHING SET'S OWN REPRINT DEPTH** — printings the gather
     bit-tests per matching printing. A single global constant was the only option available when the
     estimator produced ONE number; Round 58's `SpaceEstimate` triple is what makes a per-query answer
     possible, and `est.result.printing`/`est.result.card` are both in hand at the site that sets this
     feature.
   - **What was tried and did NOT work, so it is not re-tried:** using the depth observable from
     `acquire` (`matches / eval_domain`) as the per-query factor. Pearson r on log-log is **-0.116**
     (card) and **-0.067** (artwork) — no signal — because in card mode both quantities are card-space,
     so that ratio reads 1.00 at both the median and p90. The depth is not observable from outside.
   - **Next step, and it is a measurement not a commitment:** surface the true estimated depth
     (`est.result.printing / est.result.card`) as a diagnostic field on an instrumented build and
     re-run this correlation. Only if it predicts the realized factor is replacing the constant with a
     per-query term justified. **Separable and independently verifiable: the `Prefer::Default` card arm is
     mis-gated.** `gather_composed_page` has three per-card arms — `Mode::Printing` and the
     grouping arm (`Mode::Artwork`, or `Mode::Card` under a non-default prefer) each add
     `end - start`, the full span, while `Mode::Card` + `Prefer::Default` uses
     `(start..end).find(|pid| is_set(pid))` and adds only `pid - start + 1`, typically 1, because
     printings are stored prefer-descending so the first set printing is already the chosen
     representative. The constant's own doc names this carve-out ("except in its card/default-prefer
     arm it iterates `start..end`"), and the sibling feature five lines below gates on exactly that
     predicate (`Mode::Artwork => true, Mode::Card => !matches!(prefer, Prefer::Default),
     Mode::Printing => false`) — but `compose_scan_printings` is assigned unconditionally, so it
     charges a span walk in the one arm that provably does not do one. Probe: 62 examined against a
     `printing_span` of 191, one per card. In that arm the right magnitude is the candidate-CARD count,
     which the engine already has as `eval_domain`.
2. **`scan_units [printing_compose]` under-counts ~3x.** The highest-n miscalibration in the report
   (32,833 `printing_compose` rows of 51,767 pooled): p50 **0.32-0.38** depending on distinct-on, p10
   **0.05**, spread 20.0-32.5. `scan_units` prices the MATERIALIZING alternatives when they compete
   against compose (see its own doc: "What the MATERIALIZING alternatives see"), so under-counting it
   prices those alternatives too cheap and biases the argmin AGAINST `PrintingCompose`.
   `scan_units [card_range_popcount] / card` has the same defect at p50 0.43 (spread 8.0).
   - **Scope it honestly: this is a tail-correctness item, not a real-traffic latency item.** Measured
     2026-09-05 over the 14,473-query weighted real corpus, `PrintingCompose` was an OPTION on only
     **86 queries (0.6%)**, won 7, and when it lost it lost by a **median 130x** — with just **2**
     losses inside 1.5x and 3 inside 3x. So correcting a ~1.5x bias could flip at most 2-3 real
     queries. It matters for the tails a UNIFORM sampler probes and for the pathological mis-routes
     (Round 63 hit a plan priced at 0.2us against a measured 199.3us), not for real-traffic latency.
   - **`bench_feature_accuracy` runs `--mode uniform` and is NOT traffic-weighted** (171,915
     feature-rows; its own help says uniform "reaches the rare tails where ordering errors hide"). Its
     "57 flagged cells outside [0.8, 1.25]" therefore overstates real-traffic impact and understates
     nothing — read it as a correctness instrument, not a latency one. Every flagged cell is
     `printing_compose` or `card_range_popcount`; nothing on `candidates`/`plane` is flagged at all.
3. **Seed every `SpaceEstimate` with the domain instead of `UNKNOWN`.** **A correctness and
   maintainability item, not a performance one** — it delivers no accuracy win and no latency win, and
   should be judged on that basis. The domain size is a true upper bound, so a space can start
   `{ guaranteed: n_cards, estimate: n_cards }` and only ever tighten. That deletes every `Option`,
   makes `printing()` infallible by construction rather than by `expect`, and removes the "absence
   means unknown, never zero" footgun. What `None` actually overloads today is THREE meanings: a
   genuine unknown (no mechanism proved or guessed anything), a not-applicable (a printing-only
   mechanism has no card/artwork opinion), and a structural proxy for "did a trusted source produce
   this" — the third being what Round 62 found misread in three places. Seeding collapses the first two
   into a number and forces the third to be stated explicitly. Round 60 measured how normal absence is:
   **41,838 of 147,660** tree nodes have `printing_guaranteed` absent while `printing` is present.
   - **Corrected 2026-09-05 — this item does NOT fix `narrow_floor`'s laundering, and its case is
     thinner than first recorded.** The original justification claimed the ambiguity "caused BOTH
     laundering bugs". It caused one (Round 59's `And` seed, fixed). Seeding neither fixes nor unmasks
     `narrow_floor`'s: that function filters children through
     `range_too_broad_to_narrow(c, n_cards)` = `matched > NARROW_FLOOR && matched > n_cards * 0.25`,
     which a seeded full-domain value fails trivially, so seeded children are discarded before the
     `min`. The laundering path is untouched — a child with a REAL estimate-only card count below the
     breadth threshold still gets its guess written into `guaranteed`. Item #2 is required regardless.
   - **Corrected 2026-09-05 — ONE card gate breaks under seeding, not two.** Site by site:
     `est_cards`' and `domain_cards`' folds are `card.best().map_or(x, |dc| dc.min(x))`, which is a
     NO-OP under seeding because `x <= n_cards` already; `card_invariant_domain_exact` is
     `card.guaranteed == Some(domain_cards)`, a VALUE test that survives (it newly fires only where
     `domain_cards == n_cards`, i.e. the whole-corpus card-invariant shape the guard exists to catch);
     only the narrowing exemption `is_and && card.guaranteed.is_some()` is a genuine PRESENCE test, and
     it becomes unconditionally true for every `And`. Printing space is behaviour-neutral throughout,
     since `min(domain, x) = x` for any real `x`. So this needs one explicit signal, not two.
   - **The verification cost is the real reason to be wary.** Rounds 58/59/60 were each verified by
     BYTE-IDENTICAL survey output, the strongest guard this arc has. Seeding makes that unavailable by
     construction — 41,838 node-level `printing_guaranteed` absences become values, so `and_trace`
     diffs are non-empty on purpose. Verification would fall back to semantic-scalars-only plus the
     `{space} == min(guaranteed, estimate)` fidelity check plus an explicit diff of the one behavioural
     site: weaker evidence for a change whose entire point is that it changes nothing.
   - **If it is taken, do it as two commits: the explicit exact-card-source flag FIRST** (that half is
     behaviour-neutral and byte-identical-verifiable), then the seed — so the seed lands on a codebase
     where no consumer reads presence any more and is provably inert.
   - **Scope this item does NOT already have covered.** Round 62 replaced the tightening proxy with an
     explicit flag, which survives seeding — but its own plan claimed the CARD gates were unblocked
     too, and that was wrong. The narrowing exemption needs an "exact card source" flag parallel to
     `printing_tightened`, set where a trusted card count is written, and that work is part of THIS
     item. See the ledger's Round 62 section, and the site-by-site correction above for why it is one
     gate rather than two.
4. **Untangle `narrow_floor`.** Also a correctness item rather than a performance one. It reads
   `s.card.best()` and writes `result_space.card.lower_guaranteed(f)` — a child's GUESS becoming the
   query's BOUND, the same laundering Round 59 fixed in the `And` seed. Still latent, and Round 63 is
   why it stayed that way: the arm that might have unmasked it now writes an exact triple into BOTH
   channels rather than an estimate-only card figure, so nothing yet writes a card-space estimate the
   floor could launder. It is also doing two jobs: its stated purpose is to give card/artwork the free
   per-leaf min-fold printing already has, but its breadth filter is justified by what `narrow_rec`
   will actually narrow to — a plan-cost concern, not an answer-cardinality one. Mathematically a broad
   leaf's count IS a sound bound (`|A n B| <= |A|`), so the filter makes it deliberately weaker than
   the tightest sound bound, for a reason belonging to a different question. It also computes a `min`
   (an upper bound) while being named a floor. Round 60 left a candidate set — **4,317 root nodes**
   with `card_guaranteed` tighter than any child's — but that set also contains legitimate
   `Candidate::Exact` joints, so separating them is the round's actual work. Easiest after #3, when
   bounds are always present. The joint-witness frame in
   [local-engine-joint-witness-and-empty-short-circuit.md](local-engine-joint-witness-and-empty-short-circuit.md)
   may be the honest replacement for its breadth filter rather than a repair of it.
## Lower priority, no urgency

Measured and deliberately NOT scheduled. These were active queue items; each was removed by a
measurement rather than by being built, and each is recorded here so it isn't re-nominated from raw
symptoms. Re-open only with a fresh survey that contradicts the numbers.

- ~~**The 1.95% compose time share might be an artifact of `scan_units` under-counting**~~ —
  **RAISED AND REFUTED 2026-09-05.** The hypothesis was reasonable: `scan_units` prices the
  materializing alternatives, it under-counts ~1.5x pooled, so it should push queries away from
  `PrintingCompose` and thereby deflate compose's own measured share. Measured instead:
  `PrintingCompose` is an OPTION on **86 of 14,473** real queries (0.6%) and wins 7; when it loses, the
  median `predicted_ns` ratio to the winner is **130x**, and only **2** losses sit inside 1.5x. A 1.5x
  correction could flip 2-3 queries. Compose's small share is structural — most real conjunctions carry
  a name/text leaf or an `Or` and are not composable at all — not a consequence of the miscalibration.
- ~~**Generalize "anchored independence" further**~~ — **DELETED 2026-09-05: measured, and there is no
  headroom left to generalize into.** This item's own bar was "show a routing-relevant miss the
  min-fold does NOT already clamp". That measurement now exists, over the seed-63 survey's 9,777 rows,
  for EVERY estimate-class mechanism — claims made vs claims that survived the min-fold to become the
  row's answer, and how many of those landed on the wrong side of `STREAM_MIN_MATCHES`:

  | mechanism | claims | binds | median where it binds | wrong-side CLAIMS | wrong-side FINAL |
  |---|---|---|---|---|---|
  | `Independence` | 2,772 | 18.2% | 1.021 | 496 | **8** |
  | `ColorCmcAnchoredIndependence` | 225 | 33.3% | 1.017 | 20 | **0** |
  | `SetCollectorRange` | 105 | 35.2% | 1.000 | 0 | **0** |
  | `SubtypePairEstimate` | 93 | 32.3% | 1.314 | 0 | **0** |
  | `SubtypeArithAnchoredIndependence` | 75 | 36.0% | 0.943 | 1 | **1** |

  **The whole estimate-class machinery contributes 9 routing-relevant errors in 9,777 rows (0.09%),**
  and the claim -> final collapse is 517 -> 9 because the per-leaf min-fold absorbs 98% of the damage.
  The two anchors this item wanted to generalize score **0** and **1**. Adding a third anchor, more
  residual classes, or multi-class products would be building mechanisms with no measurable routing
  headroom. The 8 `Independence` rows are the only estimate-class residue worth anything, and they
  live in the two-sided `usd` bullet below. Round 56's `any_price_source` precheck (a ~21% cost saving
  on `and_estimate_ns`, not an accuracy fix) is the one piece of the old item still worth doing if
  anyone touches this area. Original description preserved below for its detail.
  3. **Generalize "anchored independence" further.** Last, because the evidence for it got weaker rather
     than stronger: the concrete instance this item used to point at (anchoring `legality x price`) was
     measured on 2026-09-04 and demoted, and the one shape checked closely turned out to be
     near-independent already with the min-fold handling it (see the `Independence` bullet below). Rounds
     50 and 56 shipped two anchors (`SubtypeArithBox`, `ColorCmcTable`), both with a single residual
     `IndepClass::Price` leaf, sharing one `anchored_price_residual` helper. Three directions remain,
     each its own future round (validate independently, don't bundle) — and each now needs to clear a
     higher bar: show a routing-relevant miss that the min-fold does NOT already clamp.
     - **More residual classes.** Only `Price` has a validated real-data example; other classes
       (`ColorId`, `Cmc`, `Type`, etc., wherever the anchor's own residual isn't itself the anchored
       dimension) need their own before/after check before being added, mirroring how
       `independence_safe_pair`'s own registry grew one validated class at a time (Round 38 -> Round 40).
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

- ~~**The general bounded partition search**~~ — **DELETED 2026-09-04: the population it needs does not
  exist in real traffic.** This was the arc's long-standing "eventually we should do the general
  version" item, built on Round 49's `CoveredState.subsets` primitive and blocked on measuring the
  residual-size distribution. That measurement is now done (see Completed), and it is decisive: of
  14,473 real weighted queries, **39** reach the `And` arm at all, and an uncovered residual of **>=3
  leaves occurs on 4 of them**. A general partition search would be built to serve four queries. The
  "notice one bad case, build one validated mechanism" pattern — 8 real gaps closed that way across
  Rounds 34, 40, 42, 44, 45, 48, 51, 52 — is therefore the architecture, not a placeholder for one.
  Revisit only if a future population (the planned uniform-sampler pass, or a traffic corpus that
  better reflects power users) shows many-leaf composable conjunctions actually occurring.

- ~~**Stop the devotion/broadcast leaf arm undershooting**~~ — **measured 2026-09-04, not worth doing.**
  22 devotion queries against ground truth: `scaled/true` spans **0.780x-1.304x**, and only 4 of 22
  clear the routing-relevance bar (>=200 absolute AND >=10% relative) — all 4 single-pip
  (`devotion:{w}` etc.), all OVER-estimates, max absolute miss 1,849. No error anywhere near the
  0.310x the sibling numeric arm had (which Round 63 fixed), and devotion is synthesized from mana
  cost, so `ValueTotals` has no column and there is no cheap exact counterpart to reach for.
- ~~**Anchor `Independence` / the `Or` arm for correlated `legality x price`**~~ — **demoted
  2026-09-04 on measurement.** The loose `Independence` claims this was built on are almost all
  clamped by the per-leaf min-fold before they reach anything. Over Round 63's seed-63 survey (9,777
  rows, 2,088 carrying an `Independence` claim):
  - The claim BINDS on only **32%** of those rows, and where it binds the median claim/true is
    **1.017**. Median across all rows with a claim is 1.600 for the claim but **1.068** for the row's
    final number.
  - Routing-relevant misses `Independence` is actually responsible for: **9 of 2,088 (0.4%)**, ranging
    0.79x-4.83x. The 52x-196x claims never bind.
  - **Not one of the 9 is `legality x price`.** That bucket contributes **0** routing-relevant misses
    of 120; the 9 are dominated by two-sided `usd>=a usd<=b` ranges with type/color/cmc.
  - The worst-looking row is evidence AGAINST the item. `usd>0.04 t:vampire f:oathbreaker` fires
    `Independence` twice, and the `subtype x price` pair gives **1,080 against a true 1,118 (0.966x)**,
    which the min-fold picks. Type and price are near-independent here (vampires are >$0.04 at 85.9%
    against the corpus's 83.0%). The 80,770 claim comes from `f:oathbreaker` covering **99.5%** of
    printings — a NON-SELECTIVE leaf whose product can never beat the other leaf alone. That is not
    correlation, and no anchor addresses it.
  - **The signal that originally justified the item was misread.** Round 63 reported `Independence`'s
    under-truth count rising 172 -> 180 in `check_bound_class_soundness.py`'s ROW-LEVEL view. That view
    buckets by ATTRIBUTED mechanism and its own header warns the attributed mechanism need not be the
    binding one — it is explicitly a diagnostic, not evidence about a mechanism's accuracy.
  - Worth remembering if this area is revisited: an `Independence` pair whose leaves are BOTH
    near-universal cannot tighten anything, so computing it is pure cost — the same shape as Round 56's
    `any_price_source` precheck. A cost saving, not an accuracy fix.
- **`rest_max.printings` could fill the `guaranteed` channel** (from Round 64). It is now a real
  printing-space value, and it is a PROVEN upper bound wherever `SubtypePairEstimate` fires: that arm
  only runs when no exact subtype-pair hit covered the leaves, so the pair was excluded from `top`, and
  a pair absent from the build map has count 0. Today it is `min()`-ed with the independence product
  into one estimate-only candidate, which discards the bound (Round 59's admission rule). Splitting it
  — `guaranteed = rest_max.printings`, `estimate = indep` — leaves `best()` unchanged while populating
  a channel that is currently empty. Deliberately not bundled into Round 64: it changes mechanism
  attribution and needs `check_bound_class_soundness.py`'s mechanism map updated.
- **`nway_estimate_truth_survey.py` barely samples subtype-pair table MISSES** (found by Round 64).
  That round moved its target population's median from 1.309x to exactly 1.000x and the survey read
  ZERO plan flips and no ratio change, because the catalog generates (dim, subtype) pairs almost only
  in configurations that HIT the table. A flat survey is therefore not evidence that a
  mechanism-level fix did nothing — check whether the mechanism's own population is represented first.
  Worth teaching the sampler, alongside the `banned:`/`restricted:` gap already recorded below.
- **Two-sided `usd>=a usd<=b` interior ranges** are where the 9 surviving `Independence` misses
  actually live, so they are the better-targeted successor to the demoted item above — but they need a
  design idea first, not just wiring. `RangeCardCounts` "declines genuinely interior ranges, so a
  two-sided `usd>=a usd<=b` still falls back to the projection", and the obstacle is real: printings
  subtract exactly from the sorted index, distinct CARDS and ARTWORKS do not, because one card has
  printings at several prices. Round 63's `NumericSpanTotals` does NOT transfer — price is
  near-continuous, so a per-distinct-value prefix sum is not the ~30-entry table cmc/power/toughness
  got. Quantile bucketing (`PriceJointTable`'s own approach) is the obvious direction to explore.

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
  two sibling leaf arms, both of which are now settled: Round 63 made the numeric arm exact, and
  devotion was measured and left alone (see the not-scheduled bullets above).
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
- **"Exact" is not one property: a number can be an exact ANSWER and still be the wrong DOMAIN.**
  `result.card` is read by `acquire_plan_features` as the card count the materializing alternatives
  walk, and that parts company with the answer's own card count exactly when narrowing declines a
  broad child. Round 63 found this the hard way: proving `card: Some(0)` on a disjointness branch is a
  true statement about the answer, and it drove `border:white border:black`'s `eval_domain`/`scan_units`
  to 0 and flipped its plan against a realized `cards_visited` of 2,059. **All 303 tests passed with
  that bug in place** — only checking the feature against realized execution caught it. So: before
  folding any newly-available exact count into `result`, ask which of the two questions it answers,
  and verify against `explain_analyze`'s realized counters rather than against the suite.
- **Measure the cost of an accuracy fix before assuming the cheap version is the cheap one.** Twice
  now the accurate implementation has also been the FASTER one, and once the obvious reuse was 2.9x
  slower than the thing it replaced. Round 63 rejected `arith_tuple_totals` reuse at +186% on
  `and_estimate_ns` p50 (control +7%) and shipped a new ~570-byte-per-field table that measured −19%
  against a −12% control; Round 61's shared lookup was −5.7% where the naive two-call form was +9.3%.
  Always split by a control subset the change cannot touch — a same-build canary has now twice read
  clean while the build itself moved.
- **For a CAPPED estimate, bounding the cap below the decision boundary beats improving the estimate.**
  Round 65's whole payoff came from forcing `rest_max.printings` under `STREAM_MIN_MATCHES`, not from
  better estimation: all 9 routing-relevant misses read the cap exactly rather than the independence
  product, and the alternative that improved coverage instead (doubling the table) left the ratio
  distribution flat while fixing the same 9 rows. Corollary for reading this arc's own history: a
  mechanism whose cap already sits far below the boundary — `set` at 28x, `colors` at 9.6x — cannot
  affect routing however inaccurate it is, so accuracy work on it is cosmetic by construction. Check
  the cap before scoping the estimate.
- **A safety argument must name the SPACE it holds in, and be asserted where it is established.** The
  claim "`rest_max` lands far below the count where a wrong estimate flips a routing decision" sat in
  the build for 31 rounds and was false for one of three dimensions the whole time: it was verified in
  card space (identity's card `rest_max` is 377) while the boundary applies to the space the estimate
  is compared in (printing, where it was 1,060 against 1,024). A comment cannot notice that it has
  drifted; a `debug_assert` at the point the invariant is created can.
- **Answer a structural question with a structural signal recorded where the structure happens, never
  by comparing two numbers downstream.** Round 62's retired test asked "did any mechanism tighten
  this?" by comparing `candidate` and `result`, which cannot see a tightening that moved only
  `guaranteed` — and Round 59 had made those routine. Two corollaries worth applying before the next
  proxy gets written: an `Option`'s PRESENCE is not a structural signal if any future round might seed
  the field (domain-seeding makes both card gates vacuous either way — see item #3), and a flag derived
  as `!=` against a field's own earlier value is safer than one threaded through every mutation site,
  because monotone mutators make the comparison exact while a threaded flag goes stale silently when
  someone adds a write.
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
  narrowly scoped; generalizing it further was measured and DELETED in 2026-09-05 — see the
  anchored-independence bullet under "measured and deliberately NOT scheduled".
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
  the union-of-3-spaces top-N cutoff and a real per-space `rest_max` triple (Round 64 backported both to the three older tables). Surfaced the estimate-placement ordering constraint now
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
  reprint-ratio arms (devotion, bare cmc/pow/tou) alone; Round 63 closed the numeric one and measured
  devotion as not worth doing. The regressions it left were requeued as an `Independence` anchoring
  item, which was itself demoted on measurement in 2026-09-04 — see the not-scheduled bullets above.
- Round 62: the three presence/equality proxies in `acquire_plan_features` replaced by explicit
  structural signals — the two card-trust gates read `est.result.card.guaranteed` (a PROVABLE
  zero-delta: nothing writes `result.card`'s estimate channel anywhere, so the two spellings are the
  same `Option` at every node), and a new `ComposeEstimate::printing_tightened` bool, set where a fold
  actually lowers `result.printing` off its seed, replaces `est.candidate.printing() ==
  est.result.printing()`. The flag disagrees with the retired test on 0.3-0.45% of rows, **every one
  `old=False → new=True`** — it only ever finds a bound-only tightening the number comparison was blind
  to. Zero plan flips; `bench_pairwise_ordering` unchanged, `bench_feature_accuracy` 0 cells changed
  verdict. Two caveats, both live: it does NOT unblock the card half of item #3 (its own plan claimed
  otherwise and was wrong), and it cost 6 rows on 3 queries, which Round 63 Part 2 then closed.
- Round 63: two exact numbers that existed and were being discarded. **Part 1** retires the last
  reprint-ratio leaf arm — `NumericSpanTotals`, a per-distinct-value prefix sum over each numeric
  field's existing sorted index, makes bare cmc/power/toughness exact in all three spaces (`cmc=0`
  3,699 → 11,948 = truth). The obvious `arith_tuple_totals` reuse was measured at **+186%** on
  `and_estimate_ns` and rejected (kept on `r63p1-arith-tuple-reuse-measured-slow`); the table it was
  replaced with measures *faster* than the inexact path it replaces. **Part 2** folds `PairTotals`'
  exact card/artwork columns, which `get_all` was already fetching for the trace and the estimator was
  throwing away — closing Round 62's three regressed rows structurally, with `eval_domain` now equal to
  realized `cards_visited`. 20 plan flips of 9,777 rows; ratio 0.144 → 0.140; `bench_feature_accuracy`
  flagged cells 62 → 60, the two that cleared being exactly the `eval_domain … / card` pair this round
  targets. Its apparent side effect — `Independence`'s under-truth count up 172 → 180 — was later shown to be
  a MISREAD of a row-level diagnostic, and re-measuring is what demoted the anchoring item entirely.
- Round 65: an inclusion FLOOR on every top-N pair table — any pair at or above
  `STREAM_MIN_MATCHES / 2` printings is kept regardless of rank — which turns "the fallback estimate
  cannot flip a routing decision" from a corpus observation into a proven invariant, `debug_assert`ed
  where it is established. Fixed a real hole: `identity`'s `rest_max.printings` was **1,060** against a
  **1,024** boundary, so the CAP itself was on the wrong side, and all 9 of that dimension's
  routing-relevant misses read the cap exactly. After: cap **509**, routing-relevant misses across all
  dimensions **1 -> 0**, `set`/`colors` byte-identical, **+8 KB** of archive. The Round 34 comment that
  had asserted this was already safe was verified in CARD space while the boundary applies to printing
  space — see the ledger's Round 65 section, and the standing principle it generalizes.
- Round 64: backported Round 55's union-of-3-spaces cutoff and per-space `rest_max` triple to
  `SetSubtypeTable`/`ColorSubtypeTable`, and made `SubtypePairEstimate` printing-space-native — the last
  `card-space * n_printings / n_cards` scaling in this arm, after Rounds 61 and 63 removed the other two.
  On 750 real table-MISS pairs: median **1.309x -> 1.000x**, p90 8.25 -> 6.00, routing-relevant 3 -> 1.
  Deleted `top_n_and_rest_max` (no callers left), migrating its Round 47 tie rationale into the surviving
  helper and retargeting its four guard tests rather than deleting them. Survey-flat by construction —
  see the sampler-coverage bullet above. Per-query on identical truths, `set` lands **91 of 168 rows closer
  to truth against 22 further** and cuts predictions of 0-against-nonzero-truth from 72 to 49; its median
  ratio falling (0.900 -> 0.586) is an artifact of the distribution being zero-inflated, not a regression —
  a median summarizes such a distribution badly.
- Measurement, no round number (2026-09-04): **the residual-size distribution, and the route share
  that reframes this whole doc.** Measured over real weighted traffic (14,473 queries) rather than the
  sampler, deliberately — the sampler's shape templates decide residual sizes, so measuring against it
  would partly measure the sampler. Results: **39 of 14,473** queries reach the `And` arm; uncovered
  residual >=3 leaves on **4**; `printing_compose` is **1.95% of weighted query time** against
  `candidates`' 96.31%. Deleted the general partition search outright (above) and forced every
  remaining item to state its justification. Two methodology notes worth keeping, because the first
  attempt got both wrong: `and_trace.tree.children` is **not** a leaf count (a plane-absorbed leaf
  collapses into one child with a `None` expr, and the arm's real leaf view is the union of trace
  children with every `considered` group's `leaves`), and "no `and_trace`" means "not composable",
  **not** "not a conjunction" — 4,102 real conjunctions route through `candidates` because a name/text
  leaf or an `Or` makes them non-composable.
- Harness fix (no round number, a Python-only fix outside the engine): `client/query_sampler.py`'s
  `_count_row` folded oracle/flavor words via `Counter.update(set(...))` — bare-set iteration is
  hash-seed-randomized per process, so tied-frequency co-occurring words could swap `most_common()`'s
  tie-break across runs. Fixed with `sorted(set(...))`; verified byte-identical output across 5 fresh
  process runs (was 20-32 line diffs before), plus a subprocess-based regression test that fails on the
  pre-fix code and passes after.
