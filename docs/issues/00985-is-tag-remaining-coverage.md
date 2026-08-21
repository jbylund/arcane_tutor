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

## Unsupported (75)

- [ ] `is:alchemy`
- [ ] `is:arena_league`
- [ ] `is:atypical`
- [ ] `is:bikeland`
- [ ] `is:bondland`
- [ ] `is:booster`
- [ ] `is:bounceland`
- [ ] `is:brawler`
- [ ] `is:buyabox`
- [ ] `is:canopyland`
- [ ] `is:checkland`
- [ ] `is:commander`
- [ ] `is:companion`
- [ ] `is:convention`
- [ ] `is:creatureland`
- [ ] `is:datestamped`
- [ ] `is:default`
- [ ] `is:digital`
- [ ] `is:dual`
- [ ] `is:duelcommander`
- [ ] `is:etched`
- [ ] `is:fastland`
- [ ] `is:fetchland`
- [ ] `is:filterland`
- [ ] `is:fnm`
- [ ] `is:foil`
- [ ] `is:frenchvanilla`
- [ ] `is:full`
- [ ] `is:funny`
- [ ] `is:gainland`
- [ ] `is:gamechanger`
- [ ] `is:gameday`
- [ ] `is:giftbox`
- [ ] `is:glossy`
- [ ] `is:hires`
- [ ] `is:hybrid`
- [ ] `is:instore`
- [ ] `is:intro_pack`
- [ ] `is:judge_gift`
- [ ] `is:league`
- [ ] `is:masterpiece`
- [ ] `is:media_insert`
- [ ] `is:meldpart`
- [ ] `is:meldresult`
- [ ] `is:modal`
- [ ] `is:newinpauper`
- [ ] `is:nonfoil`
- [ ] `is:oathbreaker`
- [ ] `is:painland`
- [ ] `is:partner`
- [ ] `is:pathway`
- [ ] `is:phyrexian`
- [ ] `is:planeswalker_deck`
- [ ] `is:player_rewards`
- [ ] `is:prerelease`
- [ ] `is:promo`
- [ ] `is:rebalanced`
- [ ] `is:release`
- [ ] `is:reprint`
- [ ] `is:reserved`
- [ ] `is:scryfallpreview`
- [ ] `is:scryland`
- [ ] `is:set_promo`
- [ ] `is:shadowland`
- [ ] `is:shockland`
- [ ] `is:slowland`
- [ ] `is:spell`
- [ ] `is:spotlight`
- [ ] `is:storageland`
- [ ] `is:surveilland`
- [ ] `is:tangoland`
- [ ] `is:tricycleland`
- [ ] `is:triland`
- [ ] `is:unique`
- [ ] `is:universesbeyond`
