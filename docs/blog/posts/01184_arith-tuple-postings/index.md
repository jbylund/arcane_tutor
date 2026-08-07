---
title: "We Indexed 564 Stat Lines Instead of Expressions, and Arithmetic Queries Got 3.4-45x Faster"
date: 2027-03-16
publishDate: 2027-03-16
tags: ["rust", "query-engine", "performance", "indexing"]
summary: "Our search DSL lets users write arithmetic across card fields — power+toughness<4, cmc*3+1<power+toughness — and none of it had an index arm: every such query evaluated the expression once per card, 31,508 times. But those four columns hold only 564 distinct joint values, so we interned the tuples and built tuple-to-card postings. Any expression over those columns is now 564 evaluations plus a postings union: 3.6x faster on the broadest query the index helps, 45x on the most selective, for 135 KB of archive."
---

Four queries against the same two columns, on the same 31,508-card store, before this change:

| query | matching cards | min µs |
|---|---:|---:|
| `power+toughness<20` | 17,242 | 416 |
| `power+toughness<4` | 3,975 | 417 |
| `power+toughness>13` | 393 | 431 |
| `power+toughness>30` | 2 | 361 |

The result set shrinks by a factor of 8,600 and the runtime moves 19%.
That is the signature of a predicate with no index behind it: the engine evaluated `power+toughness` once per card, 31,508 times, then threw away everything that didn't match.
The fix was to index the *inputs* instead of the expression.
The four numeric columns our query language lets you do arithmetic on take only 564 distinct joint values across all 31,508 cards, so [PR #750](https://github.com/jbylund/sylvan_librarian/pull/750) evaluates any expression a user writes 564 times and unions postings — which makes the last row of that table 45x faster.

Sylvan Librarian's query DSL extends Scryfall's with arithmetic — `power+toughness<4`, `cmc+1<power`, `cmc*3+1<power+toughness` — over the card-level integer fields `cmc`, `power`, `toughness`, and `loyalty`.
The engine answers a query by *narrowing* to a candidate set from whatever index fits the predicate and then verifying the remainder per candidate, and most predicate shapes had picked up a narrowing arm over the preceding year: postings for `set:`/`watermark:`, [bitplanes](../01088_transposed-bitplanes/) for colors and types, [sorted range arrays](../00992_index-selectivity-crossover/) for single-column numerics and prices.
Arithmetic had none, because there is no obvious thing to index: the user writes the expression at query time, and there are infinitely many expressions.

There are not infinitely many inputs.

## 31,508 Cards Have Only 564 Distinct Stat Lines

Magic's numbers are chosen by designers for a game, not drawn from a distribution.
Counting the corpus the benchmarks below use, 1,052 cards are a 2/2 for three mana.

```sql
select count(*) from (
  select cmc, creature_power, creature_toughness, planeswalker_loyalty
  from magic.cards group by 1, 2, 3, 4
) t;
-- 564
```

564 distinct `(cmc, power, toughness, loyalty)` tuples — a card's whole stat line, as far as the query language is concerned — across 31,508 cards, a 56:1 collapse, and the count is the same whether you run it against the live database or count it in the corpus export the benchmarks use.
One thing to watch if you run that query yourself: `magic.cards` is printing-grained, so it groups 97,206 rows, not 31,508.
All four fields are card-level, so the distinct count comes out the same either way — but the denominator of that 56:1 is the 31,508 distinct `oracle_id`s the engine calls cards, not the row count of the table.
Drop loyalty and it is 531 triples.
The distribution is as lopsided as you would guess: the single largest entry is `(cmc 2, no power, no toughness, no loyalty)` — every two-mana noncreature spell in the game, 3,210 cards under one key — and 179 of the 564 entries cover exactly one card each.

That number is the whole idea.
Any predicate that is a pure function of only those four fields — and every arithmetic expression the DSL can build over them is — has the same answer for every card sharing a tuple.
So evaluate it 564 times, not 31,508 times, and map the winning tuples back to cards through postings.

The build step is a dictionary and an inverted index — the same [interning](../00640_string-interning-rust/) the engine already applies to low-cardinality single columns like set codes, [pointed at a tuple instead of a scalar](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/lib.rs#L1507-L1526):

```rust
// simplified: the real version reuses the existing interner conventions
for (i, c) in cards.iter().enumerate() {
    let key = ArithTupleKey { cmc: c.cmc, power: c.creature_power,
                              toughness: c.creature_toughness,
                              loyalty: c.planeswalker_loyalty };
    let id = *interner.entry(key).or_insert_with(|| { /* push key + empty postings */ });
    postings[id].push(i as u32);
}
```

Each field is `Option<u8>` or `Option<i8>`, so Rust's derived `Hash`/`Eq` handles missing values (an instant has no power) with no sentinel encoding — `None` is just another value the dictionary interns.
Cards are visited in ascending id order, so every postings row comes out sorted for free.

The two builds' store files, written from the same corpus, are 71,745,596 and 71,880,672 bytes: the whole index costs **135,076 bytes**, +0.19%.
(The PR reports +135,068 from the engine's own allocation counter, which excludes the file header — same number, different tape measure.)
That is 564 keys plus one `u32` per card, and it is the only new memory the change spends.
It is also invisible at build time: loading the corpus and committing the store took 2.2–2.3 s on both revisions across all six runs, because one hash lookup per card disappears into the JSON parsing it rides along with.

## Evaluating the Predicate 564 Times

The narrowing walks the key array, evaluates the user's expression against each tuple's four values, and unions the postings of the tuples that came out true — [`arith_tuple_narrow`](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/lib.rs#L1537-L1574):

```rust
// simplified: the real loop widens each Option<u8>/<i8> to Option<f64> first,
// exactly as the per-card field fetch does
for (t, key) in idx.keys.iter().enumerate() {
    if eval_arith_tuple_tri(lhs, *op, rhs, key.cmc, key.power,
                            key.toughness, key.loyalty) == want {
        matched.push(t);
        count += idx.postings[t].len();
    }
}
```

Two properties make this worth more than a normal narrowing.

It is **exact**, not a superset.
Every one of the four fields is card-level — identical across all printings of a card — so a tuple's verdict is the card's verdict, and the candidate set needs no per-row recheck.
The engine tracks that as `Narrowed::tight`, which propagates into [`all_match_known`](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/lib.rs#L5278) and deletes the per-candidate verification pass outright.
Most narrowings in this engine are filters that reduce how much work the verifier does; this one *replaces* the verifier.

And it is **cheap in a way that does not depend on the query**.
564 iterations of four-field arithmetic costs a handful of microseconds — about 5 µs, measured below — no matter what the user typed.
The one term that does grow is the postings union, and it grows with the number of *matching* cards rather than with the corpus.
Above 4,096 covered cards the union switches from a sorted vector to a `CardBits` bitmap built by an O(count) scatter, because a broad set's next operation is a word-wise `AND` with a sibling filter, not a merge.

## The Evaluator Had to Be Shared, Not Copied

The tempting implementation is a small self-contained evaluator over the tuple's four fields — twenty lines, no risk of touching the hot per-card path.
It was rejected because the engine already had exactly this recursion, including SQL-style NULL propagation and divide-by-zero-to-NULL, in `NumExpr::eval`, and a second copy of it would drift.
The alternative was to make the existing one generic over how it fetches a field:

```rust
#[inline(always)]
fn eval_with<F: Fn(NumField) -> NumVal>(&self, fetch: &F) -> NumVal {
    match self {
        NumExpr::Const(v) => NumVal::Known(*v),
        NumExpr::Field(f) => fetch(*f),
        NumExpr::Arith(lhs, op, rhs) => Self::eval_arith_with(lhs, *op, rhs, fetch),
    }
}
```

The per-card path passes `&|f| field_num(card, printing, f)`; the tuple scan passes a closure over one key's four values.
Both reach the comparison through one [`numeric_cmp_tri`](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/filter.rs#L183-L190), so there is one definition of what `NULL + 1 < power` means.

This only works because of a detail already load-bearing in that file: `eval_with` is non-recursive, delegating the `Arith` case to a separately named `eval_arith_with`.
That split exists because LLVM's always-inliner refuses to inline *any* self-recursive function at *any* call site — an earlier attempt at putting `#[inline(always)]` on a still-recursive `eval` left both `bl NumExpr::eval` calls sitting in the release disassembly.
Keeping it lets monomorphization fold the generic version back into the machine code the hand-written match produced.

Whether it did is measurable on exactly one row of the benchmark below, and the answer is "almost."
`usd+1<power` fails the eligibility check two sections down, so it gets no narrowing on either build and evaluates `eval_with` → `eval_arith_with` 31,508 times per query.
That makes it the one clean before/after comparison of the per-card evaluator in the whole sweep — `cmc>=cmc` also ends up scanning, but only after the new build has already paid for a narrowing it throws away, so it measures two things at once.
It came out 2.3%, 0.7% and 1.6% slower on the after build across the three rounds.
Those are small numbers, but they are all the same sign, in rounds whose controls drift both ways, so the honest reading is that handing the recursion a closure costs the arith path a percent or two rather than nothing.
The leaf case — a bare comparison with no arithmetic in it, which is what the `#[inline(always)]` is actually for — I cannot isolate here at all.
Every bare numeric in my sweep resolves through its own index arm and never evaluates a predicate per card, so the flat `power>4` and `cmc>6` controls say nothing about codegen either way.
For that path the evidence is the PR's broad survey, whose p50 and p75 held unchanged, and not anything in my tables.

And the agreement between the two paths is a [test](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/tests.rs#L2642-L2731), not an argument in a comment: across four fixture stores, every comparison operator, both polarities and six predicate shapes, the narrowed set must be byte-identical to what the per-card reference produces.

The negation arm follows the same "don't write a second implementation" rule, but the payoff is correctness rather than codegen.
`-power+toughness<4` — a leading `-` is Scryfall's negation operator, not arithmetic on `power` — re-runs the identical 564-tuple scan collecting `Tri::False` instead of `Tri::True`.
It never complements a candidate set, which matters because a complement over nullable columns includes the NULL rows, and a NULL row must fail a predicate *and* its negation.
The [previous PR in this thread](https://github.com/jbylund/sylvan_librarian/pull/741) needed three separate fixes around that trap.
Recomputing from scratch makes the whole class unreachable: a tuple whose power is `None` evaluates to `Tri::Null`, which matches neither the `Tri::True` the positive arm asks for nor the `Tri::False` the negation asks for, so it is excluded from both polarities by construction.

## What the Index Refuses

The eligibility check is one function, [`is_arith_tuple_route`](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/filter.rs#L230-L238), and both the narrowing dispatch and the And-child cost ranking gate on it so the two cannot disagree about which shapes take this path.
It is a conjunction of two rules.
The first: *every* field referenced anywhere in either operand, recursively through nested arithmetic, must be one of the four.
The second is easy to miss, and it explains why the `power>4` control in the tables below moves only with round-to-round noise: a bare `cmc`/`power`/`toughness` against a constant is *refused*.
Those already have dedicated single-column index arms, and re-routing them would let the And-child cost ranking disagree with what the narrowing actually dispatches to — which the source comment calls the exact mismatch class #741 fought.
`loyalty` is the deliberate exception: it has no single-column index, so the tuple route is the only narrowing it will ever get, which is how `loyalty>=4` — no arithmetic in it anywhere — ends up 3.4x faster in a PR about arithmetic.

The consequence is that `usd+1<power` gets no help at all.
It mixes a printing-level field — a card's price varies per printing — with a card-level one, so no tuple verdict can stand for a card, and the expression declines to the old full scan entirely rather than being partially narrowed.
It is the slowest query in the sweep on both builds, and the only one still evaluating a predicate 31,508 times — which is why it was the control for the codegen question above.

`edhrec_rank` is the more interesting exclusion.
It is card-level, so it would be *correct* to include, and it would destroy the technique: 31,444 distinct values across 31,508 cards.
Adding it to the key would make the dictionary the same size as the table and turn the tuple scan back into a per-card scan with extra indirection.
The precondition for this whole approach is not "these columns are card-level" but "their *joint* cardinality is small," and only one of those two is checked by the type system.

Which invites the obvious objection: 564 is a fact about Magic, not a technique.
Fair, and the honest way to state the precondition is as a ratio — the scan shrinks by roughly rows ÷ distinct tuples, so the question is never "is arithmetic indexable" but "how many distinct combinations of these columns actually occur," and one `GROUP BY` answers it before any code gets written.
At 5,000 tuples this would still be a 6:1 reduction.
At `edhrec_rank`'s 31,444 it is 1:1, and the index is pure overhead.

## The First Version Made a Compound Query 2.6x Slower

Every timing in this section comes from the PR's own iteration log rather than my re-measurement: the build that produced them existed for one commit, and reconstructing it to re-time it is not something the history makes easy.
They are also from a different sitting on the same class of machine, so they sit a little high against my numbers — the full `cmc>=power` scan they call ~450 µs measures 374 µs in my round two.

The first working version gathered every matching card id into a sorted vector, always.
For a broad predicate that meant collecting ~17k ids scattered across ~280 postings rows and sorting them: 194 µs, against 69 µs for a single-column numeric index producing the same 17k-card result from one contiguous slice.

Alone that still beat the unnarrowed scan, so the targeted benchmark looked fine.
Inside an `And` it was a real regression: `cmc>=power cn:1` went from 66 to 171 µs and `cmc>=power oracle:search` from 133 to 194 µs — `cn:1` means collector number 1 and `oracle:search` a rules-text match, both far more selective than the arithmetic — because the broad set was built first and then thrown away against the selective sibling.
Same class of bug as #741's broad-negation regression: a narrowing that "works" and costs more than not narrowing.
The fix was the representation split the codebase already had for exactly this — count the covered cards first, and promote past the 4,096-card threshold to a bitmap instead of a sorted vector.
After it, `cmc>=power cn:1` is 72 µs (parity, within noise) and `cmc>=power oracle:search` is 80 µs, better than before the whole change.

## The Multiplier Grows With Selectivity

Rebuilt and re-measured for this post, comparing the PR commit `fe30740` against its parent `50eba3e` directly rather than against current `main`, so the numbers isolate this one change.
Both are release builds loading the same 97,206-printing corpus export; each row is the minimum of a 5-second window after 20 warmup queries, at `limit=100`, `offset=0`, `unique=card`, and `orderby=edhrec` (the two single-column controls sort by their own column); M5 Max, macOS 26.5.2, rustc 1.97.1, Python 3.13.7, engine built with `maturin develop --release`.
`total` is the number of matching cards and doubles as the cross-build parity check — it is identical on both builds for every row, or the comparison would be void.
The harness is `scripts/bench_arith_selectivity.py`, a sweep-shaped sibling of [the PR's own benchmark](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/scripts/bench_arith_tuple_postings.py); both reuse the corpus loader from the bitplane benchmark, and the raw CSVs are in `benchmarks/arith-tuple-postings/`.
The corpus itself is a 239 MB JSONL export of the live database and is not in the repo; the one-line `psql` command that produces it is in the bitplane benchmark's docstring.

The whole sweep ran three times, rebuilding both revisions between rounds and interleaving the passes, and the two sides are not equally stable.
After-build rows reproduce within 6.2% across rounds, mostly within 2%.
Before-build rows drift more: `power+toughness<4` measured 415, 417 and 362 µs, a 15.3% spread, and two other broad rows spread 9.9% and 11.1%.
A full unnarrowed scan is simply a longer stretch of machine time with more to go wrong in it, so read the multipliers below as ±15% — `power+toughness<4` is 5.0x, 5.0x and 4.3x depending on the round; `power+toughness>30` is 45.6x, 45.1x and 45.0x.
The table below is round two, the one whose four controls all came out flat.

| query | total | before µs | after µs | change |
|---|---:|---:|---:|---|
| `power+toughness<20` | 17,242 | 416 | 117 | **3.6x** |
| `cmc>=power` | 15,931 | 374 | 111 | **3.4x** |
| `cmc+1<power+toughness` | 9,686 | 541 | 93 | **5.8x** |
| `power<toughness` | 4,680 | 384 | 70 | **5.5x** |
| `power+toughness<4` | 3,975 | 417 | 84 | **5.0x** |
| `cmc+cmc+2<power+toughness` | 417 | 551 | 65 | **8.5x** |
| `power+toughness>13` | 393 | 431 | 61 | **7.1x** |
| `cmc*3+1<power+toughness` | 178 | 524 | 62 | **8.5x** |
| `power-cmc>4` | 36 | 379 | 25 | **15.1x** |
| `power*2>cmc+20` | 7 | 427 | 12 | **35.6x** |
| `power+toughness>30` | 2 | 361 | 8 | **45.1x** |
| `loyalty>=4` (no arithmetic at all) | 219 | 214 | 62 | **3.4x** |
| `power>4` (control, single-column index) | 2,248 | 60 | 60 | 1.00x |
| `cmc>6` (control, single-column index) | 1,344 | 57 | 57 | 1.00x |
| `t:creature` (control, bitplane) | 17,317 | 62 | 62 | 0.99x |
| `usd+1<power` (control, declines) | 11,861 | 671 | 676 | 0.99x |

The multiplier rises with selectivity across three orders of magnitude of result size, and the reason is that the two sides scale differently in the *same* variable.
Before, cost was `31,508 × (expression evaluation) + (page assembly)`.
After, it is `564 × (expression evaluation) + (postings union) + (page assembly)`, and only the last two terms know how many cards matched.

## Most of the 45x Is the Answer Getting Small

There is a confound in that table: every row runs at `limit=100`, so the three most selective rows are also the only ones whose whole result fits in one page without a top-k pass over a larger match set.
That deserves its own measurement — a denser ladder across the 100-match boundary, plus bare single-column comparisons at matched result sizes and the same `orderby`.
Unlike the sweep above, this ladder was measured only in round three — it ran once, so there is no cross-round spread for these rows.
In that round three of the four controls landed 3% faster on the after build and `usd+1<power` landed 1.6% slower, so read the column as ±3%.

| query | total | before µs | after µs | change |
|---|---:|---:|---:|---|
| `power+toughness>13` | 393 | 394 | 60 | **6.6x** |
| `power+toughness>16` | 120 | 416 | 58 | **7.2x** |
| `power+toughness>18` | 72 | 390 | 43 | **9.0x** |
| `power-cmc>3` | 64 | 386 | 38 | **10.1x** |
| `power-cmc>5` | 21 | 363 | 17 | **21.0x** |
| `power*2>cmc+16` | 16 | 430 | 16 | **26.3x** |
| `power+toughness>30` | 2 | 354 | 8 | **44.3x** |
| `cmc>12` (bare index) | 7 | 7 | 7 | flat |
| `toughness>13` (bare index) | 10 | 8 | 8 | flat |
| `power>12` (bare index) | 11 | 9 | 9 | flat |
| `cmc>6` (bare index) | 1,344 | 58 | 56 | flat |
| `power>4` (bare index) | 2,248 | 61 | 59 | flat |

The multiplier climbs through the page boundary without a step at it — 6.6x at 393 matches, 7.2x at 120, 9.0x at 72, 10.1x at 64 — but the slope does steepen below 100, because that is where the page stops costing a fixed hundred rows.
(`power+toughness>13` is in both tables, at 7.1x and 6.6x, which is the round-to-round drift described above rather than two different measurements of two different things.)
So the tail is selectivity *and* page size, and the honest attribution is that the narrowing alone buys the 6.6x–7.2x visible above the boundary; the rest of the way to 44x is the answer itself getting small.

The bare-index rows make that concrete.
A single-column indexed comparison matching 7 cards also costs 7 µs, and one matching 2,248 cards also costs 59 µs.
Nothing about the tail belongs to the tuple index; it is what this engine costs when the answer is small, and arithmetic predicates simply had no way to get there before.
The `orderby` in those controls is not doing the work either: in this round `power>4` costs 59 µs sorted by `edhrec` and 58 µs sorted by `power`.
That row is also the noisiest control in the set — 62, 60 and 58 µs on the after build across the three rounds, a 6.2% spread on a 60 µs query.

They also price the tuple scan directly — the ~5 µs quoted earlier.
`cmc>12` and `power*2>cmc+20` both match exactly 7 cards; they cost 7 µs and 12 µs.
At seven matches the postings union is nothing, so that 5 µs gap is the 564-tuple scan itself — single-digit microseconds, read as a magnitude and not a constant — and an arbitrary arithmetic expression now resolves for a few microseconds *more* than a bare indexed comparison on one column, not less.

That floor is what caps the multiplier.
Across all three rounds the sweep's before-times stay inside 360–551 µs no matter what the predicate matches, while the after-times bottom out at 8–12 µs, so the most a selective arithmetic query can gain is roughly 360 ÷ 12 to 550 ÷ 8 — 30x to 69x.
`power*2>cmc+20` and `power+toughness>30` land at 36x and 45x, inside that band and short of the top of it, because neither is both the cheapest possible answer and the most expensive possible expression.
`power-cmc>4`'s 15x is not a ceiling effect at all: its 36 matches cost 25 µs to assemble, so it never gets down to the 8–12 µs floor the band is drawn from.

## The Most Complicated Expression Now Wins the Most

One variable moves the multiplier independently of result size: expression *depth*.
`cmc+1<power+toughness` matches 9,686 cards and still beats `power+toughness<4`'s 3,975 — 5.8x against 5.0x — because its deeper tree cost more per card in the old scan (541 against 417 µs) while costing nearly the same in the new one (93 against 84 µs).

The cleanest pair is `power+toughness>13` (393 cards, one arithmetic node) against `cmc+cmc+2<power+toughness` (417 cards, three).
They return nearly the same number of cards, so what separates them is almost pure depth: 431 → 551 µs before, 61 → 65 µs after.
Two extra arithmetic nodes cost about 120 µs when paid per card and about 4 µs when paid per tuple.
I would not put a ratio on those two numbers — 4 µs is at the edge of what this harness resolves, and across rounds that gap moves between 4 and 8 µs, which would put the ratio anywhere from 16:1 to 33:1 — but the 120 µs is solid, and the direction is the whole point: the old path paid expression complexity 31,508 times and the new one pays it 564 times.
So the most expensive expression a user can write is now the one that benefits most, which is the exact opposite of the usual relationship between query complexity and index usefulness, and the part of this I did not see coming.

One last check on the whole set: the PR's own ten configurations, re-measured here, come out at a geometric mean of 4.12x, 4.07x and 4.16x in rounds one, two and three, against its reported 4.15x — which straddles it, and which is a more honest way to put it than quoting whichever round I liked.
Those ten deliberately vary `unique` and `orderby`, which is why `power+toughness<4` is 5.0x in the sweep at card/edhrec and 4.57x at the card/rarity ordering the PR reported — against its 4.6x — rather than either being wrong.

## Where It Costs More Than It Saves

One row of the sweep is a loss, shown at round two like the sweep table above — which is the mildest of the three, so the two figures after it are the honest range:

| query | total | before µs | after µs | change |
|---|---:|---:|---:|---|
| `cmc>=cmc` | 31,508 | 240 | 259 | **0.93x** |

`cmc>=cmc` is true for every card, and the engine [discards any narrowing that covers more than 75% of the domain](https://github.com/jbylund/sylvan_librarian/blob/fe3074097e72dbf32d101c2b955c8a60ad4a9091/card_engine/src/lib.rs#L2868-L2888) — [a rule measured into place](../00992_index-selectivity-crossover/) when a price range index made broad queries 4.5x slower.
So the tuple scan runs, gathers all 31,508 card ids, gets thrown away, and the query pays for the full scan anyway: 14%, 8% and 11% slower in rounds one, two and three, pure waste every time.
This one is avoidable in principle, which is what makes it worth showing: the narrowing already counts the covered cards in its first pass, *before* it materializes anything, so it could compare that count against the same 75% cap and decline without building the set it is about to lose.
It doesn't, because the cap lives one level up in the code shared by every narrowing arm — so a query matching everything pays for a bitmap nobody reads.
Nobody sends `cmc>=cmc`, so this has not been worth fixing; it is still the honest shape of the cost.

Three other limits are worth naming.
The scope is a hardcoded list of four fields, not a build-time cardinality measurement — if the joint domain ever grew to tens of thousands of tuples, the narrowing would quietly become a per-card scan with an extra layer of indirection, and no assertion would fire.
The win is confined to card-level fields by construction: the DSL happily accepts `usd+1<power`, and that query is exactly as slow as it was.
And I cannot tell you what any of this is worth in aggregate, because I do not have arithmetic's share of real traffic — the two queries that motivated the change, `power+toughness<4` and `cmc+1<power`, were found by running a 520-query generated survey and sorting by latency, not by reading logs.
What the survey does say is that both left its top 30 slowest entirely, and no query in it regressed by more than 15%.

## Does Any Other Database Do This?

Honest answer first: this is dictionary filtering, generalized from one column to a co-coded tuple domain, wired to postings instead of block pruning, with the predicate evaluated against entries rather than rewritten into their code space.
Every one of those pieces is about twenty years old.
What I went looking for was the assembly, and I did not find it.

**Expression indexes are the opposite trade.**
Postgres indexes on expressions, [Oracle](https://docs.oracle.com/en/database/oracle/oracle-database/19/cncpt/indexes-and-index-organized-tables.html) function-based indexes, MySQL indexed generated columns and SQLite expression indexes all materialize one *named* expression, and all require the query to name it back — Oracle by literally "comparing the expression trees of the statement and the function-based index."
[SQLite](https://sqlite.org/expridx.html) is bluntest: the index is used when the expression appears "*exactly* as it is written in the CREATE INDEX statement," because "The query planner does not do algebra" — index `x+y`, query `WHERE y+x=22`, get a scan.
Unlimited expression complexity, zero coverage of expressions you didn't declare, which for a DSL where the user writes the expression is no coverage at all.

**Evaluating a predicate against a dictionary, on the other hand, is real and shipping.**
[SQL Server 2016's string predicate pushdown](https://learn.microsoft.com/en-us/archive/blogs/sql_server_team/columnstore-index-performance-sql-server-2016-string-predicate-pushdown) is the closest mainstream relative: "SQL Server compares the strings stored in the dictionary and returns the rows that qualify. For example, if a dictionary entry matched with '%tool%', then all referencing rows in the rowgroup are returned. So instead of comparing each value separately, we compared only one."
It even keeps a bitmap of qualifying dictionary entries — the same `matched` list as above — with the same kind of ceiling: "We only allow up to 64K entries in the bitmap."
Oracle has [a patent](https://patents.google.com/patent/US10810208B2/en) that names it "dictionary filtering," and Abadi, Madden and Ferreira's [2006 C-Store paper](https://15721.courses.cs.cmu.edu/spring2016/papers/abadi-sigmod2006.pdf) does the weaker, older version — translating the *constant* into code space rather than evaluating anything: "the DataSource converts the predicate value to its dictionary entry and does a direct comparison on dictionary data."
Every dictionary in that list is over a single column.

**The multi-column dictionary has a name too, from the same year, and its authors called it a query-time liability.**
Raman and Swart's ["How to Wring a Table Dry"](https://www.vldb.org/conf/2006/p858-raman.pdf) calls it co-coding: "Co-coding concatenates correlated columns, and encodes them using a single dictionary."
Their assessment: "Both co-coding and dependent coding exploit correlation maximally, but cause problems when we want to run range queries on the dependent column," and the paper leaves it at "we need to do further investigation on efficient range queries over co-coded or dependent coded columns."

That is the most useful thing I found, because the objection inverts cleanly.
Co-coding hurts when the predicate touches *one* member of the group, because then you have to take the tuple back apart.
When the predicate spans the whole group — which `cmc*3+1<power+toughness` does by construction, and which is the normal case in a DSL with arithmetic in it — co-coding is the *ideal* representation, because the unit the predicate consumes is exactly the unit the dictionary stores.
A twenty-year-old liability and this speedup are one property seen from two workloads.

The closest thing to the whole assembly is [an IBM patent from 2008](https://patents.google.com/patent/US8135738B2/en) whose worked example is `col1+2*col2>col3`, evaluated over distinct values, with an IN-list of qualifying values and a "NOT-IN-list" of non-qualifying ones — the same two-polarity structure as the `Tri::True`/`Tri::False` arms here.
Its multi-column path evaluates over the *cross-product* of per-column distinct lists "by using a standard join technique like nested-loop join," not the 564 tuples that actually occur, and it finishes by "applying such IN-list predicate to each tuple," so it still visits every row where the postings union doesn't.
A patent is also not evidence that anything shipped; DB2 BLU is where I would look next.

One last coincidence: Postgres already computes the number this index is built on.
[Extended statistics](https://www.postgresql.org/docs/current/planner-stats.html) collect n-distinct over column groups because single-column statistics make those estimates "frequently wrong" — so the joint cardinality of a column group is a number mainstream databases go out of their way to measure, and then spend on correcting a row estimate rather than on an access path.

The index does not know what arithmetic is.
It knows that a Magic card can only be 564 different collections of numbers, and for a query language full of arithmetic that turns out to be enough.
