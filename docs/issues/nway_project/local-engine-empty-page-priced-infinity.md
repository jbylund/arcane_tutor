# The router prices `EmptyPage` at INFINITY and never picks the plan that would win

`cost.rs` prices `ComposePaging::Decline` at `f64::INFINITY`, so a plan predicted to refuse is kept out of the argmin entirely — "routing to a plan that returns `None` pays the detour and then runs something else anyway". That is right when the plan really refuses. **It is wrong when the executor's actual exit is `EmptyPage`**, which returns the correct answer faster than anything else.

The two are decided by different numbers: `compose_paging` predicts from the acquire's **estimate** of the total, while the fastpath branches on the **realized** one.

## What it costs

Measured with `scripts/bench_pick_quality.py`, uniform sampler, 8,000 queries, 5,366 with two or more plans timed:

| slice | n | hit% | time-weighted hit% | share of all routing loss |
|---|---|---|---|---|
| normal | 4,447 | 95.8% | 89.7% | 79.0% |
| **empty result** | 632 (11.8%) | 91.5% | **68.8%** | **9.3%** |
| **offset past the end** | 296 (5.5%) | **66.9%** | **34.0%** | 11.8% |

A worked case, verified by forcing all three plans (`set:tmp frame:1993`, printing/cubecobra/off=100): compose 7 trials, **0 declines**, `paging_taken = EmptyPage`, **1,417 ns**; GatheredScan 42.5 us; StreamedSelect 38.5 us. `result_total = 0` for all three. The router picked GatheredScan because compose's predicted cost was `inf`. Every one of the ten worst mis-picks BY RATIO has this exact signature.

## The two sub-cases are not equally real, and only one is worth scheduling

- **Empty result** (632 rows, 11.8%) — the query genuinely matches nothing. Representative of real traffic; a user typing a filter that matches no card is ordinary.
- **Offset past the end** (296 rows, 5.5%) — the query matches rows but the requested page starts beyond them. `watermark:mps` returns **5** artworks and the harness asks for offset 100. **This cell is inflated by the sampler**: `costbench.OFFSETS` draws `offset=100` on a quarter of queries regardless of result size, whereas real traffic reaches offset 100 only by paging there, having seen results. Its 66.9% hit rate is the worst number in the table and the least trustworthy.

So the schedulable claim is the first: **~12% of queries, 9.3% of routing loss, at a 68.8% time-weighted hit rate.** Re-running the sampler with a realistic offset distribution would say how much of the second survives outside the harness, and should precede any work aimed at it.

## Same gate, the other direction

Round 80's completeness audit found the mirror failure: **74 queries where compose WAS picked and then refused after paying the entire build** — composing `pbits`, projecting `card_bits`, popcounting — before `return None`. `declined_ns` p50 **17.2 us**, p90 44.3 us, **summing to 11.66 ms = 3.59% of all picked-plan measured time**, thrown away before the fallback starts.

Both directions are the same root cause. One wastes a build; the other excludes the fastest plan. Any fix should address both or explain why not.

## What a fix looks like — and what it is not

**It is not a cost-model change.** `plan_cost` cannot see that the page is empty: `matches` is an estimate and the branch is on the realized total. No rate or feature reaches this.

Two candidate shapes, neither costed:

1. **Stop returning INFINITY where the real exit would be `EmptyPage`.** The gate would need to distinguish "will refuse" from "will return an empty page fast", which today's `ComposePaging` collapses into one `Decline`.
2. **An exact zero-result / past-the-end test before costing.** `offset >= total` is trivially checkable IF the total is known exactly, which is the whole difficulty — the acquire has an estimate. But note the estimator is now exact at the median on every acquire route (Round 77: median |log| **0.000**), so an exactness FLAG rather than a better estimate may be enough.

Option 2 is attractive because several acquire branches already compute an exact total (`exact_result_total`, the `EXACT_VALUE_TOTALS` path) and the information may already be present at the decision point.

## Evidence trail

Round 85 and Round 86 in [local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md); raw output in [measurements/2026-09-05-pick-quality-uniform.txt](measurements/2026-09-05-pick-quality-uniform.txt) and the two `--worst-by` dumps beside it. Round 80's decline-side measurement is in that same ledger.
