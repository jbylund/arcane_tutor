# `DeclineSparseExact` Throws Away a Compose Build It Already Paid For

Status: proposed, not started. Filed as
[#848](https://github.com/jbylund/sylvan_librarian/issues/848). Item (1) of
[the legality scan-scope work](done/local-engine-legality-scan-scope.md), which otherwise shipped in #844.

## The cliff

`PrintingCompose` can be picked, build its printing bits, compute the exact total, find it under
`STREAM_MIN_MATCHES` (1,024), and **decline** — having paid the whole build. Dispatch then runs a second
plan on top of that:

    card:  PrintingCompose  pred=67.5u  trials=0  declined=9  picked=True  paging_taken='DeclineSparseExact'

The sparse floor is a sound rule about whether compose is worth *building* for a tiny result. By the time
`DeclineSparseExact` fires the bits already exist, and finishing the page from them is a page-sized walk.
Declining after the exact total is known is strictly worse than completing.

The decline belongs **before** the build, on the estimated total, where `ComposePaging::Decline` already
lives.

## The trigger was removed; the cliff was not

This is the part worth being precise about, because #844 could be read as having fixed it.

The estimate that walked queries into the cliff was the `min` fold: `f:modern border:white` estimated 2,755
cards against a true 978, comfortably above the floor, so the router predicted compose would run when it
would bail. Three of four queries in that family were affected:

| query (card mode) | est cards | true total | est/true | |
| --- | --: | --: | --: | --- |
| `f:modern border:white` | 2,755 | 978 | 2.82 | declined |
| `f:pauper border:white` | 2,755 | 858 | 3.21 | declined |
| `f:vintage border:white` | 2,755 | 2,025 | 1.36 | ran |
| `f:modern r:rare border:white` | 2,755 | 273 | **10.09** | declined |

[The pair table](done/local-engine-pair-totals.md) makes those two-leaf card totals exact, so
`compose_paging` now predicts the decline instead of walking into it, and card mode went **163.1 µs →
102.1 µs**.

That removed the population that was hitting the cliff. It did not remove the cliff. **An estimate near a
hard threshold will always sometimes fall the wrong side**, and the cost of being wrong is a full compose
build discarded. The exact tables cover pairs of low-cardinality values; three or more leaves get a bound
rather than an exact answer (min-over-pairs reads 2.02× on `f:modern r:rare border:white`), and any leaf
outside those dimensions is estimated as before.

## What to change

Complete the page from the bits rather than discarding them. The decline exists to avoid *starting* a build
that will not pay; once started, the marginal cost of finishing is a page walk over a set already known to
be small. Concretely: `DeclineSparseExact` becomes a completion path, and `ComposePaging::Decline` — the
pre-build, estimate-based decline — stays as the only place a compose is refused.

Two things to check rather than assume:

- **The prediction must keep matching the branch taken.**
  `compose_paging_prediction_matches_the_branch_taken` exists for this and caught an earlier attempt at a
  different paging change immediately. If `DeclineSparseExact` stops being a reachable outcome, that test's
  coverage guard has to be updated deliberately, not silently.
- **Row identity.** A completion path is new code producing user-visible rows on a population that
  previously fell through to a different plan. Compare returned `scryfall_id` sequences across all three
  result spaces and both prefer settings, not just totals — the trap recorded in
  [the existential-plane repair pattern](reference-engine-printing-varying-plane-repair-pattern.md).

## Measurement note

A declining plan accumulates no trials, so these queries are **absent from every regret and cost matrix
taken so far**. Enabling a completion path introduces a population nothing has measured — the same trap
that made [sparse compose gather](local-engine-sparse-compose-gather.md) look neutral when it was not. Take
a paired wall-clock A/B on the compose slice, not a regret delta.

## Related

- [done/local-engine-legality-scan-scope.md](done/local-engine-legality-scan-scope.md) — where this was
  found, and defect 1's scan-scope fix that shipped alongside it.
- [done/local-engine-pair-totals.md](done/local-engine-pair-totals.md) — the exact totals that removed the
  trigger.
- [local-engine-sparse-compose-gather.md](local-engine-sparse-compose-gather.md) — the other end of the
  same question: what compose should do instead of declining when the result is sparse.
