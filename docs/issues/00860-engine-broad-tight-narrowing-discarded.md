# The breadth guard discards exact narrowings — and is masking a tightness over-claim

Status: **prototyped and measured 2026-08-06; NOT safe as written — it fails the fuzz suite.** Filed as
[#860](https://github.com/jbylund/sylvan_librarian/issues/860). Depends on
[#859](00859-engine-exact-trigram-no-verify.md).

The performance idea is worth 4.4×. The reason to read this doc is the second finding: attempting it exposes a
narrowing that **already** claims tightness it does not have, which the breadth guard has been hiding.

`o:the or o:you` takes **1,819 µs** and reports `narrowed_repr: none` — it does no narrowing at all, even though
both children narrow individually and (after #859) both are *exact*. The union of two exact sets is exact, so
this query should be answerable with no verification whatever.

## Why it declines, and the margin is 44 cards

`narrow_candidates_exact` discards the whole narrowing when the set is too broad:

```rust
let domain = if printing_space { n_printings } else { n_cards };
if n.set.len() <= domain - domain / 4 {
    (Some(n.set), n.tight && !printing_space, n.proven)
} else {
    (None, false, 0)
}
```

- threshold: `31,508 - 31,508/4` = **23,631**
- `o:the or o:you` union: **23,675**

**Over by 44 cards, 0.19%** — and the consequence is not a slightly worse plan, it is scanning all 31,508 cards
and running a full oracle-text `memmem` on each.

## The guard's reasoning inverts under tightness

The guard is right for a **loose** set: you pay union, projection and materialization, and then still verify
every candidate, so a near-total loose set is worse than no narrowing. It is wrong for a **tight** set, where
keeping it eliminates verification entirely. 23,675 candidates with no `card_pass` beats 31,508 with one full
oracle-text scan each, and it is not close.

## Measured — the win is real

Prototyped (#859's tightness plus `|| (n.tight && !printing_space)` on the guard), measured, reverted:

| query | before | after | |
| --- | --: | --: | --: |
| `o:the or o:you` routed | 1,819.3 µs | **415.2 µs** | **4.4×** |
| `o:the or o:you` end-to-end | 2,198.4 | **473.6** | 4.6× |
| `o:the or o:zap` | 732.2 | **201.5** | 3.6× |
| `o:tar or o:qua` | 850.3 | **172.7** | 4.9× |

`narrowed_repr` goes `none` → `card_bits`, and all 104 row-identity cells stayed identical.

## But it is NOT safe as written, and that is the actual finding

**`fuzz_row_identity_matches_reference` fails**, on a filter with no text predicate in it at all:

```
unplaned total mismatch (mode=card, orderby=rarity, dir=asc, seed=19,
                         filter=AND(cmc<8, colors!=0b00011))
  left: 15   right: 14
```

An **extra row**. #859 alone passes the full suite (153 tests); adding the guard change is what breaks it, and
the filter contains no text, so #859 is not implicated.

**#859 is not needed to reproduce it.** Verified separately: the guard relaxation *alone*, with #859's
tightness change absent from the tree, fails the same assertion on the same filter and seed. That follows from
the filter containing no text predicate, but it was measured rather than inferred — so this issue can be worked
on `main` as it stands, with no dependency on #859 for the reproducer.

**So some narrowing already claims `tight: true` without being exact, and the breadth guard has been masking
it** — the over-claimed set was being discarded for breadth before anything trusted it. `colors != …` is the
obvious suspect (`tight_narrow_space` classifies `ColorCmp` as tight-in-card-space unconditionally, and a `Ne`
over a colour bitmask has to get colourless/null exactly right to be exact), but that is a hypothesis, not a
diagnosis.

## What to do, in order

1. **Find the over-claiming narrowing.** It is a latent correctness bug today, not merely an obstacle: any other
   change that causes a tight set to be trusted where it currently is not would expose it the same way. The
   fuzz case above is a reproducer.
2. **Then relax the guard on tightness**, and re-run the fuzz suite rather than a hand-picked query set — mine
   was 104 cells across negations, conjunctions, disjunctions and all three distinct-ons, and it missed this
   entirely.
3. Consider asserting the invariant directly: a set marked tight should have every member pass `card_pass`.
   That turns "the guard happens to hide it" into a named failure, and it is the same debug assertion
   [#859](https://github.com/jbylund/sylvan_librarian/issues/859) and #857 both want.

Relationship to [#859](00859-engine-exact-trigram-no-verify.md): #859 supplies the tightness that makes the
`o:the or o:you` *union* exact, so the 4.4× **performance** win needs it. The **bug** does not — see above. Step
1 below is workable on `main` today.

## Reproducing

```bash
.venv/bin/python scripts/bench_short_needle_cliff.py --shm /tmp/needle.store   # shows narrowed_repr: none
rtk proxy cargo test --manifest-path card_engine/Cargo.toml fuzz_row_identity_matches_reference
```

The prototype was: #859's `mk = if word.len() == 3 { Narrowed::tight } else { Narrowed::loose }`, plus
`let worth_keeping = n.set.len() <= domain - domain / 4 || (n.tight && !printing_space);` in
`narrow_candidates_exact`. Both reverted; nothing is committed.

## Related

- [#859](00859-engine-exact-trigram-no-verify.md) — supplies the tightness. Safe on its own: full suite green,
  4.7× on `o:the`.
- [#858](00858-engine-short-needle-text-scan.md) — the 1-byte tier, unaffected by either.
- [#857](00857-engine-membership-merge-sorted-list.md) — wants the same class of debug assertion for its own
  ordering precondition.
