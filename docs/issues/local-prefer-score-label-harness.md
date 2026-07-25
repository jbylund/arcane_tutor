# Prefer-Score Label Harness: Pairwise Screening and Weight Fitting

Status: proposed, not started. Split out of
[00720-prefer-score-artwork-tuning.md](00720-prefer-score-artwork-tuning.md) once it became clear this
is an independent deliverable: 00720 is the scoring defect, this is the instrument for fixing it and for
preventing the next one. Not filed as a GitHub issue of its own — it is the first step of #720.

## Why an instrument is needed at all

The governing fact, in the maintainer's words: *for any given card I can generally tell you which
printing I most prefer, but it is obviously hard for me to write down a scoring function that reproduces
that perfectly.*

That is a favourable situation — a **reliable oracle for the outputs** with **no closed form for the
function** — and it dictates the approach. The scarce resource is the maintainer's judgment, so the job
is to harvest it cheaply and convert it into weights, rather than to keep guessing at coefficients.

## Collect pairwise judgments, with an explicit "no preference"

Show **two** printings; the answer is *a*, *b*, or *other*.

`other` absorbs both "they are equally good" and "I cannot tell". Collapsing them is fine for a first
pass, but note it is the **one irreversible decision** in this design: every other choice here can be
revisited because labels re-score against any future config, whereas a distinction never recorded cannot
be recovered for labels already collected. If `other` turns out frequent, splitting it costs a re-label
of that batch. One extra button now is the cheap insurance.

Also: **do not show the labeler the scored features** (first-use date, reprint count) before the verdict.
Those are the model's inputs; displaying them makes the label partly a function of them, so the fit would
learn a rule the labeler inferred from the same numbers being fitted. The label has to be an independent
target. An optional *reveal-after-verdict* mode is safe and useful — the rate at which the labeler would
change their mind is itself a measurement of how much of "iconic" is visual versus statistical.

Not a grid of all printings with one click for the winner. Three reasons, in increasing order of
importance:

1. **It is the model's native input.** The fitting surrogate is `P(i beats j) = sigmoid(w·(x_i − x_j))`
   — literally a function of pairs. Pick-best-of-k yields a partial ranking that has to be decomposed
   into pairs anyway, or else needs the Plackett-Luce generalization.
2. **Lower cognitive load per decision.** Two images compare reliably; nine invite decision fatigue,
   and fatigue is noise in the only signal that matters.
3. **"No preference" is information no forced choice can produce.** Making the maintainer pick between
   two printings they are genuinely indifferent about *manufactures* a preference. Given that 46.9% of
   multi-printing cards are exact scoring ties, and 4,521 multi-printing cards have no visually distinct
   printings at all (00720's measurement table), knowing *which* indifferences are real is precisely the
   missing signal.

### Ties are a modelled outcome, not discarded data

Standard Bradley-Terry has no tie outcome, but the extensions do: **Rao-Kupper** (1967) adds a threshold
so ties occur when the latent score difference falls inside a band, and **Davidson** (1970) adds an
explicit tie propensity parameter. The Rao-Kupper formulation is the more useful one here because its
threshold has a direct reading: **the just-noticeable difference** — how large a score gap has to be
before the maintainer actually cares.

That threshold is a valuable output in its own right, independent of the weights. It tells you the
resolution of the scoring function; gaps below it are noise, and a deterministic tiebreak (00720 step 2)
is the right answer for pairs that fall inside it rather than more weight tuning.

## Which pair to show

Any two printings of a card that has multiple — **not** restricted to the top-2 by current score.
Restricting to top-2 makes the sampler *config-dependent*: it asks "is the current pick right?", which is
useful validation but biases the label distribution toward today's weights, and labels are meant to
outlive configs. Arbitrary pairs give better coverage of the feature space and stay valid regardless.

### Screen out pairs that look identical

The wasted click is not the low-margin pair, it is the **visually indistinguishable** one. If two
printings share artwork, border and frame treatment, there is no preference to elicit and asking
manufactures noise. So group printings by a **visual signature** —
`(illustration_id, border, frame-treatment keys)` — and only ever show pairs of *distinct signatures*.

`finish` is excluded from the signature: a foil and a nonfoil of the same art share the same scan image,
so they are visually identical even though the physical cards differ.

Measured pool (blue, English, basic lands excluded):

| looks per card | cards | pairs |
| --- | --- | --- |
| 1 — never screen | 17,688 | 0 |
| 2 | 8,170 | 8,170 |
| 3 | 2,783 | 8,349 |
| 4–6 | 2,172 | 18,533 |
| 7–12 | 584 | 19,524 |
| 13+ | 107 | 21,113 |

**Start with the 8,170 two-look cards.** One click each, no sampling decision, and each click is a
*complete* answer for that card — they are 59% of the 13,816 screenable cards.

**Sample cards, then pairs within a card — never pairs uniformly.** The 107 cards with 13+ looks
generate 21,113 pairs: 28% of the whole pool from 0.8% of the cards. Uniform pair sampling would spend
most of the labeling budget on a handful of heavily-reprinted cards.

Beyond that, in rough order of information per click:

| Sampler | Why |
| --- | --- |
| Two-look cards | One click resolves the card completely |
| Config disagreement | Each label directly discriminates between two candidate configs |
| Exact ties across distinct looks | Currently settled by store order, so any label is new information |
| Small margin | Where the function is least confident |

### Prefer pairs that differ in exactly one dimension

The strongest available lever: choose pairs differing in **one** scored dimension, holding everything
else constant. That turns collection from observational into a designed experiment — the feature
difference vector has a single nonzero entry, so the label identifies that dimension with no confounding
and means exactly "this frame is better than that one, all else equal".

Measured availability over the 75,689 distinct-look pairs:

| dimensions differing | pairs |
| --- | --- |
| **1** | **28,895** |
| 2 | 23,483 |
| 3 | 18,223 |
| 4 | 5,088 |

Single-dimension breakdown: **artwork only 12,763**, frame version 7,482, treatment 5,795, border 2,855.

**But sign-discovery is not what labels are needed for.** The maintainer already knows the signs — which
frame is preferred, which border — and what is genuinely unknown is *how to weight them against each
other*. So single-dimension pairs mostly buy information already available for free, and the phase that
would collect them is dropped.

That is fortunate, because they could not have supplied magnitudes anyway. With `P = sigmoid(w·Δx)` and
one nonzero entry in `Δx`, a deterministic answer drives that coefficient toward infinity — the MLE is
degenerate. Regularization tames the divergence but then the *prior*, not the data, sets relative
magnitudes.

### Declare the signs; fit only the magnitudes

Record the known orderings as configuration — `2015 > 2003 > 1997 > 1993`, `black > white`,
`nonfoil > foil > etched`, `extended_art > 0` — and fit subject to them. This:

- removes the degeneracy, since only magnitudes remain free;
- shrinks the unknowns to the **exchange rates**, roughly *n−1* numbers for *n* dimensions (overall scale
  being arbitrary) — call it 8–12 rather than 15–20;
- guarantees the fit can never contradict a known preference, which is what keeps the output auditable;
- directs every label at something actually unknown.

Implementation needs no constrained solver: **reparameterize as non-negative increments.** Pin an
ordinal's worst level at 0 and express each step as `softplus(θ)`, so `w_1997 = softplus(θ₁)`,
`w_2003 = w_1997 + softplus(θ₂)`, and so on; boolean sign constraints use the same trick. The problem
stays unconstrained in `θ`, so any gradient optimizer works. This is monotone / ordered logistic
regression.

**Test the declarations.** Once conflict labels exist, check whether any contradicts a declared ordering.
A contradiction means either the declaration is wrong or that label is noise, and both are worth knowing.
Note also that a violation may indicate a missing *interaction* rather than a wrong sign — "I prefer 2015"
may really be "I prefer 2015 usually, but a retro frame suits older art". If violations cluster in a
subset rather than scattering, that is evidence for a context term.

### So labels are for exactly two things

1. **Exchange rates** — the 23,483 two-dimension **conflict** pairs, where dimensions oppose (better
   frame, less-reprinted art). This is precisely the reported bug: `extended_art` = 12 against 7.51 points
   of popularity. A handful of well-chosen pairs brackets each rate; closer to a binary search than to
   bulk collection.
2. **What predicts artwork preference** — the art-only pairs below. The one dimension where no sign can be
   declared, because "better art" is not a monotone function of any feature currently available.

### The art-only pairs are the first experiment to run

The 12,763 pairs differing *only* in artwork are pure artwork preference with everything else held
constant, and the only current score difference across them is `illustration_count`. So labeling a sample
answers a question with no fitting at all: **does reprint count predict artwork preference better than
chance?** Given that artwork identity is otherwise invisible to the score (00720), a negative result is
immediate evidence for `art_first_appearance`, obtainable before any harness exists beyond the labeling
page itself.

### Labels key on the signature, not the printing

Because within-signature printings are visually identical, a verdict applies to the whole signature
group. That decomposes the problem cleanly: **labels choose between looks; the deterministic tiebreak
chooses a printing within a look.** It is also why the corpus's 46.9% exact-tie rate is mostly harmless
(00720) — most of those ties are within a single look.

### A later refinement: perceptual hashing

The attribute tuple is a proxy for "do these look different", and a good one, at zero cost — no image
fetching. A perceptual hash over the CDN images would be ground truth and would catch cases the tuple
misses (a re-cropped or recoloured variant sharing an `illustration_id`, or two distinct
`illustration_id`s that are near-identical). Worth doing only if the tuple proves insufficient in
practice; it adds an image-fetch pipeline for a refinement of a filter, not a new capability.

## Label schema

```
(card_key, look_a, look_b, shown_printing_a, shown_printing_b,
 verdict, labeler, labeled_at, look_set_hash, dims_differing)
verdict ∈ {a, b, other}
```

`look_a` / `look_b` are the visual signatures being compared — that is what the verdict is *about*. The
`shown_printing_*` ids are kept only for provenance (which exact image was on screen), since any printing
within a look is interchangeable by construction.

- **Record the pair and the verdict, never "beat the current config's pick".** A label phrased relative
  to a config encodes that config and dies the moment a weight changes. A pair verdict re-scores against
  any future config indefinitely. This single decision determines whether the labeling effort survives
  the first refactor.
- **`look_set_hash`** over the card's distinct looks at label time. A genuinely new *look* should
  re-surface the card; a new printing that merely duplicates an existing look should not, which is a
  further reason to key on looks rather than printings.
- **Keep every verdict, including `other`.** It is training data for the tie threshold, not a skipped
  card — with the caveat above that `other` conflates indifference with uncertainty.
- **`dims_differing`** records which scored dimensions differed for this pair, so the two phases below
  can be analysed separately without recomputing signatures.
- **Allow re-labeling** and keep the history rather than overwriting. Disagreement between a maintainer's
  own labels over time is the measurement of labeling noise, and the fit needs to know that number.

## Where to store it

Undecided, and worth deciding before building:

- **Checked-in file** (JSONL under `docs/` or a `labels/` directory) — reviewable in PRs, portable
  across environments, trivially diffable, no schema migration. Awkward to append to from a web page.
- **Postgres table** — natural to write from the labeling page, queryable alongside `magic.cards` for
  the samplers. Needs a migration, and the labels then live only in whichever environment collected
  them.

A file is probably right initially: the volume is small (thousands of rows at most), the review value is
real, and the samplers can join against a temporary import.

## The tool needs no new API surface

Every field required is already exposed, and image URLs derive client-side from `set_code` +
`collector_number` against the CDN exactly as `app.js`'s `buildImageUrl` does:

```
/search?q=!"Sword of the Animist"&unique=printing
       &fields=scryfall_id,set_code,collector_number,illustration_id,prefer_score
```

So it is a static page plus label persistence. The samplers need one query over `magic.cards` (the
measurement queries in 00720 are the same shape).

## Fitting: two optimizations, not one

**Agreement rate is a step function.** It is the fraction of judgments a config reproduces; as the
weights vary continuously the ordering only changes when two printings' scores *cross*, so agreement is
piecewise constant and its gradient is zero almost everywhere. It cannot be gradient-descended. That is
exactly why the logistic surrogate exists — its log-likelihood *is* smooth in `w`.

So use both, in order:

1. **Fit** the Rao-Kupper / logistic model to get a good `w` and the tie threshold, cheaply and with
   gradients. Optimizes a proxy.
2. **Evaluate** with agreement rate on **held-out** labels. Optimizes nothing; it is the honest number.
3. **Locally scan** around the fitted point (Nelder-Mead, random restarts, CMA-ES) to recover what the
   surrogate's mismatch cost. Grid search is out — exponential at 15-20 parameters.

Step 3 is only affordable because of the feature/weight separation in 00720 step 4: with a materialized
feature matrix, re-scoring a candidate config is a matrix-vector product over the contested cards —
milliseconds — rather than a full-table `UPDATE` plus an engine reload. Without that separation a scan
is infeasible and only step 1 is available.

### Encoding

- **One-hot the ordinals** (`frame`, `rarity`, `finish`, `border`) so each level is learned
  independently rather than inheriting today's hand-imposed spacing — including discovering whether
  common and uncommon genuinely tie at 16, as the current weights assert.
- **Supply the reprint-count curve as basis functions** (`ln n`, `sqrt n`, `n`, `ln(distinct_sets)`,
  `art_first_appearance`) rather than one fixed transform. This also **adjudicates 00720's numerator
  question empirically** instead of by argument.
- **Fit within-card only.** Every comparison is between printings of the same card, so card-level
  attributes cancel and cannot leak into the weights. This is a grouped/conditional fit.

### What cannot be learned

Comparisons are within-card, so **a feature whose value never varies within a card is unidentifiable at
any label volume**. Three current components are at risk, and they share a shape:

| Component | Varies within a card? |
| --- | --- |
| `language` (en: 40) | Only if foreign printings are shown |
| `has_paper` (6) | Only for cards with digital-only printings |
| `artwork_set` (not `dbl`: 20) | Only for cards printed in that one set |

All three are **exclusions dressed as scores** — "never show a foreign printing". `language` at 40
dominates nearly every other component precisely because it is enforcing a filter rather than expressing
a preference. Converting them to hard filters removes them from the fit and leaves scoring to genuinely
comparable candidates, which also makes the remaining weights readable. Worth doing regardless of
fitting.

And fitting cannot invent features. If no feature encodes iconicity, agreement plateaus and the answer
is a new feature, not more labels.

## Guards

**Hold out labels.** With 15-20 parameters and a few hundred labels, tuning to labeling noise is the
obvious failure mode. There is direct precedent in this repo: the cost-model calibration in
`card_engine/src/tests.rs` fits its coefficients with a **70/30 train/test split by query index**
specifically "so held-out fidelity reveals overfitting", and names the hazard "the 22-query trap". Same
discipline: report held-out agreement, treat train-set agreement as diagnostic only.

**Report the hard subset separately.** If most labeled cards have an obvious answer, every config scores
about the same and overall agreement cannot discriminate. Report agreement on config-disagreement and
small-margin cards apart from the overall rate.

**Blast radius is a separate number.** Agreement is measured on hundreds of labels; the corpus is 97k
printings. A config can satisfy every label while moving thousands of unlabeled cards. So also report,
for two configs, how many cards change representative printing plus a sample of which — agreement says
"right where we know", blast radius says "how much else moved".

## Rejected

- **Grid pick-best-of-k as the primary format.** Kept as a possible secondary for cards where a pair is
  genuinely ambiguous, but it forces a choice where indifference is the truth, and it does not produce
  the tie data the threshold needs.
- **Gradient-boosted variants (LambdaMART and similar).** Interpretability is a real requirement here —
  a human has to read the weights and agree with them — and a tree ensemble does not offer that. The
  linear fit gets most of the benefit and keeps it.
- **Per-card overrides.** Treats the symptom, unbounded manual burden, and hides scoring bugs rather
  than surfacing them.
