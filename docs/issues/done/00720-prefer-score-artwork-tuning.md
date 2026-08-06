# Prefer-Score Artwork Selection: Tuneable Weights and a Measured Objective

**DONE — [#720](https://github.com/jbylund/sylvan_librarian/issues/720) closed as completed 2026-07-26.**
Two changes shipped: the `art_style` component at weight 14 (visible in
[`api/sql/backfill_prefer_scores.sql`](../../../api/sql/backfill_prefer_scores.sql)) and the filtered
`illustration_count` numerator that excludes memorabilia and foreign-language rows.

**The remaining tuning work is [#855](../00855-prefer-score-remaining-tuning.md)** — child-set collapsing
(deferred for a missing `magic.set_parents` table, and the largest effect measured at 16,357 duplicate credits),
the 533-candidate `art_style` tag-set expansion, weight optimization, and lifting weights out of SQL.

**If you are here because of #720's last comment ("Should only get 1 printing credit per set"): that was built
and rejected**, at 28 better / 22 worse, with losses concentrated 17–1 on 7th–10th Edition `★` foils. See
"Counting rules that were built and rejected" below before retrying it.

This doc is the depth: what was measured, what was wrong, and why the shipped answer is not the one it looked
like for most of the investigation.

*Original status, kept for dating* — in progress; one component evidenced and ready to land.

Companion: [local-prefer-score-label-harness.md](../local-prefer-score-label-harness.md) — the labelling
instrument, its schema, sampling strategy, and fitting method.

## Why this cannot be hand-written

The governing fact, in the maintainer's words: *for any given card I can generally tell you which
printing I most prefer, but it is obviously hard for me to write down a scoring function that reproduces
that perfectly.*

A reliable oracle for the outputs, no closed form for the function. The scarce resource is the
maintainer's judgment, so the job is to harvest it cheaply and convert it into weights rather than keep
guessing at coefficients.

## The reported failures, and why they were misleading

Two cards showed a printing other than the one that should clearly win: **Pure Steel Paladin** (an
artwork with many surge-foil printings winning) and **Sword of the Animist** (a Universes Beyond art
with 2 printings beating one with ~9). Both trace to `illustration_count`, the only component that
refers to the artwork at all.

The arithmetic accounts for Sword of the Animist exactly: 2 → 9 printings is worth **7.51** points,
while `extended_art` alone is **12**. The term's *entire* range across 1 → 40 printings is **18.83** —
less than `frame` (42) or `language` (40).

But the fix was neither rescaling that term nor fixing its numerator. Chasing those two cards led to a
general finding they were only symptoms of.

## What the corpus actually looks like

Measured on blue (97,306 printings / 31,508 cards / 45,824 distinct artworks), English, basic lands
excluded.

| | |
| --- | --- |
| Multi-printing cards | 18,337 |
| Cards with ≥2 visually distinct looks | 13,816 |
| Multi-printing cards where every printing looks alike | 4,521 |
| Exact top-score ties | 8,597 (46.9%) |
| …where the tie spans different artworks | 384 (2.1%) |
| Cards where two *visually distinct* looks score identically | 1,697 (12.3%) |

Attributing the 4,245 tied look-pairs: **3,319 (78%) differ by artwork**, 410 by frame version, 365 by
an unscored frame treatment, 206 by border. The mechanism is that `illustration_count` is the only
artwork-referring component, so two arts of one card with matching reprint counts tie *exactly* — and
counts collide constantly. Two arts each printed once both score `23·ln(2)/ln(40)` = 4.32.

## The finding: off-style artwork

Community art tags (`art:`, 10,807 of them) describe the *picture*. Across labelled comparisons where
exactly one artwork carried an off-style tag:

| definition | on-style side chosen | rate |
| --- | --- | --- |
| 8 hand-picked style tags | 153/153 | 100% |
| `external-ip` raw | 117/125 | 94% |
| **`external-ip` minus `dungeons-and-dragons` / `the-lord-of-the-rings`** | **115/115** | **100%** |
| that OR `anime` / `comic-style` / `line-art` | 159/159 | 100% |
| **…and `word-art-title`** | **177/177** | **100%** |

Raw `external-ip` has **exactly 8 exceptions, and the two exemptions remove exactly those 8**. D&D and
Lord of the Rings are external IP whose art matches Magic's high-fantasy style; Fallout, Warhammer,
Doctor Who and Marvel do not. Adding the non-IP stylistic departures (anime, comic, line art, word-art-title) widens
coverage to 177 observations at no loss of accuracy. `word-art-title` was added last, on 18/18 evidence
from the stratified batch plus a direct statement of dislike; it newly demotes 247 artworks (88 of its
312 were already caught by `external-ip`) but moves only 4 cards, since few of them compete against an
untagged sibling.

`external-ip` is the Scryfall tagger's parent over ~57 licensed franchises
([tagger.scryfall.com/tags/artwork/external-ip](https://tagger.scryfall.com/tags/artwork/external-ip)) —
Assassin's Creed, Cowboy Bebop, Doctor Who, Fallout, Final Fantasy, Fortnite, Furby, Marvel, Monopoly,
Star Trek, Stranger Things, TMNT, Warhammer and so on. Of those, only D&D and Lord of the Rings were
judged consistent with Magic's core look.

**The exemption is complete, verified.** Every artwork carrying a sibling tag (`arda`, `hobbit`,
`abeir-toril`, `dnd-multiverse`) also carries its parent, so exempting the two parent tags catches all
Middle-earth and Forgotten Realms art — zero orphans. Without that check, a card tagged only `arda`
would have been silently demoted.

**One deliberate edge case.** The exemption applies to the `external-ip` clause only, not to the
style-departure clause, so Drizzt Do'Urden (AFR #338) — tagged `dungeons-and-dragons` *and* `line-art` —
is still demoted. A line-art rendering is a departure from the painted core style whoever owns the IP.
It is the single card in AFR affected, and the demotion was confirmed correct against the artwork.

Corpus reach: 7,311 artworks are `external-ip`, 5,472 after exemptions, **6,193** under the final
definition — 14% of all artworks.

### Why a tag and not a year

An era term tested well: 2003–2020 art wins **82%** of its cross-era comparisons, 2021+ wins **21.6%**.
Rejected because **it permanently penalises all future art** — every future set lands in the disliked
bucket by construction, needing perpetual re-tuning. A tag on the artwork generalises (new anime-styled
art gets tagged; new core-style art does not) and *explains* the era correlation rather than restating
it, since the crossover sets are recent. It also expresses the D&D/LotR distinction, which no year term
or "is Universes Beyond" flag can — UB (27.8%) and 2021+ non-UB (21.7%) are statistically
indistinguishable, so a UB flag would be both weak and wrong-shaped.

## What was tried and did not survive

Recorded because the failures were more instructive than the successes, and several were convincing at
the time.

- **`art_age` as a linear feature.** Batch 1 said it beat the whole scoring function (73.3% vs 65.2%
  cross-validated) and it was reported as the strongest result in the analysis. On the cleaner batch-2
  labels it **reversed** — `art_pop` won at 76.7% and the baseline rose to 75.5%. Two causes: batch 1
  showed whole cards, so a preference for older *frames* masqueraded as one for older *artwork*; and the
  preference is non-monotonic (peak 2003–2020), which a linear term with a declared non-negative sign
  cannot represent at all.
- **A monotonic sign for age.** Declaring signs presumes monotonicity. For age that was false, and the
  constraint actively prevented the model fitting the truth.
- **Artist identity.** 94.2% of within-card artwork pairs differ by artist, but 84% differ by artist
  *and* age together; only 793 corpus pairs vary age with artist held constant. Artist prominence and
  reuse were weak alone (63.5% / 67.9%). Prominence and total printings correlate at 0.986, so they
  cannot both be fitted; reparameterising as (prominence, printings-per-artwork) drops that to 0.697.
- **Perceptual hashing to filter near-duplicate artworks.** Built and calibrated. Only 3 of 183
  labelled pairs fell in the "same art" band, and `other` verdicts spread evenly across distance
  (median 43.5 vs 45.6 for decided), so the high no-preference rate is genuine indifference, not a
  filtering failure. Superseded by cheaper exact filters: excluding promos (Scryfall gives a promo
  recolour its own `illustration_id`) and the `dbl` set (530 cards reprinted in black and white).
- **Lowering `extended_art`.** See below.
- **A partial-credit tier for Portal Three Kingdoms.** `romance-of-the-three-kingdoms` is external IP by
  the tagger's reckoning but a 1999 Magic set, not a modern crossover — judged "ok, not great", so a
  middle tier was built and measured. Dropped: across the entire range from 0 to full credit it moved
  only **5 cards** (3 at the candidate weight of 7, and 7 vs 8 were identical). Most P3K cards were never
  reprinted, so their art has nothing to compete against. Not worth a third tier for a distinction that
  changes nothing visible.

## The `extended_art` cautionary tale

Reviewed three times, three different answers:

| review | result |
| --- | --- |
| 12 → 10.5, art crops shown | 4 better / 5 same / **0 worse** — looks like a free win |
| 12 → 10.5, whole cards shown | 4 better / 1 same / **4 worse** — a wash |
| 12 → 10, whole cards, larger step | 3 better / **29 worse** — **net −26** |

The first reading was an artifact: extended art is a *frame* property, and the page was showing art
crops, in which an extended-art printing and its normal sibling are identical images. Every real
disagreement was forced into "no difference." Trusted, it would have shipped a clearly harmful change
on an apparent zero-regression result.

Two rules now encoded in the tooling: **show whole cards when the decision is about a printing**, and
**a step too small to move enough cards yields no information** (12 → 10.8 moved 6 cards).

## `art_style`: the evidence

Swap review at each weight, judged blind, on whole cards:

| step | better | same | **worse** |
| --- | --- | --- | --- |
| 0 → 6 | 28 | 24 | **0** |
| 6 → 9 | 13 | 1 | **0** |
| 9 → 14 | 37 | 11 | **0** |
| **cumulative** | **78** | **36** | **0** |

**114 swaps, not one regression.** The better-rate rises with weight (54% → 93% → 77%), which is
mechanically sensible: a small weight only flips near-ties, which read as "same"; a larger one overcomes
real gaps where an off-style printing was winning decisively on other components. Saturation is ~150
cards at weight 30, so 14 captures most of the available effect.

## It fixes both cards that opened the issue

| card | current | with `art_style=14` |
| --- | --- | --- |
| **Puresteel Paladin** | `pip/456` — Fallout | `cmm/51` |
| **Sword of the Animist** | `msc/452` — Marvel | `plst/ORI-240` |

Both were showing a crossover-universe printing, and neither appeared in any labelled batch, so this is
genuine out-of-sample validation. Note the original diagnosis of Puresteel Paladin was "lots of surge
foil printings inflating the count" — surge foil is a *Fallout* treatment, so the reported symptom was
real but pointed at `illustration_count` when the cause was the crossover set.

## Two interaction findings

**Changes are entangled.** `art_style=6` alone moves 53 cards, `extended_art=10` alone moves 34, their
union 86 — but **jointly they move 101**. Fifteen cards move only when both weights move, and no
one-component-at-a-time loop can reach them.

**Ties dominate small steps.** With 46.9% of multi-printing cards exactly tied at the top, any epsilon
on a component that differs between tied printings flips them all at once, so swap count jumps
discontinuously from zero. Swaps must be split into **tie-breaks** (old pick was arbitrary; almost any
change is an improvement) and **overrides** (a strict ranking reversed — the ones worth eyeballing).
Step search targets overrides only.

## What ships

One new component, nothing else changed. `extended_art` stays at 12.

```sql
'art_style', (
    CASE WHEN NOT (
        (card_art_tags ? 'external-ip'
         AND NOT (card_art_tags ?| ARRAY['dungeons-and-dragons','the-lord-of-the-rings']))
        OR card_art_tags ?| ARRAY['anime','comic-style','line-art']
    ) THEN 14 ELSE 0 END
),
```

Expressed as a bonus for being on-style rather than a penalty on being off-style, so every component
stays non-negative, matching the rest of the table.

## Open — all four items now tracked as [#855](../00855-prefer-score-remaining-tuning.md)

Kept here because the evidence behind each one is in this doc; #855 carries the sequencing and the reason (2)
and (3) are blocked on the labelling tooling rather than on scoring judgement.

- **The tag set is partly a hypothesis.** 533 further candidates exist — art tags whose artworks
  concentrate in few sets, a measurable proxy separating style/setting tags from content tags like
  `sky` or `fire` (content tags span 450–520 sets, style tags 2–53). Scanning them needs more than 517
  observations without multiple-comparison inflation, and the filter has known false positives
  (`ghirapur-grand-prix`, `cho-arrim` — set-specific Magic content, not style).
- **Weight 14 is evidenced, not optimal.** Every step has been free; the stopping signal is the first
  "worse" verdict.
- **The printing count is filtered but not deduplicated.** One artwork can still take several credits
  from a single set: Universes Beyond sets print four treatments of one picture, and 7th–10th Edition
  model the foil as a separate `★` collector number. Collapsing to one credit per set was built and
  reviewed and did **not** survive — see below.
- **Feature/weight separation lives only in the Python tooling.** The SQL still fuses extraction and
  weights, so coefficient search needs the scripts. Lifting weights into config is the remaining
  refactor.

## The second change: what the printing count counts

`illustration_count` is a proxy for how canonical an artwork is, but its numerator counted every row
sharing the illustration — including rows that are not printing events. Mark Poole's Birds of Paradise
art counted 21: eight memorabilia (`30a` ×2, `ced`, `cei`, `ptc`, `wc00` ×2, `wc98`) and two
foreign-language (`4bb`, `fbb`), leaving 11 real printings against Marcelo Vignali's 12. Filtering
those three categories reverses which artwork the card shows.

Border alone is not the discriminator: four of Poole's eight memorabilia rows are *black*-bordered, so
a border test misses half. `set_type = 'memorabilia'` catches all eight. White borders are deliberately
kept — `2ed`–`6ed` are real core sets, and dropping them would penalise the reprints the component
should reward.

Evidence: a 47-card blind swap review returned **11 better, 36 same, 0 worse**; a later 378-card review
against production added 2 more with no regressions. It changes only the numerator — those printings
can still be displayed.

## Counting rules that were built and rejected

- **One credit per set (dedup).** Principled — a set including an artwork twice is one editorial
  decision — but reviewed at **28 better / 22 worse**. Losses concentrated 17–1 on 7th–10th Edition
  `★` foils, where collapsing the foil twin removed the only thing separating a core-set artwork from
  an older alternative. Never independently earned its place.
- **Excluding `promo` sets.** Zeroes the count for 1,018 artworks that exist only as promos, telling
  the score they were never printed. Rejected on that alone.
- **Collapsing child sets into parents** (`blc`→`blb`). The largest effect measured — 16,357 duplicate
  credits across 10,502 artworks — but `parent_set_code` is absent from our data and would need a
  `magic.set_parents` table. Release date is a usable proxy (87 of 91 changes) but systematically fails
  on late-released promos (`pltc` is 16 months after `ltr`). Deferred, not rejected.

## Frame and finish: two corrections worth recording

Reviewers of this work should know two hypotheses failed in instructive ways.

**Frame weights cannot be moved one at a time.** The ladder is 1993=10, 1997=25, 2003=30, 2015=42, and
every single-weight change perturbs two gaps. Raising `frame_2003` to widen 1997↔2003 also narrowed
2003↔2015 and was reviewed at 8 better / 22 worse. Lowering `frame_1997` instead narrowed 1993↔1997,
sending 161 of 179 swaps to the *oldest* frame. Only moving both old frames together isolates the gap.

**Foil is not invisible, and confounded batches said it was.** Six accidental foil-vs-nonfoil pairs came
back 6/6 "no difference", suggesting the `finish` component could be deleted. A deliberately controlled
batch — same card, set, artwork, frame, border, rarity, scan quality, promo types and stamp, differing
only in finish — returned **24 nonfoil, 0 foil, 26 same**. The earlier reading was small-sample noise.
Only 162 such controlled pairs exist corpus-wide; everything else that looks like a foil pair is a
special foil or a different promo product.

## Related

- [local-prefer-score-label-harness.md](../local-prefer-score-label-harness.md) — labelling instrument.
- [00707-engine-3key-ordering-parity.md](00707-engine-3key-ordering-parity.md) — `prefer_score` is the
  third sort key and where plans may diverge.
- [`api/sql/backfill_prefer_scores.sql`](../../../api/sql/backfill_prefer_scores.sql) — the scoring
  definition.
