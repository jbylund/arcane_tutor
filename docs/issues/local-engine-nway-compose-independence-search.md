# N-way `And` composition: a general strategy, not one shape at a time

## The problem

`compose_printing_estimate`'s `And` arm has grown a specific tightening for each leaf-pair shape
that turned out to matter: `PairTotals` (border/rarity/frame/legality/cmc/power/toughness pairs),
`arith_tuple_count` (2+ of cmc/power/toughness), `compile_plane`+`eval_planes` popcount
(card-invariant/existential planes), `cn`×`set` density (Round 33), the `set`/`color`/`identity`×
subtype tables (Round 34), `set`×`set` disjointness (Round 35), the subtype×(cmc,power,toughness)
cube (Round 36), and — since this doc was first written — a generalized, registry-driven independence
step (Rounds 38/40, see "What's built now" below). Each one was real, each one was hand-verified
against the real corpus, and the list keeps growing — every round in this arc found another leaf-pair
whose correlation the existing mechanisms missed.

That's the pattern worth generalizing. For an `And` of `N` leaves (`A ∧ B ∧ C ∧ D ∧ ...`), the
question isn't "does *this specific pair* have a hand-written mechanism" — it's "what's the tightest
valid combination of whatever mechanisms *are* available, applied in the right groupings." Two things
make this tractable rather than combinatorially hopeless: real queries almost never have many leaves,
and two of the existing mechanisms already solve the "many leaves" case for free within their own
domain.

**Status as of Round 40**: the registry-driven independence step (below) is a real generalization —
one mechanism, scanned over every residual leaf against a re-validated safety table, replacing several
rounds' worth of what would otherwise have been one-hard-coded-pair-at-a-time branches. It is NOT the
bounded partition search this doc originally set out to describe: no multi-leaf packing, no
triple-level safety, no cost-aware ordering. Rounds 37-40's real contribution is as much the
*measurement infrastructure* (below) as the estimator change itself — the actual search this doc
envisions can now be built and graded against a real baseline, which wasn't true when this doc was
first written.

## What's built now (Rounds 37-40)

Nothing here existed when this doc was first written. It doesn't replace the design below — it's what
makes attempting the rest of it tractable to verify rather than a leap of faith.

- **`and_trace`** (`AcquireFacts`, Round 37a): structured, always-on provenance for the outermost
  `And` node's own evaluation, exposed on `explain()`/`explain_analyze()`. A recursive tree of
  `{"kind": "leaf", ...}` / `{"kind": "op", "op": "min_fold"|"joint_lookup"|"independence", ...}`
  nodes (every node self-contained with its own card/printing/artwork numbers — no separate "final"
  field to keep in sync), plus a `considered` list of every 2-or-3-leaf combination the arm's fixed
  sequence actually attempted, hit or miss. A `hit: false` entry is as informative as a hit — it says
  a mechanism looked at this exact combination and found nothing, not that nothing was ever checked.
  Scoped to the outermost `And` only (no nested `And`-within-`And` recursion) — sufficient for every
  shape the harness below generates, not yet exercised on deeply nested filters.
- **`scripts/nway_estimate_truth_survey.py`** (Round 37b): a checked-in, deterministic, curated-shape
  query generator (every leaf-pair this doc names, a same-family-twice supplement `QuerySampler`
  itself can't draw, an OR-rooted baseline, a broad/pathological N=1..8 catch-all), measuring both the
  cheap estimate and the real ground truth in all three spaces. **Primary metric is plan-choice
  agreement** (`explain()`'s own `picked` bool, free), not raw ratio — a ratio of predicted=1 against
  true=0 reads as "infinitely wrong" yet is completely benign, and predicted=29,000 against
  true=31,000 reads as "6.9% off" and is *also* benign, for the same reason: neither is near a
  threshold that would change the router's pick. Ratio is graded second, floored at `true_total >=
  100`, as a diagnostic for locating where the estimator is loose. `--compare` diffs two isolated
  builds; `--report` summarizes one run alone.
- **`and_estimate_ns`** (`AcquireFacts`, Round 39): a real, single-shot nanosecond timer on the
  acquire-time `PrintingCompose` estimate — deliberately not multi-trial, since the target question is
  an aggregate distribution across thousands of queries, not one query's precise cost. Baseline on the
  real corpus: median 750ns, p90 4.4µs, p99 11.6µs, populated on exactly the fraction of queries whose
  acquire actually reaches that branch. This is the number any future search's own "tax" gets graded
  against — Round 40's own registry generalization moved it to ~917ns median, a real, measured,
  accepted cost for real accuracy gained.
- **A re-validated leaf-pair safety registry** (`IndepClass`/`independence_safe_pair`, Rounds 38/40):
  see "The safety bar is empirical, not provable independence" below for the methodology and the
  concrete confirmed list.

## Card/artwork space's own asymmetry, closed (Round 41)

Scoping this doc's own bounded partition search — "do we have what's needed to hand it to an agent" —
turned up a live gap unrelated to the partition-search question itself. Checking this doc's own worked
example (`color:G AND format:pioneer AND t:elf`, below) against the real engine found card/artwork
space badly under-tightened: `t:elf` already has an exact solo count in all three spaces (the same
per-leaf lookup every bare containment leaf uses), and printing space already floors on it, but
card/artwork space never did — `exact_domain_cards`/`exact_domain_artworks` were populated only when a
genuine multi-leaf mechanism fired, never subsequently folded against the OTHER leaves' own
already-exact counts. Fixed in Round 41 (see the ledger's own "Round 41" section) by flooring
`result_space.card`/`.artwork` on each uncovered leaf's own count, gated by the same breadth guard
`narrow_rec` already uses, scoped so `exact_domain` (what `scan_units`'s real cost pricing reads) is
untouched. This tightens the bound on queries like the worked example below; it does not make them
exact — the underlying "no true 3-leaf joint exists yet" problem (below) is still open.

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
a bound, not asserted — still true, still unmeasured (see "What's not yet done").

## Three things naive strategies get wrong

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

### 3. Comparing an estimate against an exact value by magnitude is unsound, not just risky (Round 40)

Every EXACT/upper-bound mechanism (`PairTotals`, `arith_tuple_count`, `compile_plane`,
`SubtypeArithBox`, plain min-fold) guarantees `count(A∧B) ≤ min(count(A), count(B))` — so among that
class, "pick the smallest available candidate" is always the tightest CORRECT choice. Independence
(and `SetCollectorRange`'s Round 33 density estimate) has no such guarantee: it's a central estimate
that lands on either side of the truth (confirmed directly: roughly half of 610 real calibration rows
had independence undershoot, half overshoot). A selection rule that picks "smallest across everything"
would let an undershooting estimate silently win over a correct exact answer — a real error dressed up
as a tighter bound, found live in Round 38's own test (two EXACT mechanisms tied at the same value,
masking the bug until Round 40's registry generalization made a real conflict possible). The fix is a
strict class priority, not a magnitude race: an estimate-class candidate may only fill a leaf subset
no exact/bound mechanism covers at all, never be magnitude-compared against or allowed to override one
for an overlapping subset. Any future search-selection logic has to preserve this distinction — it is
not solely a Round 40 implementation detail, it's a property any combinator over a mix of exact and
estimate mechanisms must have.

## The safety bar is empirical, not provable independence (revised after Rounds 38/40)

The original version of this doc treated "is this pair independence-safe" as answerable from a static
survey (a 46,184-row pass across 250 leaf-type pairs) and copied its verdicts into prose. Building
Round 40's real registry against that prose directly surfaced two problems worth stating as standing
principles, not one-off fixes:

**The prose itself was self-contradictory and wrong in a fixable way.** `legality×{cn,price,set,year}`
was listed safe, `legality×date/set` unsafe, in the same paragraph — `legality×set` in both lists.
Resolved by domain semantics: Modern/Pioneer-style format legality is *defined* by a release-date
cutoff, and `set:X`/a date/a year all pin the same underlying variable legality already depends on —
not a correlation with exceptions, the same variable observed twice. `legality×year` was a second,
un-flagged instance of the identical error. **`legality×{set,date,year}` is deliberately excluded from
the independence registry entirely** — not because independence measures badly there (it does, but
that's not the reason) — because a materially better answer exists: `card_legalities` is already real
per-printing ground truth, so an exact per-(set, format) table (which fraction of a set's printings
are legal in a format — the same shape as Round 34's `SubtypePairIndexes`) should answer this
precisely. Flagged as a follow-on round, not attempted.

**"No true independence" is the norm in this domain, not the exception, and that's fine.** Every pair
has *some* real exception if you look hard enough — even `legality×price`, the cleanest-looking safe
pair, has Alpha (Reserved-List overrepresented, commands an "original printing" premium independent of
playability). That doesn't make the pair unsafe. The actual bar is empirical and aggregate: does
`min(fold, independence)` net-improve over plain fold across a real sample that deliberately includes
the hard cases, not whether a plausible correlation story can be told. Two pairs the original survey
called UNSAFE reversed on this bar when actually measured: `id×set` (median `|log ratio|` 1.15→0.11,
118/122 improved) and `pow×set` (1.11→0.15, 72/73 improved) — independently re-confirmed on a fresh
seed before trusting a reversal this surprising, not just the implementing agent's own sample. A
follow-up investigation of `safe:legality+usd`'s own regressed tail (real, ~36% of rows, but a net
median improvement) found the same lesson at smaller scale: `f:pauper`'s worst individual cases (ratio
down to 0.28) have a real, nameable cause — Pauper legality effectively requires common rarity, and
rarity drives price directly, a genuine shared variable smaller in scope than `legality×date` but the
same species of problem — yet isolating pauper+penny barely moves the AGGREGATE regressed count (497 of
801 vs. 547 of 861 overall), confirming most of that tail is ordinary independence noise, not a hidden
structural flaw in the pair.

**Confirmed registry, as of Round 40** (printing space, `n≈300` draws per pair unless noted):
`legality×cn` (0.246→0.041), `legality×usd/eur/tix` (0.188→0.011 / 0.195→0.019 / 0.401→0.077),
`type×released` (0.478→0.178), `type×usd` (0.479→0.189), `color/identity/cmc×{usd,eur,tix}` (Round 38,
confirmed uniform across all three currencies), `id×set` (1.151→0.106), `pow×set` (1.114→0.154). A
grid search over a multiplicative bias (`fudge × independence`, 1.0–2.0) found `fudge = 1.0` — no
bias at all — strictly optimal on both median and mean error for every pair checked so far, including
`legality×usd` specifically (re-run after the Pauper investigation, isolated to rows where independence
actually won: median signed error ≈ 0 at `fudge=1.0`). Declined despite looking plausible: same-currency
price crosses (mixed signal, `usd×eur` net worse in printing space while `usd×tix`/`eur×tix` net
better) and `set×type` (similarly mixed across spaces) — don't re-attempt these without new evidence.
`color×identity` needs no registry entry: confirmed already 100%-covered by the pre-existing
`PlanePopcount` mechanism, no live gap.

Still unresolved from the original doc: pairwise-safe does not imply joint-safe (`color`×`identity`
was invisible at the pairwise level and only showed up as a real correlation once tested as a triple).
The registry above is pair-level only — a residual with 3+ mutually pairwise-safe leaves still falls
back to min-fold, not an assumption of joint safety by transitivity. Triple-level re-validation of the
confirmed pairs above hasn't been attempted.

## The corrected model: partition search, not a fixed pipeline

Rather than "contract, then independence," the right framing is: **find a partition of the `N` leaves
into groups such that (1) each group either has an exact/cheap mechanism or is left as singletons,
and (2) every pair of leaves that ends up in *different* groups is independence-safe at the
individual-leaf level** — not "atom vs. atom," since an atom's constituent leaves carry their own
correlations forward. Where condition (2) can't be satisfied for some cross-group pair, those two
leaves either need to be forced into the same group, or the whole comparison falls back to the
existing conservative min-fold for that cross-term. This makes leaf-level independence-safety a
**constraint** on which partitions are valid, not a combination step applied after the fact — still
the target architecture; what's shipped (Round 40) is one flat pairwise scan over the residual, not
this general partition framing.

**"Which grouping wins a leaf" is a non-issue for the EXACT/bound class — Round 42 confirmed this
directly, not just reasoned about it.** Any true sub-conjunction's own exact count is a valid upper
bound on the full `And` no matter what other leaves are present or what other mechanism also fired
(intersecting more constraints only shrinks or preserves a matching set) — so `.min()`-folding every
candidate grouping any registered EXACT mechanism can compute, in any order, over overlapping or
disjoint leaf subsets, is always sound. No priority/placement rule is needed for this class; a general
partition search over EXACT mechanisms only needs to enumerate every applicable subset and fold the
min, same as Round 42 did for one mechanism. The genuinely open version of this question is narrower
than the doc used to frame it: what happens when an ESTIMATE-class candidate (independence, or a
future one) could apply to a leaf subset an EXACT mechanism ALSO covers, or when two ESTIMATE
candidates compete for the same leaves — an estimate is not a guaranteed bound (Round 40's
class-priority finding), so `.min()` isn't automatically safe there the way it is for EXACT/bound
mechanisms. That question (not "how a 3+-leaf residual gets partitioned" in general) is what remains.

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

Not built. Round 40's scan is pairwise-only over the residual (every registry-confirmed-safe PAIR of
present leaf classes gets its own independence candidate, each separately narrowing the same `result`
via `min`) — never a genuine subset search, never a packing decision among competing groupings larger
than a pair. The residual-size distribution this bound is reasoned from still hasn't been measured
against real (or deliberately pathological) traffic.

## Efficiency: don't pay for the search itself

Two principles, one already validated against real (if narrow) evidence — both still fully open,
unchanged since this doc was first written, because no new EXPENSIVE mechanism has been added since
(Round 40's registry entries are all `O(1)` hashmap-style lookups, same cost class as the leaves'
own solo estimates, hence why `and_estimate_ns`'s own tax from Round 40 was real but modest — see
above):

- **Prefer cheap mechanisms over expensive ones, not just tight ones.** The hashmap-based exact
  lookups (`PairTotals`, the subtype tables, `cn`×`set`) are `O(1)`; `compile_plane`+`eval_planes`
  costs real, measured time (`O(leaves × n_cards/64)`, ~4-9μs measured directly for the
  `color`×`identity` case, cheaper for simpler existential-only combinations). The search should rank
  by cost as well as tightness, defaulting to the free lookups and only paying for a plane popcount
  when nothing cheaper covers the leaves in question — and even then, a real cost/benefit check found
  it "leans net win, but not decisive" for the one case measured this way (a same-build-canary
  latency check found no clean signal at the whole-query level, though the routing-flip-rate argument
  favored keeping the exact path). Moot until a future round adds a mechanism in this cost class.
- **Never redo the same plane intersection twice.** `popcount_with_bits` (`lib.rs`, the `And` arm's
  existing existential-leaf loop) currently rebuilds and re-`eval_planes`s the *entire* card-invariant
  plane list from scratch, once per existential leaf present — real, measurable waste whenever 2+
  existential leaves co-occur (rare in practice, but a genuine bug of the same shape this whole
  section is about avoiding in the *new* machinery). The fix, and the design principle for anything
  new: compute the shared/base intersection once, cache the resulting bit-vector, and treat every
  additional candidate as an incremental extension of that cached base — never recompute a shared
  prefix from scratch per candidate. Still unmeasured for real-traffic frequency; still not attempted.

## What's not yet done

- **The actual bounded partition search** — subset enumeration up to size 3/4, greedy packing of
  multiple simultaneous non-overlapping tightenings across arbitrary leaf groupings. Round 40 ships a
  flat pairwise scan over the residual, not this. This is the single biggest remaining gap between
  "what's built" and "what this doc describes."
- ~~The `color:G format:pioneer t:elf`-shaped 3-leaf joint~~ — **closed in Round 42.** This was
  originally framed as needing a placement rule (`compile_plane` claims `color`+`legality` together
  first in source order, so `SubtypePairIndexes` would need to "win" the leaf instead). That framing
  was wrong: `exact_domain_*`'s existing `.map_or(x, |d| d.min(x))` chaining across mechanisms already
  composes correctly regardless of order — any true sub-conjunction's exact count is always a valid
  bound on the full `And`, so there's no race to adjudicate for the EXACT/bound class at all. The real
  gap was just that `SubtypePairIndexes` never computed a candidate past `v.len() == 2`. Round 42
  generalized the gate (no reordering of `compile_plane`), and the existing `.min()`-chain automatically
  picked whichever mechanism was tighter. See the ledger's "Round 42" section, including a real
  first-pass gap (skipping `covered` leaves for the exact-hit branch, not just the estimate branch)
  caught before merging.
- **Cost-aware mechanism ordering** — moot so far; no expensive mechanism has entered the registry
  since this was written.
- **The `popcount_with_bits` redundancy fix** — still needs a real-traffic frequency measurement
  before it's worth shipping on its own; still not done.
- **Triple-level (3+-leaf) independence safety** — pairwise-safe does not imply joint-safe; the
  confirmed pair-level registry (above) hasn't been re-checked at the triple level the way
  `color`×`identity` was originally found to fail at.
- **The residual-size distribution for real (and deliberately pathological) 5+-leaf queries** — the
  `N choose 3/4` bound is still reasoned from what's been sampled, not confirmed at the tail. The
  harness's own `broad:n1..n8` catch-all generates this population; it hasn't been specifically
  analyzed for this question yet.
- **An exact `legality×{set,date,year}` mechanism** — flagged above as a better answer than
  independence for this specific family; a genuinely promising, scoped follow-on (Round 34-shaped),
  not attempted.
- **`t:enchantment power<10`-shaped queries** (a main type that mostly *excludes* having a value at
  all, combined with a broad arithmetic bound) — real ratio 7.4x over via naive independence, verified
  against the corpus — haven't been checked against the *existing* `compile_plane`+`arith_tuple_route`
  combination to see whether this shape is already handled correctly or is a live gap; structurally
  similar to the already-verified-exact `format:modern id:g t:creature power+toughness>cmc+cmc`, but
  the "mostly no value at all" population shape hasn't specifically been tested. Unchanged since this
  doc was first written — still open.
- **`safe:legality+usd`'s Pauper/Penny tail** — a real, small, explainable exception (see above); not
  urgent, but a candidate for a narrow follow-up if it ever matters for real routing regret.

## Related docs

- [local-engine-gathered-scan-card-printing-varying-depth.md](local-engine-gathered-scan-card-printing-varying-depth.md)
  — the round-by-round ledger this whole arc is tracked in. Rounds 33-36 are the hand-written
  mechanisms this doc originally generalized from; Rounds 37-40 are the measurement infrastructure
  (`and_trace`, the survey harness, `and_estimate_ns`) and the first real generalization (the
  independence registry, the class-priority fix) — read there for the full round-by-round numbers,
  not repeated here.
- [00852-engine-compose-acquire-p3-p4-ranking.md](00852-engine-compose-acquire-p3-p4-ranking.md) —
  the original `StreamedSelect`/`GatheredScan` routing investigation this whole cardinality-estimation
  arc grew out of.
