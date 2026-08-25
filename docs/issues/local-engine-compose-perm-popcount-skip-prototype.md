# A popcount-scatter alternative to `Perm`'s card walk, prototyped and measured

`PrintingCompose`'s `Perm` paging (`walk_grouped_page`) walks the sort permutation card by card,
bit-testing each visited card's whole printing span, until enough matches fill the page. Its cost is
*position-dependent*: insensitive to how many matches a filter has, sensitive to how deep into the
permutation the walk must go — which is exactly why `cards_visited` has been hard to estimate
([reference-engine-compose-perm-cards-visited-estimator.md](reference-engine-compose-perm-cards-visited-estimator.md)):
EDHREC-order clumping means "how deep" has no simple relationship to the query's own match count.

`run_query_streamed_popcount` (serving `PlanePopcountOrder`/`CardRangePopcount`) already solves a
sibling problem differently: scatter a card-existence bitmap into sort-rank order via the inverse
permutation, then skip to an offset by popcounting 64-card *words* instead of testing cards one at a
time. Cost is `O(popcount(bitmap) + n_cards/64)` — *density-dependent*, completely insensitive to
where the matches sit. This doc prototypes porting that technique to `Perm`'s own walk, for all three
`unique` modes, and measures whether it's actually better, not just differently-shaped.

**This turns out to be exactly what [00730-engine-popcount-skip-walk.md](00730-engine-popcount-skip-walk.md)
already proposed and filed**, arrived at independently from the cost-model-estimation side rather than
recognized up front — see that doc's own "the idea" section. This doc is the prototyping/measurement
half of it; [reference-engine-compose-popcount-skip-topk-select.md](reference-engine-compose-popcount-skip-topk-select.md)
is a follow-on design question (collapsing the skip+re-walk into the scatter itself).

## What was built

Three `#[cfg(test)]` prototypes, not wired into the fastpath, all built against the general
`SortOrder`/`ArchivedSortPermutations::order` API `run_query_streamed_popcount` already uses — so all
three generalize to every sort column with a permutation for free, unlike `WalkCheckpoints`, which is
EDHREC-only:

- **`walk_card_page_via_popcount_skip`** (`Mode::Card`). Scatter, in one pass over `pbits`' set
  printings, straight to a rank-ordered card-existence bitmap (`printing_to_card[pid] ->
  order.inv[cid]`, deduplicated in-line so a card reached via several of its own matching printings
  still counts once), skip by word-popcount, then re-scan only the *emitted* cards' spans to pick
  each one's best-`prefer_score` printing.
- **`walk_printing_page_via_popcount_skip`** (`Mode::Printing`). A card can contribute more than one
  row here, so the scatter is *weighted* rather than a bare existence bit: one increment per matching
  printing (`printing_to_card[pid] -> order.inv[cid] -> block_weight[rank/64] += 1`). The fine phase,
  once the target block is found, walks forward card by card re-testing each one's span — the same
  loop shape `walk_grouped_page`'s own `Printing` branch already uses.
- **`walk_artwork_page_via_popcount_skip`** (`Mode::Artwork`). One row per *distinct*
  `artwork_group_id` a card touches, not one row per matching printing, so raw per-printing weighting
  would overcount. Still one pass, still `O(popcount(pbits))`: printings of one card are contiguous
  in pid-space (`offsets` partitions it that way), so a card boundary is detected simply by the card
  id changing, and a small `seen_groups` buffer (bounded by one card's own distinct-group count, not
  the corpus total) dedupes within it. The fine/emit phase reuses `walk_grouped_page`'s own `Artwork`
  branch verbatim (`group_best`/`touched`, best-`prefer_score` representative per group, `page_cmp`
  order across a card's own multiple group-rows).

All three share a single skip/scatter shape: scatter into rank-ordered 64-card blocks weighted by
whatever "one row" means for that mode (existence, printing count, or distinct-group count), then
skip by summing block weights instead of testing cards one at a time.

**`Mode::Card` was originally built as two passes** (project `pbits` to card-existence via
`printing_bits_to_card_bits`, *then* scatter that into rank order) — a real inefficiency caught by
comparing it against `Mode::Printing`'s one-pass design, since the projection alone already costs as
much as `Printing`'s entire scatter, before `Card` paid a second pass on top. Folded down to one pass,
matching the other two; see the performance numbers below for the before/after.

## Correctness

Three differential tests, `#[ignore]`d (need `real.store`), sharing one `random_pbits` helper (a
printing-space bitmap at a given density, tail padding cleared): `walk_card_page_via_popcount_skip_matches_walk_grouped_page`,
`walk_printing_page_via_popcount_skip_matches_walk_grouped_page`, and
`walk_artwork_page_via_popcount_skip_matches_walk_grouped_page` — 360 cases each (4 bitmap densities
x 3 random trials x 3 sort columns x 2 directions x 5 page points, including one past the total).
Deliberately not tied to any real filter's semantics — the algorithms' correctness depends only on
the bitmap's shape, not what it means. `Artwork`'s is the one most likely to have caught a real bug
(the scatter's group dedup and the emit phase's own grouping have to agree on what counts as "one
row"); it passed on the first run. Every case, all three modes, matches `walk_grouped_page` row for
row by pointer identity.

## Performance: a real, measured crossover — in all three modes

Real corpus, `otag:triggered-ability` (a tag whose matches front-load among reprint-heavy
EDHREC-early staples, per this session's own earlier finding, then thin out), `limit=20`, sweeping
`offset`. `Card`'s numbers are post-single-pass-fix:

| offset | Card old/new | Printing old/new | Artwork old/new |
| --: | --: | --: | --: |
| 0 | 0.02x | 0.02x | 0.01x |
| 2,000 | 0.46x | 0.21x | 0.28x |
| 4,000 | 0.93x | 0.39x | 0.57x |
| 8,000 | **2.00x** | 0.73x | **1.24x** |
| 15,000 | 3.53x | **1.62x** | 2.84x |
| 25,000 | 3.81x | 3.26x | 4.00x |

All three show the same shape: old scales roughly linearly with offset (position-dependent — exactly
what's made `cards_visited` hard to estimate), new is flat past its fixed scatter cost
(density-dependent, indifferent to depth). Crossover sits earliest for `Card` (between 4,000 and
8,000), latest for `Printing` (between 8,000 and 15,000), with `Artwork` in between — ordering that
roughly tracks each mode's own scatter cost (`Artwork`'s per-printing `seen_groups` scan costs more
than `Printing`'s bare increment; `Card`'s single-pass fix undercut both of the others' "new" times
for the same tag). A shallow-page control on a common value (`keyword:flying`) shows the opposite in
all three: old wins by several x at `offset=0`, new wins within a couple thousand offset — the same
crossover, driven by depth rather than by rarity, on a filter with ~20x more total matches.

**`Card`'s single-pass fix was a real, large win**, not just a cleanup: crossover moved from
offset ~4,000-8,000 down to ~2,000-4,000, and the win at depth roughly doubled (offset=25,000: 2.16x
before the fix, 3.81x after).

**Neither strategy dominates, in any mode.** This is exactly why it needs to be a *choice*, not a
replacement: build the scatter only when it's predicted cheaper than walking, using quantities
already available (match count vs. an estimated walk depth) — the same three-way shape
`Perm`/`OrderbyWalk`/`Gather` already is, one more option.

## The card-invariant shortcut: not built yet, and how much it would cover

`Printing` mode's general scatter above already works for any filter, card-invariant or not — the
weight is 1 per matching printing regardless. A cheaper, but conditional, alternative was discussed
before building the general version: project to a card-existence bitmap and weight each matching card
by a static `card_id -> num_printings` lookup (`offsets[cid+1]-offsets[cid]`, already free), turning
the scatter into `O(matching cards)` instead of `O(matching printings)` — cheapest exactly where
clumping already makes cards expensive (heavily-reprinted matches). This is only EXACT when the
composed filter is **card-invariant**: every printing of a matching card matches, nothing
printing-level narrows it further (`touches_printing_field`, already used elsewhere in this codebase
for the identical question on the `candidates` acquire path). Not built — the general version above
covers correctness for every filter already, so this is a targeted speedup layered on top, not a
prerequisite.

**Measured how much of real `Perm` traffic that shortcut's gate would actually cover**
(`scripts/bench_compose_card_invariance_split.py`, uniform sampler, `residual_card_invariant` newly
exposed on `PrintingCompose`'s own acquire branch — see `card_engine/src/lib.rs`'s
`compose_source`/`mk_plan_feats` call site):

| paging | invariant | varying | invariant% |
| --- | --: | --: | --: |
| Decline | 55,229 | 147,240 | 27.3% |
| Gather | 14 | 7,715 | 0.2% |
| OrderbyWalk | 6,899 | 47,588 | 12.7% |
| **Perm** | **11,601** | **84,159** | **12.1%** |
| ALL | 73,743 | 286,702 | 20.5% |

Stable across seeds (12.0-12.3% for `Perm` specifically, 20.4-20.5% overall). Lower than a hopeful
prior might guess — most real compose traffic touches at least one printing-varying field (price,
border, rarity, set code, artist, flavor text are all common in realistic query mixes). **This does
not mean the popcount-skip approach only helps 12% of `Perm` traffic** — the *general* per-printing
scatter (still `O(popcount(pbits))`, still density- not depth-dependent) applies regardless of
card-invariance; the 12% is specifically how much traffic would get the *cheapest* card-level-only
form of it.

## A v1 decision rule, validated against real data — right ballpark, not calibrated

Getting a rate for the scatter phase alone turned out to be its own small investigation. A kernel
bench isolating it (`compose_popcount_skip_kernel_costs`, `Mode::Card`, `offset=0, limit=1`, varying
match count via synthetic random card samples of controlled size — real tags only offer a handful of
discrete match counts to fit against) hit a real confound at first: even at `offset=0`, the skip
phase still has to scan forward through empty blocks until it finds the *first* match in rank order,
and for a sparse random sample that position is itself random — not the scatter-only cost this was
meant to isolate. Fixed by forcing the rank-0 card into every sample. Even after the fix, the small
sizes (100-1,000 cards) stayed noise-dominated — a fixed per-call cost (allocation, function-call
overhead) comparable to or larger than the true per-match signal at that scale — but the larger sizes
agreed with each other: 6.575 ns/card between the 5,000 and 10,000-card points, 6.529 ns/card between
10,000 and 20,000. Averaging 5 random draws per size (not just one) helped but didn't fully clean up
the small end.

Rather than chase that further, `Mode::Printing`'s own rate came from a 2-point calibration using
already-measured, already-clean data instead: `keyword:flying` (9,100 matches) and
`otag:triggered-ability` (41,216 matches), reading their real `new` plateau from
`compose_printing_walk_popcount_skip_vs_walk`. That gives `new_ns ~= 0.872 * matches - 935` — a
near-zero intercept, consistent with `Printing` mode's scatter cost dominating almost entirely (no
big fixed skip/emit overhead to speak of), and it predicts both calibration points' own measured
values back within a few percent.

Combined with the reconciled natural-query regression for the OLD walk (`1.9 * cards_visited_estimate
+ 0.32 * printings_walked_estimate`, from `reference-engine-compose-perm-cards-visited-estimator.md`) and
`WalkCheckpoints`' own depth estimate (the one validated case — see that doc, `unique=printing`,
EDHREC, bare leaf), this is enough for an actual decision rule:
`scripts/bench_compose_popcount_skip_decision_rule.py` predicts, for every offset in
`otag:triggered-ability`'s own sweep, which strategy should win — and gets the *qualitative* shape
right (old cheaper through offset 15,000, new cheaper by 25,000) but the *crossover position* wrong
by about 50%: predicted ~15,566, real measured ~10,200. Right ballpark, wrong precision — exactly
what a rate built from a 2-point calibration and an estimator with its own ~10-15% typical error
(`WalkCheckpoints`' own p10-p90 band) should produce.

## What this doesn't answer yet

- **The decision rule only covers `unique=printing`, bare-leaf/EDHREC traffic** — the one population
  with a validated depth estimator. `Card` and `Artwork` mode have no such estimator at all (the
  checkpoint machinery is `Printing`-mode-only), so a decision rule there would have to fall back to
  the ratio estimator this whole investigation started by showing fails badly, or use a cruder
  always-prefer-the-safer-option heuristic. Not attempted.
- **The rates themselves are rough.** `Printing`'s is a 2-point calibration, not a properly isolated
  kernel rate; `Card`'s kernel attempt is noisy below ~5,000 matches. Both are good enough to get the
  right qualitative answer, not to trust the exact crossover position.
- **The card-invariant shortcut isn't built.** Measured how much it would cover; not implemented.
- **Not wired in.** These are `#[cfg(test)]`-only prototypes, exercised by seven tests, plus a
  Python-side decision-rule validation that reads real acquire output but doesn't touch the engine.
  Making any of the walk prototypes real execution behavior is a bigger, riskier change than anything
  else in this doc chain — it touches the executor, not the cost model, with its own row-order
  correctness surface (covered here for all three modes, but only against random bitmaps, not the
  full fuzz suite's real-filter coverage).
- **If this pans out, it plausibly obsoletes most of the `WalkCheckpoints` estimator work**: the
  executor would just do the cheap thing and report real time, and the cost model would need
  `O(matches + n_cards/64)` — quantities that don't blow up with clumping — instead of an approximated
  `cards_visited`. That would be a good problem to have, but it's not resolved by this doc.

## Status

Prototyped and measured, not shipped, all three `unique` modes, plus a v1 decision rule validated
(not yet calibrated) for `unique=printing`. Seven new `#[cfg(test)]`-gated tests in
`card_engine/src/tests.rs` (three differential correctness tests, three real-corpus offset-sweep
performance comparisons, one scatter-rate kernel bench), all `--ignored` (need `real.store`).
`residual_card_invariant` now also set on `PrintingCompose`'s own acquire branch (diagnostic only, not
read by `plan_cost`). Two new scripts: `bench_compose_card_invariance_split.py` (the invariance split
above) and `bench_compose_popcount_skip_decision_rule.py` (the decision-rule validation). `cargo test
--release` 156/156, `cargo clippy --all-targets -- -D warnings` clean.

Next: tighten the `Printing`-mode rates (a cleaner kernel isolation, or more calibration points),
extend the decision rule's coverage to `Card`/`Artwork` (needs a depth estimator there first, which
doesn't exist), or accept the v1 rule as directionally useful and move toward actually wiring
something in.
