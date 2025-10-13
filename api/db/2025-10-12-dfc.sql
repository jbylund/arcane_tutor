
-- delete some dirty data
DELETE FROM magic.cards WHERE scryfall_id IN (
    '9cf54062-7b5b-4e46-ae1e-fab7e419a9fa',  -- this is https://scryfall.com/card/tdm/379/scavenger-regent-exude-toxin-scavenger-regent - has 2 copies of one face
    '484b5580-b179-4dce-8bdf-d714eb4635e5', -- coil and catch... same-ish issue 
    '081f2de5-251a-41c9-a62f-11487f54d355' -- claim territory
);

-- less precise than the above but same idea
DELETE FROM magic.cards WHERE card_name LIKE '%//%//%';


CREATE SCHEMA s_dfc;

CREATE TABLE s_dfc.card_faces AS (
    SELECT
        face_name,
        oracle_id,
        face_idx,

        type_line AS face_type_line,
        card_subtypes AS face_subtypes,
        card_types AS face_types,

        card_colors AS face_colors,
        cmc AS face_cmc,
        creature_power_text AS face_creature_power_text,
        creature_power AS face_creature_power,
        creature_toughness_text AS face_creature_toughness_text,
        creature_toughness AS face_creature_toughness,
        devotion AS face_devotion,
        mana_cost_jsonb AS face_mana_cost_jsonb,
        mana_cost_text AS face_mana_cost_text,
        oracle_text AS face_oracle_text,
        planeswalker_loyalty AS face_planeswalker_loyalty,
        planeswalker_loyalty_text AS face_planeswalker_loyalty_text,
        produced_mana AS face_produced_mana
    FROM
        magic.cards
    GROUP BY (
        1, 2, 3, 4, 5,
        6, 7, 8, 9, 10,
        11, 12, 13, 14, 15,
        16, 17, 18, 19
    )
);

/*
select sum(1) from s_dfc.cards c where (c.back_face).face_name is not null;

this is incorrect as it groups by card name and oracle id
and at this point card name isn't a "card name" - it's the face name
so we need to update ingestion to keep both card name and face name
*/

CREATE TABLE s_dfc.cards AS (
    SELECT
        cards.card_name,
        cards.oracle_id,

        cards.card_color_identity,
        cards.card_keywords,
        cards.edhrec_rank,
        front_face,
        back_face
    FROM 
        magic.cards AS cards
    JOIN
        s_dfc.card_faces front_face
    ON
        cards.oracle_id = front_face.oracle_id AND 
        front_face.face_idx = 1
    LEFT JOIN
        s_dfc.card_faces back_face
    ON
        cards.oracle_id = back_face.oracle_id AND 
        back_face.face_idx = 2
    GROUP BY (
        1, 2, 3, 4, 
        5, 6, 7
    )
);

CREATE UNIQUE INDEX ON s_dfc.cards (card_name); -- this applies now

/* todo update this to not unpack all the card data - just leave it in a card type */
CREATE TABLE s_dfc.face_prints AS (
    SELECT
        scryfall_id,
        face_idx,

        card_artist AS print_artist,
        card_border AS print_border,
        card_frame_data AS print_frame_data,
        card_is_tags AS print_is_tags,
        card_layout AS print_layout,
        card_oracle_tags AS print_oracle_tags,
        card_watermark AS print_watermark,
        flavor_text AS print_flavor_text,
        illustration_id AS print_illustration_id,
        image_location_uuid AS print_image_location_uuid,
        prefer_score AS print_prefer_score,
        prefer_score_components AS print_prefer_score_components,
        raw_card_blob AS print_raw_card_blob -- TODO: we probably don't want this
    FROM
        magic.cards
    GROUP BY (
        1, 2, 3, 4, 5,
        6, 7, 8, 9, 10,
        11, 12, 13, 14, 15
    )
);

CREATE UNIQUE INDEX ON s_dfc.face_prints (scryfall_id, face_idx);

CREATE TABLE s_dfc.prints AS (
    WITH 
    unique_scryfall_prints AS (
        SELECT
            scryfall_id
        FROM
            magic.cards
        GROUP BY
            scryfall_id
    )
    SELECT
        unique_scryfall_prints.scryfall_id,
        card_info.card_name,
        card_info.oracle_id,
        card_info.card_set_code,
        card_info.released_at,
        card_info.set_name,
        card_info.price_eur,
        card_info.price_tix,
        card_info.price_usd,
        card_info.card_legalities,
        card_info.card_rarity_int,
        card_info.card_rarity_text,
        card_info.collector_number,
        card_info.collector_number_int,
        front_face,
        back_face
    FROM
        unique_scryfall_prints
    JOIN
        magic.cards AS card_info
    ON
        unique_scryfall_prints.scryfall_id = card_info.scryfall_id AND
        card_info.face_idx = 1
    JOIN
        s_dfc.face_prints AS front_face
    ON
        unique_scryfall_prints.scryfall_id = front_face.scryfall_id AND
        front_face.face_idx = 1
    LEFT JOIN
        s_dfc.face_prints AS back_face
    ON
        unique_scryfall_prints.scryfall_id = back_face.scryfall_id AND
        back_face.face_idx = 2
);

CREATE TABLE s_dfc.cards_with_prints AS (
    SELECT
        card_info,
        print_info
    FROM
        s_dfc.cards AS card_info
    JOIN 
        s_dfc.prints AS print_info
    ON 
        card_info.oracle_id = print_info.oracle_id
);
