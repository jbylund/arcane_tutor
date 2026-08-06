# `PlanePopcountOrder`'s Under-Costing: Re-Measure, and Instrument `popcount_words` First

Status: open, not started. Filed as
[#854](https://github.com/jbylund/sylvan_librarian/issues/854). The one calibration finding left over from
[the decline-fallback work](done/local-engine-plan-misselection.md), which otherwise shipped in #805. That doc
called this *"real and unexplained"*, and it is the last of its items without a home — the other three route to
[#852](00852-engine-compose-acquire-p3-p4-ranking.md),
[#853](00853-engine-interior-range-distinct-counts.md), or are deliberately parked.

## What was measured

Both figures are `measured / predicted`, the convention `bench_plan_misselection.py` prints, so **> 1 means
under-costed** — the model thinks the plan is cheaper than it is and over-picks it.

| scan | n | median | tail |
| --- | --: | --: | --: |
| 200 generated queries | 36 | **2.44** | worst 8.69 |
| 2,500-query scan | 157 | **1.61** | p90 6.16 |

It materializes nothing, so netting the acquire is a no-op and raw and net agree. That is what made it worth
keeping: every other apparent defect in that scan dissolved under netting — `GatheredScan` went 6.47 raw to
0.84 net, and *"anyone reading the raw column alone would go and 'fix' a calibrated arm"* — and this one did
not.

## It has probably narrowed, and that needs confirming rather than assuming

`CARD_RANGE_BUILD_PER_PRINTING_NS`'s doc comment (`card_engine/src/cost.rs`), written in #813, records
PlanePopcountOrder as *"slightly UNDER-costed at 0.92"*.

**Mind the convention — the tree uses both directions.** That comment's ratios are `predicted / measured`: it
calls the arm *"over-costed by a near-uniform 1.20"* and fixes it by **lowering** the rate 1.22 → 0.93, and one
only lowers a rate that over-predicts. Converted, its 0.92 is `measured / predicted` ≈ **1.09**, against the
1.61 above.

So #813's refit closed most of the gap. Two reasons not to call it done:

1. **It is a passing remark, not a re-measure.** The 0.92 appears in *another constant's* doc, as the argument
   for why shared constants cannot absorb `CardRangePopcount`'s error. It is not an n=157 sweep, and the
   population behind it is plausibly narrower — bare ranges at `unique=card`, which is where
   `CardRangePopcount` lives.
2. **The measurement basis moved, not just the constants.** [#833](https://github.com/jbylund/sylvan_librarian/pull/833)
   re-based regret on dispatch and made plane rows dispatch-comparable *specifically because plane rows were
   getting negative regret* — the routed path reuses an artifact a forced trial rebuilds. Every plane-row
   number taken before that is suspect in a way a rate refit does not address.

## There is a feature question underneath, and it should be settled first

[#852](00852-engine-compose-acquire-p3-p4-ranking.md)'s oracle result is that features come before rates. This
arm has a feature that has never been checked against anything.

Its cost is four terms (`card_engine/src/cost.rs`):

```rust
PhysicalPlan::PlanePopcountOrder => {
    matches * PLANE_POPCOUNT_SCATTER_PER_MATCH_NS
        + (n_cards / 64.0) * PLANE_POPCOUNT_PER_WORD_NS
        + limit * PLANE_POPCOUNT_EMIT_PER_CARD_NS
        + PLANE_POPCOUNT_FIXED_COST_NS
}
```

`(n_cards / 64.0)` charges the **whole corpus in words, every time**. The executor's skip-scan stops once it
reaches the page offset, so the words actually scanned depend on page depth: a shallow page should scan far
fewer, a deep one more.

**Hypothesis, not a measurement.** If that is the mechanism, this is a feature error rather than a rate error,
and it would produce exactly the observed shape — a median near 1 with a fat tail (p90 6.16), the tail being
deep pages. Worth stating because it is falsifiable in one breakdown, and because a rate refit would paper over
it while leaving the tail.

## The prerequisite is one counter at one site

[local-engine-instrument-fast-paths.md](local-engine-instrument-fast-paths.md) item 1 specifies it already, and
it is the cheapest step in that doc: counter the word passes in `run_query_streamed_popcount` — the single
function both `PlanePopcountOrder` and `CardRangePopcount` delegate to — reported against `popcount_words`, **a
cost feature that already exists**. One site closes two of the four uninstrumented fast paths, against a
feature needing no new plumbing. [#798](https://github.com/jbylund/sylvan_librarian/issues/798) tracks the
wider instrumentation gap; item 3 there is the compose half.

`plan_stats_never_leak_between_participants` currently asserts the **opposite** contract — that these four
plans report zeros — and does so deliberately, to pin today's behaviour so that instrumenting one is a decision
someone makes on purpose rather than a silent change in what a zero means. Expect to update it, one plan at a
time.

## The coupling that makes a blind refit expensive

Three of the four constants above are **shared with `CardRangePopcount`**. That is precisely why its own retune
had to move `CARD_RANGE_BUILD_PER_PRINTING_NS` — 80% of its error, and unshared — rather than touch the shared
terms: *"Its four other constants are shared with PlanePopcountOrder… so they cannot absorb it."*

So re-fitting this arm's rates moves `CardRangePopcount` too, and that arm is currently calibrated. A feature
fix does not have that problem, which is a second reason to instrument before fitting.

## Order

1. **Land the `popcount_words` counter** (instrument-fast-paths item 1).
2. **Re-measure on a larger sample**, on post-#833 regret basing. **Expect this to come back near 1.0** — if it
   does, close it and record the figure, because the open item then costs more to carry than to settle.
3. **Only if a real gap survives:** decide feature vs rate from the page-depth breakdown, not a pooled median.
   Every pooled figure in this line of work hid structure that only showed up per cell — the parent doc lists
   three methodology traps that each produced a confidently wrong answer.

## Reproducing

```bash
.venv/bin/python scripts/bench_plan_misselection.py --source distribution --out A.jsonl
.venv/bin/python scripts/bench_plan_misselection.py --compare A.jsonl B.jsonl   # the only real verdict
.venv/bin/python scripts/bench_cost_model_agreement.py --seconds 300            # the per-cell bar
```

The parent doc's method notes still apply and are the reason its figures are quotable: min of 15 trials after 3
warmups, plans rotated each round, every plan from a fresh `filter.clone()` so each pays `memoize_text_predicates`
identically, and queries where the best measured plan *is* the picked plan excluded. Misses under ~5 µs flip
run to run; quote only the larger ones.

## Related

- [done/local-engine-plan-misselection.md](done/local-engine-plan-misselection.md) — the shipped fix, the
  mis-costing scan this came from, and the three methodology traps.
- [local-engine-instrument-fast-paths.md](local-engine-instrument-fast-paths.md) — the counter, and why the
  four fast paths' zeros are zero-by-definition rather than zero-by-omission.
- [#852](00852-engine-compose-acquire-p3-p4-ranking.md) — features before rates, established by oracle run.
- [#853](00853-engine-interior-range-distinct-counts.md) — shares the `card_range_popcount` acquire, where the
  parent doc's thin-sample `StreamedSelect`/`GatheredScan` finding (~2.4×, n=13) also lives.
