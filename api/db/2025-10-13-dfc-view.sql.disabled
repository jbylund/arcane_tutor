-- Create a flattened view for easier querying of s_dfc.cards_with_prints
-- This extracts commonly-used fields from the nested composite types

CREATE OR REPLACE VIEW s_dfc.cards_flat AS
SELECT
    -- Card-level attributes (from card_info)
    (card_info).card_name AS card_name,
    (card_info).oracle_id AS oracle_id,
    (card_info).card_color_identity AS card_color_identity,
    (card_info).card_keywords AS card_keywords,
    (card_info).edhrec_rank AS edhrec_rank,
    
    -- Print-level attributes (from print_info)
    (print_info).scryfall_id AS scryfall_id,
    (print_info).card_set_code AS card_set_code,
    (print_info).released_at AS released_at,
    (print_info).set_name AS set_name,
    (print_info).price_usd AS price_usd,
    (print_info).price_eur AS price_eur,
    (print_info).price_tix AS price_tix,
    (print_info).card_legalities AS card_legalities,
    (print_info).card_rarity_int AS card_rarity_int,
    (print_info).card_rarity_text AS card_rarity_text,
    
    -- Front face attributes (most commonly displayed)
    ((print_info).front_face).print_artist AS card_artist,
    ((print_info).front_face).print_border AS card_border,
    ((print_info).front_face).print_frame_data AS card_frame_data,
    ((print_info).front_face).print_layout AS card_layout,
    ((print_info).front_face).print_watermark AS card_watermark,
    ((print_info).front_face).print_collector_number AS collector_number,
    ((print_info).front_face).print_collector_number_int AS collector_number_int,
    ((print_info).front_face).print_flavor_text AS flavor_text,
    ((print_info).front_face).print_illustration_id AS illustration_id,
    ((print_info).front_face).print_image_location_uuid AS image_location_uuid,
    ((print_info).front_face).print_prefer_score AS prefer_score,
    
    ((card_info).front_face).face_name AS face_name,
    ((card_info).front_face).face_type_line AS type_line,
    ((card_info).front_face).face_colors AS card_colors,
    ((card_info).front_face).face_subtypes AS card_subtypes,
    ((card_info).front_face).face_types AS card_types,
    ((card_info).front_face).face_cmc AS cmc,
    ((card_info).front_face).face_creature_power AS creature_power,
    ((card_info).front_face).face_creature_power_text AS creature_power_text,
    ((card_info).front_face).face_creature_toughness AS creature_toughness,
    ((card_info).front_face).face_creature_toughness_text AS creature_toughness_text,
    ((card_info).front_face).face_planeswalker_loyalty AS planeswalker_loyalty,
    ((card_info).front_face).face_mana_cost_text AS mana_cost_text,
    ((card_info).front_face).face_mana_cost_jsonb AS mana_cost_jsonb,
    ((card_info).front_face).face_devotion AS devotion,
    ((card_info).front_face).face_produced_mana AS produced_mana,
    ((card_info).front_face).face_oracle_text AS oracle_text,
    
    -- Store composites for when we need back face or raw data
    card_info,
    print_info
FROM
    s_dfc.cards_with_prints;

-- Create indexes on the flattened view for common queries
CREATE INDEX IF NOT EXISTS idx_cards_flat_card_name ON s_dfc.cards_flat (card_name);
CREATE INDEX IF NOT EXISTS idx_cards_flat_oracle_id ON s_dfc.cards_flat (oracle_id);
CREATE INDEX IF NOT EXISTS idx_cards_flat_scryfall_id ON s_dfc.cards_flat (scryfall_id);
CREATE INDEX IF NOT EXISTS idx_cards_flat_set_code ON s_dfc.cards_flat (card_set_code);

COMMENT ON VIEW s_dfc.cards_flat IS 'Flattened view of s_dfc.cards_with_prints for easier querying. Shows front face by default, composites available for back face access.';
