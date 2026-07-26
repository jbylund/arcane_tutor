# A closed tuning loop for `prefer_score`

The tuning in [#720](00720-prefer-score-artwork-tuning.md) worked but the *process* did not. Each
change took a bespoke script, a hand-built review page and a hand-written analysis, and the analysis
was wrong three separate times. This is a design for replacing that with one loop: propose a change,
grade ~20 cards, accept or reject, repeat — with a single number going up.

## The objective already exists

`gen_labeller.py` produced 202 cards where the artwork was chosen directly from all distinct artworks
for that card. That gives an accuracy measure: **does the score's top pick match the chosen artwork?**

| config | agreement |
| --- | --- |
| production today | 160/202 = 79.2% |
| `+ art_style=14` | 168/202 = 83.2% |
| `+ count filter` (#766) | 168/202 = 83.2% |

This is the hit rate to drive up. It is cheap (a dot product over cached levels), it is not the thing
being tuned against directly if a holdout is kept, and it makes "did that help" answerable in
milliseconds instead of a labelling session.

## The loop

1. **Propose.** Pick a component and a magnitude that moves ~20 cards. `step` already binary-searches
   for a target swap count; the loop drives it over every component in turn rather than one by name.
2. **Pre-screen on the objective.** Compute the hit rate. A candidate that lowers it on the labelled
   set is discarded without spending any grading — most will be.
3. **Grade 20.** Only survivors reach a review page: blind, randomised sides, whole cards.
4. **Decide.** Accept on a pre-registered rule (below), fold into the baseline, and repeat.

Step 2 is what makes this cheaper than what we did. Of the changes tried in #720, `frame_2003=33`,
`frame_1997=22` and dedup would all have been rejected before any grading.

## The decision rule has to be fixed in advance

With ~20 cards and most verdicts landing on "no difference", the counts are small. Accept when
`better - worse >= 3` and `worse <= 1`; reject when `worse >= 3`; otherwise grade 20 more, to a cap of
60, then reject. Registering it in advance matters because in #720 the rule moved with the data:
`finish_foil=8` was scored 100–5 and recommended, and only a controlled batch later showed its sign
was backwards.

## What the loop must carry over

Five failures from #720, each of which cost a wasted grading session. The loop is only worth building
if it makes them structurally impossible.

- **Confounding.** Every batch must report what varies across its pairs before the reviewer starts.
  Three sessions were spent on frame-vs-finish comparisons where both moved together, and the
  conclusion was wrong until a batch isolated one.
- **Coupled parameters.** Frame weights form a ladder (1993/1997/2003/2015); moving one perturbs two
  gaps. The proposer must move declared groups together and describe the change as a gap, not a value.
- **Destination-specific verdicts.** A verdict answers "is this printing better than that one", so it
  transfers only when a later config lands on the same printing. `load_judged` does this now.
- **Blinding.** Sides must be randomised and labelled left/right. The first three batches showed
  `CURRENT` and `PROPOSED` outright.
- **Selection on the evaluation set.** Fitting three things against one set of 89 labels made every
  p-value optimistic. Hold out a third of the labels, never fit against them, and report holdout
  agreement at accept time.

## Where the current tooling stops

`prefer_weights.py` has the pieces — validated level extraction, `step`, blinded review,
destination-aware exclusion — but they are subcommands a human drives one at a time. Missing:
the hit-rate objective as a first-class command, the labelled set as a tracked artifact rather than
files in `~/Downloads`, the accept/reject rule in code, and a run log so a component's history is
recoverable. The scoring definition also still lives in SQL with weights fused into it, so an accepted
change is hand-copied — the weights should move to config that both the SQL and the tooling read.

## Related

- [00720-prefer-score-artwork-tuning.md](00720-prefer-score-artwork-tuning.md) — the tuning this
  process was derived from, and the record of what it got wrong.
- [local-prefer-score-label-harness.md](local-prefer-score-label-harness.md) — the labelling
  instrument that produces the objective.
- [`scripts/prefer_weights.py`](../../scripts/prefer_weights.py) — the existing pieces.
