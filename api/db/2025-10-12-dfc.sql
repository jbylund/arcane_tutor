CREATE SCHEMA s_dfc;

CREATE TABLE s_dfc.card_faces AS (
    SELECT
        card_name AS face_name,
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

CREATE UNIQUE INDEX ON s_dfc.cards (card_name);

CREATE TABLE s_dfc.prints AS (
    SELECT
        card_name,
        scryfall_id,
        oracle_id,

        card_artist AS print_artist,
        card_border AS print_border,
        card_frame_data AS print_frame_data,
        card_is_tags AS print_is_tags,
        card_layout AS print_layout,
        card_legalities AS print_legalities,
        card_oracle_tags AS print_oracle_tags,
        card_rarity_int AS print_rarity_int,
        card_rarity_text AS print_rarity_text,
        card_set_code AS print_set_code,
        card_watermark AS print_watermark,
        collector_number AS print_collector_number,
        collector_number_int AS print_collector_number_int,
        flavor_text AS print_flavor_text,
        illustration_id AS print_illustration_id,
        image_location_uuid AS print_image_location_uuid,
        prefer_score AS print_prefer_score,
        prefer_score_components AS print_prefer_score_components,
        price_eur AS print_price_eur,
        price_tix AS print_price_tix,
        price_usd AS print_price_usd,
        raw_card_blob AS print_raw_card_blob,
        released_at AS print_released_at,
        set_name AS print_set_name
    FROM
        magic.cards
    GROUP BY (
        1, 2, 3, 4, 5,
        6, 7, 8, 9, 10,
        11, 12, 13, 14, 15,
        16, 17, 18, 19, 20,
        21, 22, 23, 24, 25,
        26, 27
    )
);

CREATE UNIQUE INDEX ON s_dfc.prints (scryfall_id);
CREATE INDEX IF NOT EXISTS prints_oracleid_idx_hash ON s_dfc.prints USING HASH (oracle_id);

CREATE VIEW s_dfc.cards_with_prints AS (
    SELECT
        cards.*,
        prints.print_artist,
        prints.print_border,
        prints.print_frame_data,
        prints.print_is_tags,
        prints.print_layout,
        prints.print_legalities,
        prints.print_oracle_tags,
        prints.print_rarity_int,
        prints.print_rarity_text,
        prints.print_set_code,
        prints.print_watermark,
        prints.print_collector_number,
        prints.print_collector_number_int,
        prints.print_flavor_text,
        prints.print_illustration_id,
        prints.print_image_location_uuid,
        prints.print_prefer_score,
        prints.print_prefer_score_components,
        prints.print_price_eur,
        prints.print_price_tix,
        prints.print_price_usd,
        prints.print_raw_card_blob,
        prints.print_released_at,
        prints.print_set_name
    FROM
        s_dfc.cards AS cards
    JOIN 
        s_dfc.prints AS prints 
    ON 
        cards.oracle_id = prints.oracle_id
);
