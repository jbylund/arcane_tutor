# N-way `And` composition: a general strategy, not one shape at a time

## The problem

`compose_printing_estimate`'s `And` arm has grown a specific tightening for each leaf-pair shape
that turned out to matter: `PairTotals` (border/rarity/frame/legality/cmc/power/toughness pairs),
`arith_tuple_count` (2+ of cmc/power/toughness), `compile_plane`+`eval_planes` popcount
(card-invariant/existential planes), `cn`×`set` density (#local-engine's Round 33), the
`set`/`color`/`identity`×subtype tables (Round 34), `set`×`set` disjointness (Round 35), and the
subtype×(cmc,power,toughness) cube (Round 36). Each one was real, each one was hand-verified against
the real corpus, and the list keeps growing — every round in this arc found another leaf-pair whose
correlation the existing mechanisms missed.

That's the pattern worth generalizing. For an `And` of `N` leaves (`A ∧ B ∧ C ∧ D ∧ ...`), the
question isn't "does *this specific pair* have a hand-written mechanism" — it's "what's the tightest
valid combination of whatever mechanisms *are* available, applied in the right groupings." Two things
make this tractable rather than combinatorially hopeless: real queries almost never have many leaves,
and two of the existing mechanisms already solve the "many leaves" case for free within their own
domain.

## What already generalizes for free

`compile_plane`+`eval_planes` and `arith_tuple_route`/`ArithTupleIndex` are not pairwise mechanisms —
they each absorb *however many* eligible leaves are present in one shot, with no search:

- `compile_plane` computes one exact joint popcount over every card-invariant leaf plus up to one
  existential leaf (the shared-witness rule caps it at one existential fact, not at two leaves).
- `arith_tuple_route` narrows any combination of `cmc`/`power`/`toughness`/`loyalty` — including
  compound linear expressions like `power+toughness>cmc+cmc` — via the existing `#743` index,
  confirmed exact (verified directly against the engine: `format:modern id:g t:creature
  power+toughness>cmc+cmc` reads ratio 1.00 in every mode).

So the real algorithmic question is only about the **residual** — leaves that don't compile to a
plane and aren't part of an arith combination (subtypes, `set`, price/date ranges, and similar). Real
`And`s rarely have more than 2-4 such residual leaves; the design below assumes this is checked
against real traffic (including deliberately pathological many-leaf queries) before being trusted as
a bound, not asserted.

## Two things naive strategies get wrong

### 1. Contraction doesn't launder correlation (transitivity)

If leaf `A` is correlated with leaf `B`, contracting `A` with some other leaf `C` into an atom `AC`
(via an exact 2-leaf mechanism) does not make `AC` independent of `B` — the correlation is still in
there, just hidden behind the atom's boundary. Treating `AC` as independent of `B` because `AC` is
"exact" is exactly the same mistake as treating `A` as independent of `B` was, applied one level up.

### 2. There is no fixed "always contract via whatever's available" rule

Verified directly against the real corpus for `color:G AND format:pioneer AND t:elf`:

```
n_cards = 31,724
color:G                    = 6,450      format:pioneer = 14,817      t:elf = 660
color:G AND format:pioneer = 3,097      (already exact today, via compile_plane)
color:G AND t:elf          =   560
ALL THREE (real)           =   246

contract (color, legality) first [the pair that's "free" via compile_plane],
  then × P(elf) independently        = 64.4   (ratio 0.26x)
contract (color, elf) first [the pair that's actually correlated],
  then × P(legality) independently   = 261.6  (ratio 1.06x)
```

Same three leaves, same "contract-then-multiply" shape, only the *order* differs — a 4x swing in
accuracy. `compile_plane`'s automatic, unconditional absorption of `color`+`legality` is a liability
here, not a shortcut: it grabs the pair that happens to be cheap to combine, not the pair that's
actually correlated. This isn't a one-off — a systematic check across 6 color×subtype tribal pairs
(Dragon/Wurm/Giant-style "big creature" correlations, and the color-pie ones: `G`×Elf, `U`×Wizard,
`B`×Zombie, `R`×Warrior, `W`×Human, `W`×Soldier) × 3 different "safe" third dimensions (legality,
`cmc` bound) found the same failure in **all 18 combinations**: wrong grouping 0.20x–0.57x, right
grouping 0.86x–1.11x.

## The corrected model: partition search, not a fixed pipeline

Rather than "contract, then independence," the right framing is: **find a partition of the `N` leaves
into groups such that (1) each group either has an exact/cheap mechanism or is left as singletons,
and (2) every pair of leaves that ends up in *different* groups is independence-safe at the
individual-leaf level** — not "atom vs. atom," since an atom's constituent leaves carry their own
correlations forward. Where condition (2) can't be satisfied for some cross-group pair, those two
leaves either need to be forced into the same group, or the whole comparison falls back to the
existing conservative min-fold for that cross-term.

This makes leaf-level independence-safety a **constraint** on which partitions are valid, not a
combination step applied after the fact. A large body of leaf-type-pair verdicts already exists to
answer "is this pair independence-safe" (a 46,184-row survey across 250 leaf-type pairs — safe:
legality×{cn,price,set,year}, id/color×price, cmc×price, type×{year,usd}; unsafe:
legality×date/set (format legality is literally date-defined), color×type, keyword×type, set×type,
id×set, power×set, color×identity (colors ⊆ identity), same-currency price pairs (usd/eur/tix aren't
independent measurements of each other)) — but it needs re-checking at the 3-way level before
trusting it generally: pairwise-safe does not imply joint-safe (`color`×`identity` was invisible at
the pairwise level and only showed up as a real correlation once tested as a triple).

## Bounding the search

Given the residual is typically small, the search doesn't need to be clever — it needs to be
*bounded*, so a pathological query (10+ leaves, adversarial or just unusual) degrades gracefully
instead of blowing up:

- Enumerate subsets of the residual up to size 3 (or 4) — `O(N^3)`, trivial even at `N=20`.
- For each subset, check whether any registered mechanism (exact or verified-independent) applies to
  exactly that shape.
- Greedily pick a set of non-overlapping winning subsets (a small packing problem — real queries
  rarely have more than one or two candidates active at once).
- Combine whatever's left via independence, respecting the leaf-level safety constraint above; worst
  case, behave exactly like today's min-fold.

## Efficiency: don't pay for the search itself

Two principles, one already validated against real (if narrow) evidence:

- **Prefer cheap mechanisms over expensive ones, not just tight ones.** The hashmap-based exact
  lookups (`PairTotals`, the subtype tables, `cn`×`set`) are `O(1)`; `compile_plane`+`eval_planes`
  costs real, measured time (`O(leaves × n_cards/64)`, ~4-9μs measured directly for the
  `color`×`identity` case, cheaper for simpler existential-only combinations). The search should rank
  by cost as well as tightness, defaulting to the free lookups and only paying for a plane popcount
  when nothing cheaper covers the leaves in question — and even then, a real cost/benefit check found
  it "leans net win, but not decisive" for the one case measured this way (a same-build-canary
  latency check found no clean signal at the whole-query level, though the routing-flip-rate argument
  favored keeping the exact path).
- **Never redo the same plane intersection twice.** `popcount_with_bits` (`lib.rs`, the `And` arm's
  existing existential-leaf loop) currently rebuilds and re-`eval_planes`s the *entire* card-invariant
  plane list from scratch, once per existential leaf present — real, measurable waste whenever 2+
  existential leaves co-occur (rare in practice, but a genuine bug of the same shape this whole
  section is about avoiding in the *new* machinery). The fix, and the design principle for anything
  new: compute the shared/base intersection once, cache the resulting bit-vector, and treat every
  additional candidate as an incremental extension of that cached base — never recompute a shared
  prefix from scratch per candidate.

## What's not yet done

- No general machinery has been built. Every fix so far (Rounds 33-36) is still its own hand-written
  2-leaf-shape detector; this doc describes the target architecture, not shipped code.
- The residual-size distribution for real (and deliberately pathological) 5+-leaf queries hasn't been
  measured — the `N choose 3/4` bound is reasoned from everything sampled so far, not confirmed at
  the tail.
- The exact scoring function for partition selection (how to weigh "how much tightening" against "how
  many forced-conservative cross-terms remain") hasn't been designed.
- The `popcount_with_bits` redundancy fix hasn't been measured for real-traffic frequency (how often
  2+ existential leaves actually co-occur) before deciding it's worth shipping on its own.
- `t:enchantment power<10`-shaped queries (a main type that mostly *excludes* having a value at all,
  combined with a broad arithmetic bound) — real ratio 7.4x over via naive independence, verified
  against the corpus — haven't been checked against the *existing* `compile_plane`+`arith_tuple_route`
  combination to see whether this shape is already handled correctly or is a live gap; structurally
  similar to the already-verified-exact `format:modern id:g t:creature power+toughness>cmc+cmc`, but
  the "mostly no value at all" population shape hasn't specifically been tested.

## Related docs

- [local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)
  — the round-by-round ledger this whole arc is tracked in (Rounds 33-36 are the shipped mechanisms
  this doc generalizes from).
- [00852-engine-compose-acquire-p3-p4-ranking.md](00852-engine-compose-acquire-p3-p4-ranking.md) —
  the original `StreamedSelect`/`GatheredScan` routing investigation this whole cardinality-estimation
  arc grew out of.
