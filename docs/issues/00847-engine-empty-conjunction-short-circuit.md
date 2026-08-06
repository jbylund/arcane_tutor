# An Exact Total of Zero Should Short-Circuit Before Routing

Status: proposed, not started. Filed as
[#847](https://github.com/jbylund/sylvan_librarian/issues/847). Split out of
[the pair-totals work](done/local-engine-pair-totals.md) that shipped in #844.

## The gap

`PairTotals` makes the three-space total for two low-cardinality leaves exact, and that includes exactly
zero — the table deliberately archives the zero cells, because storing only non-zero ones made a
provably-empty pair indistinguishable from a pruned one (`frame:2003 frame:1997` read 10,769 against a
true 0).

Nothing consults it early enough to act on the zero.

`border:white border:black t:creature` scans **6,402 printings over 79 µs to return nothing.** Its acquire
is `candidates`, which never reads the totals tables at all, so the query runs a full match loop to
discover what a lookup already knows. A filter containing two disjoint conjuncts is empty regardless of
what else it says.

`leaves_are_disjoint` already encodes the rule that makes this decidable without stored data: border,
rarity and legality hold exactly one value per printing, so two distinct values never co-occur. It is
called too late to matter.

## Worth doing for the shape, not the aggregate

These queries are rare in traffic and the aggregate win is near zero. What the change buys is the removal
of a class where **adding selectivity makes a query slower** — the same complaint that motivated
[proven conjuncts](done/local-engine-proven-conjuncts.md), where `o:this border:black` was 38× slower than
`o:this` for a 6× smaller result. Here the result is empty and the query still scans.

## The one thing that will go wrong

Carried from #844, where it cost 24× on the first attempt: **a result total is not a scan domain.**

Letting an exact zero reach the feature vector collapsed `eval_domain` and `scan_units` for the
*materializing alternatives*, which priced `GatheredScan` at 0.2 µs against a measured 199.3 —

    GatheredScan     pred=  0.2u   meas= 199.3u   <- picked
    PrintingCompose  pred=  1.9u   meas=   1.0u   <- actually best

A plan still has to scan to *discover* that a set is empty. #844's fix was to split `ComposeEstimate` into
`result` and `candidate` so the alternatives are priced on what they actually walk.

So the short-circuit must **bypass routing entirely** and return the empty result, not feed routing a
zero. Those are different changes and only the first is safe.

## Where it goes

Before plan selection, on the bound filter. The check is cheap — group an `And`'s children by dimension,
ask `leaves_are_disjoint` — and it is exact rather than an estimate, so there is no threshold to tune and
no A/B to justify it. The verification that matters is row identity: an empty answer must be empty for the
right reason, in all three result spaces, and under `Or` / `Not` the rule must not fire at all.

## Related

- [done/local-engine-pair-totals.md](done/local-engine-pair-totals.md) — the table, its selectivity
  pruning, and the 24× regression the result/candidate split fixed.
- [#846](00846-engine-card-numeric-contradictions.md) — the same "provably empty" question arriving from
  numeric bounds instead of disjoint values. If both land, they want one pass.
