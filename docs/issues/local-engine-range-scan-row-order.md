# PrintingRangeScan orders tied rows differently from every other plan

`force_plan_differential_agreement` asserts full row-order equality across plans — except for
`PrintingRangeScan`, which is excluded. This is that exclusion.

## What differs

Every other plan orders its whole match set through `page_cmp`, or walks a card permutation whose
order `page_cmp` reproduces. `PrintingRangeScan` does neither. `printing_range_fastpath` walks the
range index bucket by bucket, counts past the buckets that fall before the page, and sorts only the
*touched* buckets:

```
collected.extend((bs..be).map(|t| u32::from(idx[t].1)));   // one value-bucket
...
select_page(matches, page_offset - first_touched, limit)   // window within what was collected
```

That is the whole point of the plan — it is O(page) rather than O(matches), and never materializes
the rows it skips. But it means the global order is *implied* by the index walk rather than produced
by a comparator, and the two only agree if bucket order and `page_cmp` order agree on every key the
comparator reads.

They do not, for printing-space orderbys. A `usd` bucket is one price; within it the index has its
own arrangement, while `page_cmp` continues on to `edhrec_rank`, then card, then printing. Rows tied
on price come out in different orders depending on which plan answered.

## It is not the key-3 divergence

Worth stating explicitly, because the two look alike and were found together.

The key-3 problem — the permutation baking in the first STORED printing's `prefer_score` while the
gathered paths use the first MATCHING one — was fixed by dropping key 3 from cross-card comparison
entirely. This one survived that fix. Probed directly: with the `cid` tiebreak removed from
`page_cmp`, `PrintingRangeScan`/`usd` fails *identically*. Neither caused by that change nor repaired
by it.

## Why it was invisible until now

The ordering contract used to compare a 2-key VALUE sequence, which is blind to this by construction:
tied rows share their `(key1, key2)` value, so any interleaving of them produces the same sequence.
Only once the contract could assert full row order did the divergence have anywhere to show up.

## Blast radius

Same shape as the key-3 bug: a query paged twice can run two plans, because plan choice depends on
`offset`. If one page is answered by `PrintingRangeScan` and the next by a gathered plan, a row tied
on the sort column can repeat or be skipped at the boundary.

Narrower than key 3 was, though. It needs a *printing-space* orderby (`usd`, `rarity` — the columns
with no card permutation), a tie on that column, and a page boundary landing inside the tie. Price
ties are common in the corpus, so this is not exotic.

## Options, none costed yet

1. **Give the fastpath the same total order.** Sort each touched bucket by `page_cmp` — it already
   does — and make the *bucket* boundaries agree with `page_cmp`'s key 1. If a bucket is exactly one
   distinct value of key 1, that holds, and the remaining question is only whether rows spanning a
   bucket edge are windowed correctly.
2. **Widen the buckets to a full key-1 tie group.** Guarantees the plan sees every row it needs to
   order, at the cost of collecting more than the page when a value is very common.
3. **Decline the plan when the page boundary lands inside a tie.** Cheap to detect from the index,
   and falls back to a plan that is already correct — but gives up the fast path exactly where prices
   cluster, which is where it is most valuable.
4. **Leave it and narrow the contract instead**, documenting that printing-space orderbys have
   unstable tie order. Honest, but it keeps a user-visible paging defect.

(1) looks most likely to be both correct and free; it needs someone to check the bucket-edge
windowing rather than assume it.

## Reproducing

`force_plan_differential_agreement` with the `PrintingRangeScan` exclusion removed:

```
PrintingRangeScan ROW ORDER disagrees with GatheredScan
(mode=printing, prefer=default, orderby=usd, dir=asc, filter=usd<100000)
```
