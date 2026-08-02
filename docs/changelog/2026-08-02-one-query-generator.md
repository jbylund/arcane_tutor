# One query generator

Three generators produced synthetic queries with three different field lists, and the gaps between
them were invisible from inside any one of them. `client/query_runner.py` carried 333 lines of
hand-written fragments and had no `cmc` predicate at all; `scripts/query_sampler.py` had the
corpus-drawn selectivity ladder the cost model needs but no `tix`, `eur`, `produces:` or
`devotion:`; `scripts/survey_queries.py` reimplemented weighted fragment selection on top of the
runner's table so it could add the shapes the runner lacked.

Everything now draws from `client/query_sampler.py`. It lives in `client/` because the load-runner
image copies only that directory, and it imports nothing but stdlib.

## What changed in the universe

- **Every field is reachable from every caller.** The sampler gained `tix`, `eur`, `produces:` and
  `devotion:` (which the cost-model benches never sampled); callers of the old runner gained `cmc`,
  `loyalty`, arithmetic, bounded ranges, and quantile-drawn thresholds.
- **One family per field.** Families are the dedupe unit — a query never draws twice from the same
  one — so merging fields into a family made their conjunctions unreachable. Colour and colour
  identity were one family, making `c:u id:wu` impossible; `pow`/`tou`/`cmc` were one family,
  making `pow>2 tou<4` impossible. Split into 26 families, merged only where fields genuinely share
  an operator (types and subtypes both use `t:`).
- **Quantile sampling extended to every numeric field.** `pow`, `tou`, `cmc` and `loyalty` were
  hand-picked constants; they are now drawn from the corpus distribution like `usd` always was.
- **Boundedness is a property of a range draw, not its own family.** As a separate family it could
  co-occur with a one-sided draw on the same field and emit `usd<5 usd>=1 usd<=20`.
- **A `keyword:` family**, which no generator had. Corpus-derived (639 usable values, `Flying` at
  9,060 printings down to one-offs); the no-corpus fallback is the evergreens plus a few
  distinctive mechanics (`infect`, `exalted`, `cascade`, `delve`) so the selective end is covered.
  Deliberately overlapping the oracle family — `keyword:flying` is a JSONB key lookup and
  `o:flying` a trigram substring match, two index paths for one intent.
- **No corpus required.** `QuerySampler()` with no corpus serves `FALLBACK_DECILES` (measured
  deciles per numeric column, interpolated) and `FALLBACK_VOCAB`. Uniform-in-quantile sampling
  survives at decile resolution, which is what lets the container-shipped runner use the same
  module. A corpus missing a column keeps that column's fallback rather than dropping the family.

## Bugs this surfaced

- `c>=2`, `c>=3`, `c=1`, `c=0` were in the old sampler's colour vocabulary and **the parser accepts
  none of them** — there is no colour-count syntax. Roughly one colour draw in eight has been
  raising in every cost-model benchmark since the sampler was written; those samples were dropped
  by the callers' blanket excepts. Removed.
- Corpus values containing apostrophes, parentheses or spaces (`O'Connor`, `C'tan`, `First Strike`)
  fail the lexer bare. Values are now quoted when they are not plain alphanumeric, which is
  semantically inert (`t:goblin` and `t:"goblin"` parse to the same node).
- The `is:` vocabulary was mostly dead. `is:` resolves through `api/parsing/rewrite.py`'s
  `_DERIVED_EXPANSIONS` for the 17 values it expands; anything else falls through to a
  `card_is_tags` JSONB lookup, and that column is empty. Of the old load generator's six values,
  **four matched zero cards** (`is:spell`, `is:modal`, `is:token`, `is:commander`, plus `is:reprint`
  which the sampler had added). The family is now exactly the rewrite table's `is:` keys, with a
  test pinning the two together. Verified against the live API: all 17 return results, none zero.
- **`keyword:` is broken for 131 of 770 keywords, including two evergreens** — found while adding
  the family. Keywords are stored verbatim from Scryfall but looked up `.title()`-cased, so
  `keyword:"first strike"` asks for `First Strike` while the stored key is `First strike` and
  matches nothing. 5.6% of all keyword occurrences; `.title()` also mangles `Doctor's` → `Doctor'S`.
  Not fixed here — either fix needs a reimport, so it wants its own change. Filed as #825 with
  `docs/issues/00825-keyword-title-case-mismatch.md`; the sampler skips the affected values via
  `is_queryable_keyword`, which should be deleted along with the mismatch.
- `ORDERBY_VALUES` was duplicated across four scripts in **three different versions** — 13 values in
  two benches, 8 in the survey, 7 in `bench_guard_validation`. The authority is
  `api.enums.CardOrdering` (8). The 13-value copies passed `order=released`/`set`/`color` through to
  the engine, which silently sorts those by edhrec — so those rows were labelled with an ordering
  they did not have. Now one `ENGINE_ORDERBYS`, with a test asserting it matches the enum.

## Consequence for existing baselines

Seeded query streams change. Cost-model benches are paired A/B (same queries both sides), so the
methodology is unaffected, but absolute numbers from before this change are not comparable to after.
The survey's seeded corpus changes for the same reason.

## Net

22 files, +1,013 / -850. Excluding tests and docs it is **-116 lines net while gaining shapes, four
new fields, quantile sampling on four more columns, and the no-corpus path** —
`client/query_runner.py` alone went from 574 lines to 233, and `scripts/survey_queries.py` shed 131.

New `client/tests/test_query_sampler.py` (85 tests) asserts every family and every shape parses in
both modes, corpus-backed and not, and pins `ENGINE_ORDERBYS` to `api.enums.CardOrdering`. Full
suite 2,340 passing; `ruff check` and `ruff format` clean. Verified end to end against the live blue
API: 25/25 queries, 100% success, including ORs, nested parens, negations, arithmetic and
non-default offset/prefer.
