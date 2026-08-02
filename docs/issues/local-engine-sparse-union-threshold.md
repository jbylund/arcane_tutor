# Pick the oracle-word sparse union by matched-word count

The oracle-word index's sparse expansion unions one posting row per matched dictionary word.
Two strategies are possible and each wins a different regime; today the engine ships one of them
unconditionally, and it is the right one for ~99% of needles. This doc records the measured
crossover in case the other regime ever becomes worth serving.

Spun out of #811 (item 16 of [00799](00799-engine-simplicity-pass.md)), where the rewrite was
proposed as a pure simplification, measured as a regression, and reverted.

## The two strategies

`narrow_rec`'s `TextContains { field: OracleTextLower }` arm, `sparse_text_ids`:

- **Fold** (shipped): `ids = union_sorted(ids, row)` once per matched word. Each step is an
  O(|ids| + |row|) merge that reallocates the accumulator, so total work is quadratic in the
  *number of matched words* `w` — the classic repeated-merge shape.
- **Concatenate + sort**: `extend` every row, then one `sort_unstable` + `dedup`. Linearithmic in
  total postings, one growth-amortized allocation.

Same output either way: `union_sorted` dedups on equal, and so does `dedup`.

## Measurement

`cargo test --release bench_narrow_alloc -- --ignored --nocapture`, section B — real dictionary
from `benchmarks/verify-order/real.store`, best-of-300, times per call.

| needle | words matched | postings | fold | sort+dedup | fold ÷ sort |
|---|---|---|---|---|---|
| hexproof | 1 | 297 | 45 ns | 184 ns | 0.24× |
| sacrifice | 2 | 585 | 447 ns | 2219 ns | 0.20× |
| creature | 3 | 1086 | 1085 ns | 4555 ns | 0.24× |
| equip | 4 | 1748 | 1859 ns | 7898 ns | 0.24× |
| block | 7 | 2959 | 8554 ns | 13401 ns | 0.64× |
| enchant | 8 | 4202 | 8985 ns | 20791 ns | 0.43× |
| land | 18 | 1940 | 7600 ns | 8397 ns | 0.91× |
| king | 19 | 1393 | 8246 ns | 5856 ns | 1.41× |
| lock | 22 | 3102 | 25880 ns | 14059 ns | 1.84× |
| less | 23 | 3158 | 13912 ns | 15208 ns | 0.91× |
| fire | 34 | 126 | 2207 ns | 535 ns | 4.12× |
| ring | 38 | 1188 | 13142 ns | 5146 ns | 2.55× |

The crossover is governed by `w`, not by total postings — `fire` matches 34 words holding only 126
postings between them and still favors sort+dedup by 4×, while `enchant` matches 8 words holding
4,202 postings and favors the fold by 2.3×. Around `w` = 18-23 the two are within noise of each
other in either direction, depending on whether a long row lands early in the fold (which inflates
the accumulator for every later step) or late.

## Why the fold ships

Sweeping every eligible sparse dictionary word as its own needle (each is a legal query — `o:ring`
is how a user reaches this path), 6,246 needles:

| words matched | needles | share |
|---|---|---|
| 1 | 4,892 | 78.3% |
| 2-3 | 988 | 15.8% |
| 4-7 | 291 | 4.7% |
| 8-15 | 61 | 1.0% |
| 16-31 | 12 | 0.2% |
| 32+ | 2 | 0.0% |

98.8% of needles land at `w` ≤ 7, where the fold wins by 2-5×. Fourteen needles reach `w` ≥ 16.
Switching unconditionally costs up to 11.8 μs on `o:enchant` to save up to 8 μs on `o:ring`.

## The proposal, if it is ever worth it

Branch on `sparse.len()`: fold below a threshold, concatenate + sort above it. From the table the
threshold belongs around 24 — above the ambiguous 18-23 band, below the clear wins at 34+.

Worth doing only if the needle mix shifts (a UI that generates wide fragment needles, or a
tokenizer change that grows the dictionary's substring overlap). As of this measurement it buys a
few microseconds on 0.2% of an already-microsecond path, in exchange for a tuned constant on a code
path whose current form is one loop. `bench_narrow_alloc.rs` section B already benches both
contenders and reports the needle distribution, so re-deciding is a single command.

## Acceptance

- `bench_narrow_alloc.rs` section B shows no regression at `w` ≤ 7 against the fold's numbers above.
- The `w` ≥ 32 rows (`fire`, `ring`) improve by at least 2×.
- The threshold is a named constant with the measured band in its comment, per the project's
  measured-constant convention (`STREAM_MIN_MATCHES`, `MAX_NARROW_FRACTION`).
