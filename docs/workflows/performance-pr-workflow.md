# Performance PR Workflow: Design Doc to Merge

Applies to changes justified by "queries get faster" or "memory footprint shrinks" — engine
kernel work, index/bitplane additions, SQL generation changes. Correctness-only fixes don't need
a benchmark story; see [differential property tests](https://github.com/jbylund/sylvan_librarian/pull/641)
for how those are verified instead.

## 1. Write the design doc

Start (or refine) a doc at `docs/issues/<name>.md`. These are local working notes — untracked,
not shipped in the PR diff — so write freely. Shape follows the
[performance issue template](../../.github/ISSUE_TEMPLATE/performance.md):

- **Measured problem** — numbers, and the protocol used to get them (warmup/window, corpus, build, machine)
- **Where the cost is** — mechanism, not symptom
- **Proposed approach** — design, expected cost, alternatives considered
- **Acceptance** — which benchmark proves it, which queries must improve, what must not regress, and
  — for executor work — **which physical plan** is being changed, since that decides whether the
  broad screen can see the change at all (step 2's plan-pinned block)

If the design claims a predicate is exact/tight, check that claim against the existing composition
invariants *before* writing any code, not after: `Not` only narrows through tight children, and
`And`/`Or` of same-space tight sets is assumed to stay tight. A predicate that's only exact in
isolation (true for a lone leaf, false once ANDed with another predicate over the same
non-card-invariant domain — a shared-witness problem) is a silent correctness bug if it's marked
tight anyway, not just a missed optimization. This is a design-time review, not something a
benchmark will surface — a wrong exactness claim produces wrong *results*, not just slow ones, and
a differential test only catches it if someone thinks to write the specific case that breaks it.

Recent examples: `local-engine-legality-bitplanes.md`, `00630-engine-card-bitplanes.md` (both closed out under
`docs/issues/done/` once merged).

## 2. Build the test sets: broad screen, plan-pinned, targeted set, and kernel micro-benchmarks

Three rungs, and which one answers the question depends on what changed. The broad screen measures
**what a user gets** — the cost-based router picks a plan and the number includes that choice. A
plan-pinned run measures **one executor**. A kernel benchmark measures **one subroutine**. Reaching
for the wrong rung is the most common way a real win reads as noise; see the plan-pinned block for
why that is a router-era problem this doc predates.

**Broad regression screen** — `scripts/survey_queries.py`. This is the one to reach for by
default; it composes `client/query_runner.py`'s hand-tuned dimension weights (30+ query types,
no exact-name bias) with a slice of real `scryfall.com/search` traffic from
`benchmarks/wild-queries/wild-corpus.jsonl`. Exact-name lookups (`!"Sol Ring"`) dominate that wild
corpus by raw frequency, so they're deliberately capped at `WILD_NAME_LOOKUP_FRACTION = 1/6` of
the wild slots (see PR #646) rather than sampled proportionally — if a future edit to that file
removes the cap, the survey silently reverts to being name-search-heavy, so check for it before
trusting a survey run as a general screen.

```bash
.venv/bin/python scripts/survey_queries.py --out benchmarks/survey/baseline-main.csv \
    --count 400 --wild 120 --seed 42
```

`client/query_runner.py` itself is the live load-testing loop this reuses weights from — don't
invoke it directly for a one-shot baseline CSV.

**Targeted set** — a fixed `CONFIGS` list scoped to the feature area, following the pattern in
`scripts/bench_bitplanes.py`: named groups of queries that exercise the changed code path, plus a
handful of `control` queries picked to be unaffected (they must not regress). Corpus is exported
once from the blue DB, not regenerated per run:

```bash
COLS=$(.venv/bin/python -c "import card_engine; print(', '.join(card_engine.ENGINE_COLUMNS))")
docker exec sylvan_blue-postgres-1 psql -U foouser -d magic -X -At \
    -c "SELECT row_to_json(t) FROM (SELECT $COLS FROM magic.cards) t" > benchmarks/<feature>/corpus.jsonl
```

Check whether a corpus already under `benchmarks/` covers this schema before re-exporting —
`ENGINE_COLUMNS` changes rarely, so an existing export (e.g. `benchmarks/bitplanes/corpus.jsonl`)
is usually still valid and saves standing up Docker/the blue DB at all.

If the change isn't engine/bitplane-shaped, write a small new script on this pattern rather than
stretching `survey_queries.py` to cover area-specific cases it wasn't built for.

**Plan-pinned measurement** — for any change to a *plan's executor* rather than to the cost model.
This is the rung that did not exist when the rest of this doc was written: with a cost-based router
in front of six physical plans, an end-to-end latency delta is the sum of two independent effects,
and the survey cannot tell them apart.

- The executor got faster **but the router never picks it**, so the survey moves by 0% and the win
  looks like it never happened.
- The change moved a `cost::plan_cost` input, so the router picks *differently* — the survey moves,
  and none of the delta is the executor's. The same query can be served by a different plan
  before and after, which also silently breaks the `total`-row parity check's usefulness as a
  "same work, less time" argument.

`explain_analyze` ([`card_engine/src/lib.rs`](../../card_engine/src/lib.rs)) is the fix, and it is
already built: it drives `run_query_with_plan` for **every applicable plan**, `num_warmups` discarded
rounds then `num_trials` recorded ones, and returns raw per-trial nanoseconds next to each plan's
predicted cost. Pin the plan under test, compare it to itself across builds, and report the routing
change as a separate line item.

Two scripts already drive it — prefer extending one over writing a third:

```bash
# per-(plan, acquire branch) measured vs. predicted, plus the phases no cost term describes
.venv/bin/python scripts/bench_cost_model_agreement.py --seconds 120
# does argmin(predicted) match argmin(measured)? reports regret, in time, over multi-plan queries
.venv/bin/python scripts/bench_plan_misselection.py --source wild-operators --sample 200
```

Three properties of `trials_ns` that decide whether a number means anything:

- It is a fair head-to-head **between plans**, not a reproduction of a real query's wall time. Each
  forced run re-runs its own `prepare_candidates`, where `run_query_routed` acquires the shared
  artifact once. Never compare a `trials_ns` figure against a `query()` latency.
- Participants are shuffled per round from a fixed seed, not rotated, so ordering is reproducible but
  **not position-balanced**. Since every consumer reduces with `min`, a participant can draw the warm
  tail twice at low trial counts. Use ≥ 7 trials (what `bench_cost_model_agreement.py` uses); do not
  read a 2–3 trial run as a head-to-head between plans within ~10% of each other.
- A plan can return `None` — declined on its applicability predicate or, for the fastpaths,
  structurally. A decline still costs real time (`DeclineSparseExact` composes the printing bitmap
  before turning back) and produces no page, so it appears in no measured/predicted table. If the
  change touches a fastpath's gate, the decline cost is part of the result, not an absence of one.

**Acceptance for an executor change therefore has two halves**, and a PR that reports only the first
has not shown the change helps anyone: the pinned plan is faster, *and* the router still routes to it
(or the cost model was updated in the same PR so that it now does). An executor win the router
declines to use is a latent win, and the PR body should say so plainly rather than quoting a survey
delta that came from somewhere else.

**Kernel micro-benchmarks** — for representation-level questions neither of the above can resolve:
which of two algorithms/data structures wins for a specific sub-routine, or *why* a broad-survey
regression happened once you know one exists. End-to-end query timing can't isolate sub-microsecond
effects; a kernel benchmark runs the two contenders directly against each other over real data,
nothing else in the loop. Follow the existing pattern (`bench_mana.rs`, `bench_verify_cost.rs`,
`bench_text_search.rs`, `bench_posting_intersect.rs`, `bench_iter_dispatch.rs`,
`bench_word_dict_scan.rs`): a `#[cfg(test)] mod bench_<name>;` in `lib.rs`, an `#[ignore]`d test
function reading `benchmarks/verify-order/real.store` (rebuild it if the archive layout changed
since it was last built — see any of those files' module doc for the one-time command), asserting
every contender agrees on real data before timing any of them, run via
`cargo test --release bench_<name> -- --ignored --nocapture`. This is what actually diagnosed and
fixed the regression in #663 (`bench_word_dict_scan.rs` comparing `match_indices` vs `memchr::memmem`
over the real dictionary blob) — reach for it whenever the broad/targeted scripts show something
regressed but not *why*, not only when scoping a brand-new representation up front.

## 3. Baseline on main

Run both scripts against a `main` build before touching anything. Save CSVs under
`benchmarks/<feature>/`, named by branch or commit sha (e.g. `baseline-main-d3c5e58.csv`). The
`benchmarks/` tree is untracked working data — never committed, just local scratch for the
duration of the PR. A `git worktree add <path> main` alongside the working branch is the clean way
to get a `main` build without disturbing uncommitted work.

If the change touches archive layout (`CardIndexes`, any new/changed index struct), also capture a
memory baseline: build with `--features alloc-counter` and read `QueryEngine.mem_stats()` after
loading the same corpus (`archive_bytes`, `indexes_rkyv_bytes`, `reload_peak` are the ones worth
tracking). The issue template's "Measured problem" section already asks for "archive bytes, RSS"
alongside query timings — this is easy to skip since the query-latency scripts don't touch it at
all, but a change can shrink or grow the archive independently of what happens to query speed (#663
turned out to shrink it 14%, discovered only after the PR was already up because nobody had asked).

## 4. Implement, with correctness first

Add/extend unit and differential tests before chasing speed. For engine changes, a `total`
row-count column doubling as a parity check (identical across builds) is the cheap way to catch a
change that's fast because it's wrong. Self-review the diff before re-measuring — don't loop
performance tuning on top of a design you haven't re-read.

## 5. Re-measure and loop

Re-run the same two scripts against the branch build, same corpus and seed. Compare:

- Broad screen: percentiles (p50/75/90/95/99/max), a "top improvements" table, and — importantly —
  every remaining regression, not just the ones that look bad in aggregate.
- Targeted set: per-query before/after/speedup plus a geometric mean.
- Plan-pinned, if the change touched an executor: the target plan's `trials_ns` before/after, **and**
  separately whether the routed pick changed on those same queries. A broad-screen delta that
  disagrees with the pinned delta is not a contradiction to explain away — it means routing moved,
  and the two numbers are measuring different things.
- Memory, if step 3 captured a baseline: re-run `mem_stats()` against the branch build, same corpus.

Fix regressions, look for further wins, repeat until the branch is in good shape. This is the slow
part — budget for several iterations, not one measurement. When a regression shows up but its cause
isn't obvious from either script's output, that's the point to reach for a kernel micro-benchmark
(see step 2) rather than guessing and re-measuring blind.

## 6. Open the PR

Use `.github/PULL_REQUEST_TEMPLATE.md`'s Performance section: a `Benchmark | Before | After |
Change` table, geometric mean, and which corpus was used — or, if step 3 captured a memory
baseline, the template's own note to swap that table for a before/after memory profile instead
(or alongside it, if both moved). Link the design doc's issue number if
one was filed.

Report before/after in the **most natural unit for the magnitude** — if the numbers are
sub-millisecond, use μs, not `0.088 ms` (leading-zero decimals bury the scale and misread at a
glance). Every before/after table (in the PR body and the docs) must, without exception:

- Put the unit **in the column header** (`off µs` / `Before (μs)`), never in a caption or repeated
  per cell — a reader scanning the table sees the numbers, not the prose around it.
- Use the **same unit on both sides** of a comparison, picked from the smaller value. (`Change`/speedup
  is a ratio, so it's unitless.)
- Include a **`rows`/`total` parity column** (identical across builds — the cheap correctness check)
  and, when several queries move, a **geometric-mean speedup** over the impacted rows so the summary
  is one honest number rather than a cherry-picked max.

Examples of this workflow end-to-end: PR #663 (oracle word index — a kernel micro-benchmark
diagnosed and fixed a broad-survey regression the other two tools could only detect, and a memory
baseline turned up an unplanned 14% archive shrink), #659 (numeric-range bitplanes), #658
(exactness propagation), #654 (legality bitplanes), #646 (name-sort permutation — the PR that
added the wild corpus's exact-name cap described above), #639 (bigram index, broad-screen
percentile table).

## Related

`docs/issues/local-query-benchmark-suite.md` proposes formalizing corpus generation further (a
`--generate-corpus` flag) — unimplemented; the manual export in step 2 is the current state.
