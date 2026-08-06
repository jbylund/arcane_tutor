# `card_types` Has No `Battle`, and `t:battle` Poisons an `Or`'s Plane Path

Status: proposed, not started — **and (1) below may be a wrong-answer bug, which is why it outranks
everything else carried out of that audit.** Filed as
[#850](https://github.com/jbylund/sylvan_librarian/issues/850). Items 2 and 3 of
[the `is:` / `frame:` audit](done/local-engine-is-frame-predicates.md), whose other findings shipped in
#840 and #842.

## 1. The data question — check this first

Counting `card_types` across the corpus gives 12 distinct names and **`Battle` is not among them**:

    Creature 45,976   Legendary 13,537   Land 11,552   Artifact 10,949   Instant 10,725
    Sorcery 10,626    Enchantment 9,914  Basic 4,196   Planeswalker 1,379  Snow 262
    Kindred 183       World 42

The corpus carries release dates through 2025-02-14 and battles arrived with March of the Machine in 2023,
so they should be present. If the live store looks the same, `t:battle` silently returns nothing and
**`is:permanent` misses every battle** — a wrong answer, not a slow one.

Check the importer's type extraction before treating this as a corpus artifact. It is cheap to check, and
it decides whether (2) is even the right framing: fixing the plane path would still return zero battles.

## 2. The plane path

`is:permanent` desugars to `t:creature or t:artifact or t:enchantment or t:land or t:planeswalker or
t:battle`, and that last disjunct costs **11.5×**:

| query | count source | plan | µs |
| --- | --- | --- | --: |
| 5 disjuncts (through `t:planeswalker`) | plane | PlanePopcountOrder | **55** |
| … `or t:snow` (6 disjuncts) | plane | PlanePopcountOrder | 57 |
| `t:instant or t:sorcery or t:snow or t:world or t:basic or t:kindred` (6) | plane | PlanePopcountOrder | 47 |
| … `or t:battle` (6 disjuncts) | **candidates** | StreamedSelect | **497** |
| `t:battle or t:creature` (2 disjuncts) | **candidates** | StreamedSelect | 90 |
| `is:permanent` | **candidates** | StreamedSelect | **641** |

**Two explanations are ruled out by that table.** It is not the disjunct count — six other-type disjuncts
stay on the plane path. And it is not a missing type plane: `TYPE_PLANES = 14` covers every bit including
`TYPE_BATTLE = 1 << 2`, and `PERMANENT_TYPES` includes it.

**The mechanism is not identified.** `t:battle` alone acquires as `printing_compose` returning 0 rows in
3.5 µs, which is already odd for a `TypeCmp` — that is the thread to pull. Do not guess; two plausible
mechanisms are already eliminated above.

Whatever it turns out to be, the fix is probably algebraic rather than local: an empty or non-compilable
disjunct should be **dropped** from an `Or` (`Or(x, ∅) = x`) rather than poisoning the whole expression.
That generalizes past `Battle` and is the version worth shipping.

## Also carried over: the `is:old` / `is:historic` gap

Recorded here so it is not lost with its parent doc, and it may explain more than it looks like.

`is:old` narrows to **7,191** cards and takes **399 µs**. `is:historic` narrows to **7,261** cards — 0.4%
more — and takes **59 µs**. Same field, same count source, near-identical evaluation domain, **6.8×** apart.
Both are `Or`s over `card_frame_data`.

Nothing in the audit explains it, and whatever makes `is:historic` fast is presumably available to `is:old`.
Diagnosis only; it may share a cause with (2), since both are `Or`s that route differently than their
siblings.

## Traffic caveat

`is:` is absent from `REALISTIC_FAMILY_WEIGHTS`, so the 24%-of-dispatch figure that motivated the parent
audit is a **uniform-mode** number and the realistic share is unmodelled. That caveat sizes (2); it does not
apply to (1), which is a correctness question regardless of how often the query is typed.

## Related

- [done/local-engine-is-frame-predicates.md](done/local-engine-is-frame-predicates.md) — the parent audit,
  the `frame_data` hybrid that shipped, and the threshold-conflation fix.
- [local-engine-layout-postings.md](local-engine-layout-postings.md) — item 4 of the same audit,
  deprioritized separately.
- [local-engine-empty-text-narrowing.md](local-engine-empty-text-narrowing.md) — `is:vanilla` /
  `is:permanent` from the other direction: a plane-composable `Or` falling back to the general path.
