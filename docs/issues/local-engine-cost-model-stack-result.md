# What the cost-model stack measured, end to end

The eight PRs from #804 to #816 against `origin/main`, on the query distribution the work was
targeted at. Recorded here because the individual PRs each report a component metric — agreement,
feature-vs-counter ratios, paging-prediction rates — and none of them answers "did queries get
faster".

## The measurement

`bench_query_latency_ab.py`, uniform weighting from
[`query_sampler.py`](../../scripts/query_sampler.py), 120,000 sampled queries per side, 1 warmup and
3 trials each, **interleaved A/B/A/B** across two reps with a separate store per side. Two reps
because one number is not a measurement here: at `--sample 400` the same engine and seed has
produced 0.26 and 0.82 µs on consecutive runs.

Uniform rather than realistic on purpose. Realistic weighting is what users wait for, but it
under-samples the tails where selectivity extremes live and where plan choice actually changes;
uniform reaches them. The tradeoff is that the headline mean is not a traffic-weighted number.

## Result

    rep                     origin/main      stack        delta            95% CI
    1                          85.8 us      81.8 us      -3.95 us   [-4.23, -3.65]
    2                          87.8 us      83.9 us      -3.89 us   [-4.20, -3.62]

    median ratio (stack/main)                              0.992 / 0.986

The two reps agree to 0.06 µs, which is the part worth trusting. Earlier measurements in this work
disagreed between reps by 3.5 µs and were read as signal before the disagreement was noticed.

### By distinct-on

    unique        rep1              rep2
    printing     -9.78 us (-12.3%)  -10.90 us (-13.2%)
    artwork      -1.93              -0.73
    card         -0.09              -0.05

### By cost band (banded on origin/main's time)

    band            rep1 mean A -> B        rep2 mean A -> B
    <10 us          7.1  ->  7.3  (+0.19)   7.2  ->  7.3  (+0.11)
    10-50          25.2  -> 24.7  (-0.50)  25.3  -> 24.8  (-0.59)
    50-200        102.8  -> 99.2  (-3.65) 103.6  -> 100.0 (-3.61)
    200-1000      384.1  -> 370.7 (-13.4) 388.2  -> 376.0 (-12.3)
    >=1000       1219.6  -> 968.4 (-251) 1226.0  -> 988.9 (-237)

## Reading it honestly

**The median ratio is below 1.** That matters more than the mean: it says the typical query got
faster, not just that the tail did. An intermediate version of this work sat at 1.02-1.03 — a mean
improvement carried entirely by expensive queries while ordinary ones were slightly worse.

**Card mode is flat, and that is a fix, not a null.** It carried a reproducible +2% through every
measurement of the earlier branch. The cause was `scan_units()`'s exact sum being O(candidates) —
which printing/artwork already paid but card mode did not — so correcting the feature's VALUE also
added ~14 µs of acquire to every broad card query (`border:black` 29.8 -> 15.5 µs once it took the
O(1) projection instead).

**The tail is where the routing work shows up.** The >=1000 µs band improves ~20%, and it is 0.5% of
queries. A mean over the whole distribution understates that and a p99 overstates it, which is why
the bands are here.

**The <10 µs band is marginally worse** (+0.1 to +0.2 µs), consistently across reps. Small enough
that it could be added planning work or could be noise at that scale; it was never attributed.

## What this does not say

- Not traffic-weighted. Uniform over-samples rare shapes by construction.
- Not a claim about any single PR. The stack was measured at its tip; component PRs report their own
  metrics and several were measured to be neutral end to end on their own.
- Not a p99. Heavy-tailed distributions make percentile claims fragile at this sample size; the bands
  above are the honest version of the same question.
