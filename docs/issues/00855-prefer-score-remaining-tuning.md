# Prefer-Score Artwork Selection: The Tuning Work Left After `art_style`

Status: open, nothing started. Filed as
[#855](https://github.com/jbylund/sylvan_librarian/issues/855). Split out of
[#720](done/00720-prefer-score-artwork-tuning.md), which closed as completed when `art_style` shipped.

**Read the parent record before touching any of this.** Three hypotheses here were built, reviewed and
rejected, and a fourth was deferred for one specific missing table. Each re-derivation costs a blind review
round, and the parent doc has the review counts.

## Already rejected — do not simply retry

**One printing credit per set.** [#720's own last comment](https://github.com/jbylund/sylvan_librarian/issues/720)
asks for exactly this, so it is the first thing anyone will reach for. It was built and reviewed at **28 better
/ 22 worse**, and the losses concentrate **17–1 on 7th–10th Edition `★` foils**, where collapsing the foil twin
removes the only thing separating a core-set artwork from an older alternative. It never independently earned
its place.

A future attempt has to handle that population specifically rather than re-run the same rule. The principle is
sound — a set including an artwork twice is one editorial decision — which is what makes it tempting twice.

**Excluding `promo` sets.** Zeroes the count for 1,018 artworks that exist only as promos, telling the score
they were never printed. Rejected on that alone.

## Live work

### 1. Collapse child sets into parents — deferred, not rejected, and the largest effect measured

`blc`→`blb` and similar. **16,357 duplicate credits across 10,502 artworks** — a larger effect than anything
that shipped in #720.

Blocked on data rather than judgement: `parent_set_code` is absent from our data and would need a
`magic.set_parents` table.

Release date is a usable proxy — 87 of 91 changes correct — but **systematically fails on late-released
promos**: `pltc` lands 16 months after `ltr`. So the proxy is not a substitute for the table; it is a way to
prototype the win and size it before paying for one.

This item is independent of everything below and can go first.

### 2. Expand the `art_style` tag set — needs statistical power first

The shipped tag set is **partly a hypothesis**. 533 further candidates exist, found by a measurable proxy: art
tags whose artworks concentrate in few sets separate style/setting tags from content tags. Content tags span
**450–520 sets**; style tags span **2–53**.

Two real obstacles:

- Scanning 533 candidates needs more than the **517 observations** the shipped component rests on, without
  multiple-comparison inflation.
- The proxy has **known false positives** — `ghirapur-grand-prix`, `cho-arrim` — set-specific Magic *content*,
  not style. So the proxy nominates candidates; it does not decide them.

### 3. Weight 14 is evidenced, not optimal

Every step up has been free, and the stopping signal is the first "worse" verdict. Finding the actual optimum
is a coefficient search, not another hand-reviewed step — and see the frame-ladder trap below for why it has to
be a *joint* search.

### 4. Lift weights out of SQL — the enabler for (2) and (3)

Feature/weight separation currently lives only in the Python tooling.
[`api/sql/backfill_prefer_scores.sql`](../../api/sql/backfill_prefer_scores.sql) still fuses extraction and
weights, so any coefficient search has to run through the scripts. Lifting weights into config is the remaining
refactor, and it is what makes (2) and (3) tractable rather than artisanal.

## Sequencing

**(1) first** — independent data work with a known payoff and no tooling dependency.

**(2) and (3) are blocked on tooling, not on this issue.** The labelling instrument and the closed loop are the
prerequisite: [label harness](local-prefer-score-label-harness.md) and
[the closed tuning loop](local-prefer-score-tuning-loop.md). The latter exists precisely because #720's
analysis was **wrong three separate times** without one. Do **(4)** alongside whichever of those lands, since
it is what a coefficient search needs.

## Two traps from the parent, both load-bearing here

**Weights cannot be moved one at a time.** The frame ladder is 1993=10, 1997=25, 2003=30, 2015=42, and every
single-weight change perturbs *two* gaps. Raising `frame_2003` to widen 1997↔2003 also narrowed 2003↔2015 and
reviewed at 8 better / 22 worse; lowering `frame_1997` instead narrowed 1993↔1997 and sent **161 of 179 swaps
to the oldest frame**. Only moving both old frames together isolates a gap. This is a direct argument for (3)
being a joint search rather than a ladder of single steps.

**Confounded batches lie, and they lied in the reassuring direction.** Six accidental foil-vs-nonfoil pairs came
back 6/6 "no difference", suggesting the `finish` component could be deleted outright. A deliberately
controlled batch — same card, set, artwork, frame, border, rarity, scan quality, promo types and stamp,
differing *only* in finish — returned **24 nonfoil, 0 foil, 26 same**. Only 162 such controlled pairs exist
corpus-wide, so any component this small needs a built batch rather than found pairs.

## Related

- [done/00720-prefer-score-artwork-tuning.md](done/00720-prefer-score-artwork-tuning.md) — what shipped, the
  rejected hypotheses with their review counts, and the corpus analysis.
- [local-prefer-score-label-harness.md](local-prefer-score-label-harness.md) — the labelling instrument and
  weight fitting.
- [local-prefer-score-tuning-loop.md](local-prefer-score-tuning-loop.md) — the closed loop: propose, grade ~20
  cards, accept or reject, with one number going up.
- [done/00707-engine-3key-ordering-parity.md](done/00707-engine-3key-ordering-parity.md) — `prefer_score` was
  the third sort key; the engine has since dropped key 3 from cross-card comparison, though SQL still carries
  it.
