# Remaining `is:` Tag Coverage

[#985](https://github.com/jbylund/sylvan_librarian/issues/985).

Checklist of the `is:` tags Scryfall's syntax page documents that we do **not** resolve on `main`.

Source: `GET /discover_is_tags_from_syntax` (92 tags) minus the 17 `is:` keys in
[`_DERIVED_EXPANSIONS`](../../api/parsing/rewrite.py). That rewrite table is the only path that
resolves `is:` — anything outside it parses cleanly and falls through to a `card_is_tags` JSONB
lookup on an empty column, so it is a **silent zero-result query**, not an error.

Already supported, excluded below: `bear`, `colorshifted`, `dfc`, `flip`, `historic`, `leveler`,
`manland`, `mdfc`, `meld`, `new`, `old`, `outlaw`, `party`, `permanent`, `split`, `transform`,
`vanilla`.

Recovery mechanism per tag (rewrite / build-time bit / dropped bulk field / no source) is
classified in [00713-is-tag-recovery.md](done/00713-is-tag-recovery.md). Note that every entry
there flagged `~` is an unvalidated hypothesis: the naive expansion is frequently ~97–99%, not
exact, so each definition needs a live count-check against Scryfall before it ships.

The endpoint reflects the syntax page, not the full vocabulary — `is:token`, `is:textless`, and
`is:firstprinting` are real filters absent from this list.

## Unsupported (14)

- [ ] `is:alchemy`
- [ ] `is:atypical`
- [ ] `is:brawler`
- [ ] `is:default`
- [ ] `is:digital`
- [ ] `is:duelcommander`
- [ ] `is:funny`
- [ ] `is:meldpart`
- [ ] `is:meldresult`
- [ ] `is:newinpauper`
- [ ] `is:oathbreaker`
- [ ] `is:rebalanced`
- [ ] `is:spell`
- [ ] `is:unique`

## Supported (61)

Via `_DERIVED_EXPANSIONS` in `api/parsing/rewrite.py`:

- [x] `is:bikeland`
- [x] `is:bondland`
- [x] `is:bounceland`
- [x] `is:canopyland`
- [x] `is:checkland`
- [x] `is:commander`
- [x] `is:companion`
- [x] `is:creatureland`
- [x] `is:dual`
- [x] `is:fastland`
- [x] `is:fetchland`
- [x] `is:filterland`
- [x] `is:frenchvanilla`
- [x] `is:gainland`
- [x] `is:modal`
- [x] `is:painland`
- [x] `is:pathway`
- [x] `is:scryland`
- [x] `is:shadowland`
- [x] `is:shockland`
- [x] `is:slowland`
- [x] `is:storageland`
- [x] `is:surveilland`
- [x] `is:tangoland`
- [x] `is:tricycleland`
- [x] `is:triland`

Via `BOOLEAN_IS_TAGS` in `api/admin_resource.py` (synced from raw_card_blob on every import):

- [x] `is:arena_league`
- [x] `is:booster`
- [x] `is:buyabox`
- [x] `is:convention`
- [x] `is:datestamped`
- [x] `is:etched`
- [x] `is:fnm`
- [x] `is:foil`
- [x] `is:full`
- [x] `is:gamechanger`
- [x] `is:gameday`
- [x] `is:giftbox`
- [x] `is:glossy`
- [x] `is:hires`
- [x] `is:hybrid`
- [x] `is:instore`
- [x] `is:intro_pack`
- [x] `is:judge_gift`
- [x] `is:league`
- [x] `is:masterpiece`
- [x] `is:media_insert`
- [x] `is:nonfoil`
- [x] `is:partner`
- [x] `is:phyrexian`
- [x] `is:planeswalker_deck`
- [x] `is:player_rewards`
- [x] `is:prerelease`
- [x] `is:promo`
- [x] `is:release`
- [x] `is:reprint`
- [x] `is:reserved`
- [x] `is:scryfallpreview`
- [x] `is:set_promo`
- [x] `is:spotlight`
- [x] `is:universesbeyond`
