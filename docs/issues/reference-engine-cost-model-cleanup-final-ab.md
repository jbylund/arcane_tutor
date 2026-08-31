# Cost Model Cleanup — Final A/B Against `main` (Round 27)

Round 27, and the last one before this branch splits into PRs. Twenty-six rounds of work on
`costcell/trunk` are documented across
[local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)
(Phase 1, Rounds 0-10), [#852](00852-engine-compose-acquire-p3-p4-ranking.md) (resolved as a side
effect), [local-engine-plane-acquire-compose-costing.md](local-engine-plane-acquire-compose-costing.md)
+ [local-engine-plane-scope-printing-compose-executor.md](local-engine-plane-scope-printing-compose-executor.md)
(Rounds 12-13, "don't build this"),
[done/local-engine-gathered-scan-undercosted-arith-existential-and.md](done/local-engine-gathered-scan-undercosted-arith-existential-and.md)
+ [local-engine-domain-cards-existential-arith-and.md](local-engine-domain-cards-existential-arith-and.md)
(Rounds 15-25), and [reference-engine-cost-model-state-2026-08.md](reference-engine-cost-model-state-2026-08.md)
(Round 26's whole-engine survey). All of that validation was against the branch's own history — each
round measured its own before/after, and Round 26 measured only `costcell/trunk` against itself.
**Nothing in the tree so far measures the whole branch against `main` in one sitting.** This doc is
that measurement: two isolated builds, five harnesses, one sitting, no code changed.

## Method

Two release wheels, built with `maturin build --release` (never `maturin develop`, which rewrites the
shared `.venv`'s `card_engine.pth` and would flip every other session's `import card_engine` — see
the shared-checkout note in project memory). Each wheel unzipped to its own scratch directory and
selected per-invocation via `PYTHONPATH`, verified by printing `card_engine.__file__` and hashing the
`.so` before any measurement — confirmed four distinct binaries (plain + `routed-phases` for each
build), and confirmed the shared `.venv`'s own `import card_engine` still resolves to the primary
checkout throughout, untouched.

- **`main`** @ `ca016410`, built in a fresh detached worktree.
- **`costcell/trunk`** @ `ddba298a`, built in this round's own worktree (`costcell/27-final-ab`).

Corpus: `benchmarks/bitplanes/corpus.jsonl` (97,812 printings), read-only, from the primary checkout —
never written to; every harness was pointed at a `--shm-path` under scratch instead of the corpus's
own directory (the default `--shm-path` would have written a `.store` file next to the read-only
corpus).

`bench_regret_matrix.py` needs a `routed-phases` build for its decline-row population; both `main`
and `costcell/trunk` wheels were built both ways (plain for the other four tools, `routed-phases` for
the regret matrix) so the two sides are always compared like-for-like.

### Canary: the measurement doc's own warning, reproduced

[reference-cost-model-measurement.md](reference-cost-model-measurement.md) warns that a same-build,
same-seed pair false-positived at the old (2, 7) trial defaults and reads clean at 30. On this
machine — a shared dev box with a visible background load (pants test workers, MCP servers, browser
automation processes) at the time of this run — **30 was not enough**: three same-build pairwise
checks at the tool's own default trial count (30) read `-1.1`, `+2.0`, `+3.1` µs against a ~51-54 µs
mean latency, each with a bootstrap CI excluding zero in a different direction. Raising `--trials` to
60 (the measurement doc's prescribed remedy when a canary fires) tightened individual pairs to
~0.3 µs, but a 3-pair pooled same-build check at 60 trials (n=2,371 shared queries) still read a small
systematic `+0.5 µs, CI [+0.4, +0.7]` — traced to an order effect (the second run in a pair reads
slightly slower than the first on this machine), not random noise, since all three pairs shared the
same first/second ordering.

Because that order effect is real, every `main`-vs-`costcell/trunk` latency round below alternates
which build runs first, so the effect cancels in the pooled result rather than biasing it. See
[Latency](#latency) for why this matters to the headline number.

## Cost/feature accuracy

### `bench_cost_model_agreement.py --seconds 300 --seed 0`

| | `main` | `costcell/trunk` |
|---|---|---|
| queries sampled | 87,212 | 97,575 |
| cells within `[0.8, 1.25]` (by acquire) | 13/17 | 12/17 |
| cells within `[0.8, 1.25]` (by unique) | 10/12 | 10/12 |

One FAIL flip, both directions checked:

- **New FAIL, immaterial**: `PlanePopcountOrder / plane` — median `0.81` (main, PASS) → `0.80`
  (trunk, FAIL). This is a boundary artifact, not a real change: the displayed medians round to the
  same two decimals `main` passed on. Confirmed inert by both the regret matrix (`plane` acquire is
  0% of all SHARE, mean regret `0.00 µs` on both builds) and pairwise ordering (`PlanePopcountOrder`
  wins its argmin 100% of the time under `plane` acquire on both builds) — matches Round 26's own
  "Explicitly not candidates" finding for this exact cell.
- **No FAIL→PASS flips.** `GatheredScan / candidates` moved from median `0.61` (main, 15% within 25%)
  to `0.79` (trunk, 31% within 25%) — real, substantial movement toward agreement — but stays a hair
  under the `0.8` floor on both builds, so the verdict column doesn't change.
- Everything else moved by roughly the sampling-driven ~12% larger `n` (trunk sampled more queries in
  the same 300s wall-clock budget) with proportionally similar ratios — no other qualitative shift.

### `bench_feature_accuracy.py --seconds 300 --seed 0` (mode=uniform, default)

**Fixed in Round 28** ([local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)'s
own Round 28 section has the full bisection, mechanism, and confirmation pass) — recorded here as this
survey originally found it, plus the resolution:

| feature (pooled) | `main` median | `costcell/trunk` median (as surveyed here) | `costcell/trunk` median (post Round 28 fix) | verdict |
|---|---|---|---|---|
| `scan_units` | 1.00 (no flag) | 0.70 | 0.94 | UNDER-COUNTS → clean |

`main`: 697,375 feature-rows, `scan_units` reads clean (median 1.00, no verdict flag). `costcell/trunk`
as surveyed by this round: 705,768 rows, `scan_units` reads `0.70` and is flagged `UNDER-COUNTS` pooled
and across nearly every `unique`/`prefer` slice. This reproduced Round 26's own number for this exact
cell (`reference-engine-cost-model-state-2026-08.md`, "Feature accuracy" section: median 0.70,
"already covered, the era-correlated print-position confound... plus the printing-varying range depth
work") to two decimal places, and this A/B added the piece Round 26 didn't have: `main` does not have
this problem. **Round 28 bisected it precisely**, rather than accepting the "byproduct of this
branch's own fixes" framing as the final word: the actual trigger was a single commit
(`e1c40466`, this branch's own Round 7) whose broad-guard `scan_units` scale — fit exclusively against
`unique=card` samples, exactly like its sibling `COMPOSE_RANGE_AND_BROAD_SCAN_SCALE` from Round 4 —
was applied unconditionally to `Mode::Printing`/`Mode::Artwork` too, where the real
`printings_examined / n_printings` ratio for that guard-fired population reads an exact, zero-spread
1.0 (those modes' kernels never short-circuit). Scoping both scales to `Mode::Card` only closed the
gap: pooled `scan_units` median `0.70` (UNDER-COUNTS) → `0.94` (clean, inside `main`'s own `[0.8,
1.25]` band), confirmed via a fresh isolated-wheel `main`-vs-fixed-tip A/B at this same `--seconds 300
--seed 0` protocol. The residual `0.94` vs. `main`'s `1.00` is the two other, already-documented,
un-touched-by-this-fix contributors (`PrintingCompose`'s "narrow"-bucket under-count, named and
deferred by Round 7 itself, and the era-correlated existential-leaf confound Rounds 17/20/25 already
characterized as out of their own blast radius) — real, pre-existing, not introduced by any commit on
this branch, and not attempted this round; see Round 28's own section for why (the root cause is
`domain_cards`'s documented broad-range undercount for bare ranges, which nine prior rounds already
found hard to fix directly).

No other feature changed materially in the pooled table.

## Regret

### `bench_regret_matrix.py --seconds 180 --mode realistic` (routed-phases builds)

| | `main` | `costcell/trunk` | Δ |
|---|---|---|---|
| multi-plan queries | 75,112 | 80,499 | — |
| total regret | 120.3 ms | 71.4 ms | **-41%** |
| mean regret/query | 1.60 µs | 0.89 µs | **-44%** |

SHARE by compose paging branch (the mechanism most of this branch's rounds targeted):

| branch | `main` SHARE | `main` mean | `costcell/trunk` SHARE | `costcell/trunk` mean |
|---|---|---|---|---|
| `Perm` | 46% | 5.99 µs | 68% | 4.86 µs |
| `OrderbyWalk` | 42% | 14.88 µs | 10% | 1.90 µs |
| `Gather` | 8% | 0.19 µs | 15% | 0.18 µs |

`OrderbyWalk`'s absolute contribution collapsed from ~50.5 ms to ~7.1 ms (SHARE is a fraction of a
shrinking pie, so read the absolute too) — the single largest driver of the whole-branch improvement.
`Perm`'s absolute contribution also fell slightly (~55.3 ms → ~48.6 ms). Neither branch's *own*
cost-formula fix is what's recorded as shipped in the docs read above (Round 26 names `OrderbyWalk`'s
fix as still "fully-designed, unshipped"); the reduction is consistent with the Sigma decision rule
(`docs/issues/local-engine-compose-perm-sigma-decision-rule.md` and the Step 4-7 commits in this
branch's recent history) steering more queries away from the branch transitions where `OrderbyWalk`'s
miscalibration would have been exposed, rather than fixing the miscalibration itself.

The `#852` story, specifically — `picked → best` transitions:

| transition | `main` n | `main` SHARE | `costcell/trunk` n | `costcell/trunk` SHARE |
|---|---|---|---|---|
| `PrintingCompose → GatheredScan` | 1,072 | 43% | 180 | 8% |
| `StreamedSelect → GatheredScan` | 1,135 | 18% | 1,573 | **49%** |

The misroute `#852` targeted dropped 83% in raw occurrence count (1,072 → 180) and from the single
largest SHARE to a minor one. But `StreamedSelect → GatheredScan` — the compound-existential-plane
`GatheredScan` cost-formula miscalibration Round 26 explicitly parked as "needs a saturating/banded
rate, not a flat linear one" — grew to the largest single slice on `costcell/trunk`, both in SHARE and
in absolute terms (~21.7 ms → ~35.0 ms). This matches Round 26's own ranking of it as the largest
still-open item, now visible for the first time against a genuine `main` baseline rather than only
against the branch's own history.

## Latency

### `bench_query_latency_ab.py --mode realistic --trials 60 --sample 800`, 4 rounds, order-alternated

| round | seed | order | B - A | 95% CI | verdict |
|---|---|---|---|---|---|
| 1 | 1 | main, trunk | -1.9 µs | [-2.4, -1.4] | trunk faster |
| 2 | 2 | trunk, main | -0.1 µs | [-0.8, +0.5] | no detectable difference |
| 3 | 3 | main, trunk | -0.4 µs | [-1.1, +0.3] | no detectable difference |
| 4 | 4 | trunk, main | +0.6 µs | [-0.5, +1.5] | no detectable difference |
| **pooled** | all 4 | alternated | **-0.4 µs** | **[-0.8, -0.1]** | trunk marginally faster |

Pooled over 3,158 queries shared across all four rounds: `costcell/trunk` reads a mean latency of
52.0 µs against `main`'s 52.4 µs — nominally outside the bootstrap's zero-crossing, in the expected
direction, but the magnitude (~0.8% of mean latency) is the same order of magnitude as this machine's
own measured same-build noise floor (the pooled canary read `+0.5 µs` under an *unbalanced* run order;
see Method). Only 1 of the 4 individual rounds was independently significant.

**Reconciling this with the 41% regret-matrix win**: regret is concentrated in a specific, minority
population — compose-paging-branch mismatches under `printing_compose` acquire, which the regret
matrix shows is ~13-27% of all multi-plan queries (`n=20,458`/`75,112` on main, `21,931`/`80,499` on
trunk) and produces the vast majority of the SHARE. Pooled over *all* realistic-mode traffic —
dominated by cheap `candidates`/`plane` lookups where nothing changed — that improvement is real but
small enough, at an 800-query-per-round sample, to sit right at the edge of what this environment can
resolve from noise. A user issuing the specific query shapes the regret matrix flags would feel a
real, measurable improvement; a user issuing a uniformly-sampled realistic query would not reliably
notice one at this sample size.

## Pairwise ordering

### `bench_pairwise_ordering.py --seconds 300`, realistic and uniform, both builds

The `#852` cell head-to-head against `main` (not against the branch's own Round-0 baseline, which the
brief for this round flagged as measured after some fixes had already shipped):

| mode | pair / acquire | `main` ordered-right | `main` mean regret | `costcell/trunk` ordered-right | `costcell/trunk` mean regret |
|---|---|---|---|---|---|
| realistic | `GatheredScan` vs `PrintingCompose` `[printing_compose]` | 80% | 8.09 µs | 90% | 3.03 µs |
| uniform | `GatheredScan` vs `PrintingCompose` `[printing_compose]` | 91% | 3.97 µs | **87%** | **5.25 µs** |

Against a real `main` baseline, `#852`'s realistic-mode improvement is **80% → 90%**, not the
**69% → 97%** the tracking docs' own internal comparison reports — confirming the round's brief was
right to be suspicious of that number; the internal baseline was measured on a `costcell/trunk`
ancestor that already carried some of Round 0-10's fixes, which inflates the apparent delta. The real,
`main`-relative improvement is smaller but still genuine and in the right direction.

**Under `uniform` mode — the sampler built specifically to reach rare tails — the same pair got
worse**: 91% → 87% ordered right, mean regret nearly doubling (3.97 → 5.25 µs). This is the branch's
one clear pairwise-ordering regression: the fixes are tuned to realistic-traffic-shaped populations
(the `QuerySampler`'s traffic weighting) and give up a small amount of accuracy on the query shapes
`uniform` mode is designed to surface. Pooled (not sliced by acquire), the same direction holds:
`GatheredScan` vs `PrintingCompose` overall reads 91% → 87% under uniform, 84% → 89% under realistic.

The structurally-inert `[plane]` pairs (`PlanePopcountOrder` always wins its argmin regardless of how
any competitor is priced) were re-confirmed on both builds, both modes — 100% ordered right,
0.00-0.01 µs regret throughout, consistent with Round 12/13's original finding.

## Honest verdict

The aggregate effect is real, but noisier and smaller than the round-by-round narrative alone would
suggest, and it is not uniformly positive.

**What holds up:**
- Regret fell 41% in total, 44% per query, on a realistic traffic mix — the single most important
  number here, and it is not a wash: the reduction is dominated by the `OrderbyWalk` paging branch
  collapsing from 42% to 10% SHARE, a real, large, `main`-relative win.
- The `#852` misroute (`PrintingCompose → GatheredScan`) dropped 83% in occurrence and from the
  largest SHARE to a minor one — genuinely fixed, just not by as much as the branch's own internal
  comparison claimed (80%→90% ordered-right against `main`, not 69%→97%).
- A small, marginally-significant end-to-end latency win (-0.4 µs pooled, in the expected direction)
  survived a canary-verified, order-alternated measurement — real, but small enough that a single
  realistic user request would rarely notice it.

**What doesn't, or is smaller than advertised:**
- `#852`'s own internal "69%→97%" figure does not survive a head-to-head against `main` — the true
  number is 80%→90%, because the internal baseline had already absorbed some of Round 0-10's fixes.
- `scan_units` pooled feature accuracy got measurably worse (`main` 1.00 clean → `costcell/trunk` 0.70,
  UNDER-COUNTS) — a real, `main`-relative regression, low-severity per this round's own regret-matrix
  cross-check, and **fixed by Round 28**: a mode-scoping bug (a `unique=card`-only-calibrated scale
  applied to Printing/Artwork mode too) traced to Round 7's own `e1c40466`, closed by scoping it to
  `Mode::Card`. Pooled median now `0.94`, inside the same band `main`'s `1.00` sits in — see
  `local-engine-gathered-scan-card-printing-varying-depth.md`'s Round 28 section.
- Pairwise ordering for `GatheredScan` vs `PrintingCompose` under `printing_compose` acquire got worse
  under `uniform` sampling (91%→87%) even as it improved under `realistic` sampling (80%→90%) — the
  branch traded rare-tail accuracy for common-case accuracy on this one cell, which is a defensible
  trade given realistic traffic is what users send, but it is a trade, not a pure win.
- `StreamedSelect → GatheredScan` grew to the largest single regret slice on `costcell/trunk`
  (18%→49% SHARE, ~21.7ms→~35.0ms absolute) — not a regression introduced by this branch (Round 26
  already named and parked it), but proof that the branch's 26 rounds did not touch the largest
  remaining opportunity, which is now more visible precisely because everything else shrank around it.
- The 41% regret win does not translate into a latency difference an average realistic query would
  reliably notice, because the affected population is a minority of realistic traffic.

**Overall**: the effort was worthwhile and the routing-regret number is genuinely, substantially
better against `main`, not just against the branch's own history — but a skeptical reviewer reading
only the round-by-round docs would come away expecting a bigger, cleaner, more uniform win than what
a fresh `main`-relative measurement actually shows. Ship it, but do not carry the `69%→97%` or
"41% regret reduction ≈ 41% faster" framings into the PR descriptions; use the numbers in this doc.
