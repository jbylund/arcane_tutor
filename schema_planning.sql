DROP SCHEMA IF EXISTS magic CASCADE;
CREATE SCHEMA magic;

CREATE TABLE magic.sets (
    set_name text,
    set_code text,
    release_date date,
    num_cards integer
);

CREATE UNIQUE INDEX set_code_idx ON magic.sets (set_code);

CREATE TABLE magic.cards (
    /*
    cards don't actually have names... they just have ids?
    each face of the card has a name...
    that said scryfall search currently searches both sides
    for example a search for "beloved generous" turns up
    "beloved beggar" // "generous soul" which doesn't match for
    either side individually

    t:sorcery t:creature also finds adventure cards
    where the card is a creature but the adventure is a sorcery
    */
    card_name text,
    card_supertypes jsonb, /* legendary, snow */
    card_types jsonb, /* creature, enchantment */
    card_subtypes jsonb, /* human, knight */
    legalaties jsonb /* standard, modern, legacy, vintage, commander */
);

CREATE UNIQUE INDEX card_name_idx ON magic.cards (card_name);

/*
some sets have non-numeric collector's numbers
like: https://scryfall.com/card/plst/2X2-332/cryptic-spires
... which is lame
*/

CREATE TABLE magic.card_printings (
    card_name text references magic.cards(card_name),
    set_code text references magic.sets(set_code),
    collector_number integer
);

CREATE TABLE magic.card_faces (
    card_name text references magic.cards(card_name),
    face_name text,
    mana_cost jsonb,
    card_supertypes jsonb, /* legendary, snow */
    card_types jsonb, /* creature, enchantment */
    card_subtypes jsonb, /* human, knight */
    oracle_text text /* this should be constant over all printings */
);

/*
likewise power/toughness are integers the vast majority of the time
but there are a few edges cases where
*/

CREATE TABLE magic.scryfall_raw_cards (
    raw_card jsonb
);

DROP VIEW magic.paper_legal_cards;

CREATE OR REPLACE VIEW magic.paper_legal_cards AS
SELECT
    raw_card->>'artist' as artist,
    raw_card->>'booster' as booster,
    raw_card->>'border_color' as border_color,
    raw_card->>'cmc' as cmc,
    raw_card->>'collector_number' as collector_number,
    raw_card->'color_identity' as color_identity,
    (raw_card->>'digital')::boolean as digital,
    raw_card->'finishes' as finishes,
    (raw_card->>'foil')::boolean as foil,
    raw_card->>'frame' as frame,
    (raw_card->>'full_art')::boolean as full_art,
    raw_card->'games' as games,
    raw_card->>'highres_image' as highres_image,
    raw_card->>'id' as id,
    raw_card->>'image_status' as image_status,
    raw_card->'keywords' as keywords,
    raw_card->>'lang' as lang,
    raw_card->>'layout' as layout,
    raw_card->'legalities' as legalities,
    raw_card->'multiverse_ids' as multiverse_ids,
    raw_card->>'name' as name,
    raw_card->>'nonfoil' as nonfoil,
    raw_card->>'object' as object,
    raw_card->>'oracle_id' as oracle_id,
    raw_card->>'oversized' as oversized,
    raw_card->>'prices' as prices,
    raw_card->>'prints_search_uri' as prints_search_uri,
    raw_card->>'promo' as promo,
    raw_card->>'rarity' as rarity,
    raw_card->>'related_uris' as related_uris,
    raw_card->>'released_at' as released_at,
    raw_card->>'reprint' as reprint,
    raw_card->>'reserved' as reserved,
    raw_card->>'rulings_uri' as rulings_uri,
    raw_card->>'scryfall_set_uri' as scryfall_set_uri,
    raw_card->>'scryfall_uri' as scryfall_uri,
    raw_card->>'set' as set,
    raw_card->>'set_id' as set_id,
    raw_card->>'set_name' as set_name,
    raw_card->>'set_search_uri' as set_search_uri,
    raw_card->>'set_type' as set_type,
    raw_card->>'set_uri' as set_uri,
    raw_card->>'story_spotlight' as story_spotlight,
    raw_card->>'textless' as textless,
    raw_card->>'type_line' as type_line,
    raw_card->>'uri' as uri,
    raw_card->>'variation' as variation,
    raw_card
FROM
    magic.scryfall_raw_cards
WHERE
    raw_card->'games' ? 'paper';

CREATE MATERIALIZED VIEW magic.sets AS
SELECT
    raw_card->>'set' AS set_code,
    raw_card->>'set_name' AS set_name,
    min((raw_card->>'released_at')::date) AS release_date,
    sum(1) AS num_cards
FROM
    magic.paper_legal_cards
GROUP BY
    set_name,
    set_code;
