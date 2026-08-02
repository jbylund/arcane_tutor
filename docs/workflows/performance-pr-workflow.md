# Performance PR Workflow: Design Doc to Merge

Applies to changes justified by "queries get faster" or "memory footprint shrinks" — engine kernel
work, index/bitplane additions, cost-model changes, SQL generation. Correctness-only fixes don't need
a benchmark story; see [differential property tests](https://github.com/jbylund/sylvan_librarian/pull/641)
for how those are verified instead.

## 1. Write the design doc

Start (or refine) a doc at `docs/issues/<name>.md`, shaped by the
[performance issue template](../../.github/ISSUE_TEMPLATE/performance.md): measured problem (with the
protocol that produced the numbers), where the cost is, proposed approach, and acceptance.

**These docs are tracked and this repo is public.** Everything under `docs/issues/` is committed —
the sole exception is `docs/issues/security-*`, which is gitignored. Read
[docs/issues/README.md](../../docs/issues/README.md) before writing anything you would not publish.

For acceptance, name **which of the four measurement layers below** proves the change, and for
executor work name **which physical plan** you are changing. That decides whether an end-to-end
measurement can see it at all.

If the design claims a predicate is exact/tight, check that against the composition invariants
*before* writing code: `Not` only narrows through tight children, and `And`/`Or` of same-space tight
sets is assumed to stay tight. A predicate exact in isolation but not once ANDed with another over
the same non-card-invariant domain is a silent correctness bug, not a missed optimization — it
produces wrong *results*, and no benchmark will surface it.

## 2. The four measurement layers

With a cost-based router in front of six physical plans, "the query got faster" is four separate
questions, and a number from one layer cannot answer another. Reaching for the wrong layer is the
most common way a real win reads as noise.

| # | question | tool |
| --- | --- | --- |
| 1 | Are the model's **features** right — do the estimates match what the executor did? | [`bench_feature_accuracy.py`](../../scripts/bench_feature_accuracy.py) |
| 2 | Are the **plan costs** right — does predicted match measured? | [`bench_cost_error_percentiles.py`](../../scripts/bench_cost_error_percentiles.py) |
| 3 | Did a **plan's execution** get faster, whether or not the router picks it? | [`bench_plan_execution_ab.py`](../../scripts/bench_plan_execution_ab.py) |
| 4 | Did the **end user** get a faster answer? | [`bench_query_latency_ab.py`](../../scripts/bench_query_latency_ab.py), [`survey_queries.py`](../../scripts/survey_queries.py) |

Layers 1 and 2 are diagnosis; 3 and 4 are acceptance. All of them share one core,
[`scripts/costbench.py`](../../scripts/costbench.py) — the sampling loop, the nearest-rank
percentiles, and the single definition of "what this plan costs to run" (`plan_self_ns`). Use it for
anything new rather than starting a twelfth private copy.

For diagnosing *which* of features / model shape / coefficients is at fault once layer 1 or 2 shows
an error, and for the regret and pairwise-ordering views, see
[reference-cost-model-measurement.md](../../docs/issues/reference-cost-model-measurement.md). That is
the tool-picking reference; this doc is the process.

### Layers 1 and 2: read the shape, not the median

Both print nearest-rank percentiles from p0 to p100, sliced by plan, by distinct-on, by acquire
branch, and by orderby. **Slice before you conclude** — errors cancel when pooled, and the
cancellation has hidden the largest routing defect in the engine: compose read 0.81 on artwork and
1.15 on printing, pooling to "roughly fine" while the two drove opposite routing errors.

p0 and p100 are the real min and max. They earn their columns because estimates of zero cards and of
every card are both common, and they are exactly where an arm's shape breaks down — a recent run read
p99 = 7.1 and p100 = 232 on the same cell.

### Layer 3: did the executor get faster

The layer end-to-end latency cannot answer, because a `query()` delta is the sum of two independent
effects: how fast the executor is, and which executor got picked. An executor change lands in one of
three places, and only this layer separates them:

- Faster **and still picked** — a real win, visible end to end.
- Faster **but never picked** — a latent win. Layer 4 moves 0%, and a PR quoting only the survey
  concludes, wrongly, that nothing happened.
- A `cost::plan_cost` input moved, so the router picks **differently** — layer 4 moves, and none of
  the delta is the executor's.

```bash
# once per build, same corpus, mode and seed on both sides
.venv/bin/python scripts/bench_plan_execution_ab.py --sample 600 --out /tmp/main.jsonl
.venv/bin/python scripts/bench_plan_execution_ab.py --compare /tmp/main.jsonl /tmp/branch.jsonl
```

It pairs per (query, plan), reports acquire and the routed path alongside the plans, excludes pairs
whose `result_total` disagrees, and says separately whether routing moved.

### Kernel micro-benchmarks: below all four layers

For representation-level questions no query-level measurement can resolve — which of two algorithms
wins for one subroutine, or *why* a layer-4 regression happened once you know one exists. Follow the
existing pattern (`bench_mana.rs`, `bench_word_dict_scan.rs`, `bench_posting_intersect.rs` and the
six others in `card_engine/src/`): a `#[cfg(test)] mod bench_<name>;`, an `#[ignore]`d test over
`benchmarks/verify-order/real.store` that asserts every contender agrees on real data before timing
any of them, run with `cargo test --release bench_<name> -- --ignored --nocapture`. This is what
diagnosed and fixed the regression in #663.

## 3. Baseline on main

Record a baseline with `--out` from a `main` build before touching anything —
`git worktree add <path> main` is the clean way to get one without disturbing your branch. The
`benchmarks/` tree is local scratch: untracked, though **not** gitignored, so don't `git add -A`.

If the change touches archive layout, also capture memory: build with `--features alloc-counter` and
read `QueryEngine.mem_stats()` after loading the same corpus (`archive_bytes`, `indexes_rkyv_bytes`,
`reload_peak`). Easy to skip, since none of the four layers touches it — and #663 turned out to
shrink the archive 14%, discovered only after the PR was up because nobody had asked.

## 4. Implement, with correctness first

Add unit and differential tests before chasing speed. `result_total` doubling as a parity check
(identical across builds) is the cheap way to catch a change that is fast because it is wrong;
layer 3 enforces it for you. Self-review the diff before re-measuring.

## 5. Re-measure — and run the canary first

Re-run the same layers against the branch build, same corpus and seed.

**Before trusting any cross-build number, compare a build against ITSELF.** Same commit, same seed,
two runs. It should report no detectable difference. Both A/B harnesses failed that canary at the
toolkit's default 7 trials:

| harness, same build and seed | at 2w/7t | at 6w/30t |
| --- | --- | --- |
| `bench_plan_execution_ab.py` | every plan, acquire and routed "SLOWER" by 4–9%, "faster on 0" | no detectable difference |
| `bench_query_latency_ab.py` | "B is FASTER", −1.0 µs, CI [−1.6, −0.5] | +0.5 µs, CI [−1.0, +2.9] |

The cause is not the machine. `min` over the trials is a floor estimator, and how far above the floor
it lands depends on the interference that run saw. **That error is common-mode across every query in
a run, so pairing and a wider sample do not cancel it** — the latency canary above had 388 paired
queries and still called it. Depth is what fixes it, which is why both A/B tools default to 30 trials
while the within-call diagnostics stay at 7 (see `costbench.py` for the convergence table).

`bench_plan_execution_ab.py` also treats acquire as a **control**: most executor changes don't touch
it, so if it moves, the two runs are not comparable and the tool says so and prints an adjusted
column. Raise `--trials` before believing anything it flags.

Interleaving A/B/A/B and quiescing the machine are still worth doing against genuine long-run
thermal drift. They were not what these two canaries needed.

Never compare two headline means. Latency and regret are heavy-tailed enough that the same engine and
seed have produced 0.26 and 0.82 µs on consecutive runs. Pair over identical observations and report
the bootstrap interval.

## 6. Open the PR

Use `.github/PULL_REQUEST_TEMPLATE.md`'s Performance section. For an executor change, state
acceptance as both halves: **the plan is faster, and the router still routes to it.** A win the
router declines to use is a latent win — say so plainly rather than quoting a layer-4 delta that came
from somewhere else.

Every before/after table, without exception:

- Unit **in the column header** (`Before (µs)`), never in a caption or repeated per cell.
- **Same unit on both sides**, picked from the smaller value, at the natural magnitude — `88 µs`,
  not `0.088 ms`. (Ratios are unitless.)
- A **`rows`/`total` parity column**, identical across builds.
- A **geometric mean** over the impacted rows when several queries move, so the summary is one honest
  number rather than a cherry-picked max.

Examples end to end: #663 (oracle word index — a kernel benchmark diagnosed a regression the
query-level tools could only detect, plus an unplanned 14% archive shrink), #659 (numeric-range
bitplanes), #658 (exactness propagation), #654 (legality bitplanes), #646 (name-sort permutation),
#639 (bigram index).
