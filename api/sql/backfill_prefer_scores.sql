-- Backfill prefer_score and prefer_score_components for all cards
-- This script recalculates the prefer score for all existing cards based on multiple attributes

UPDATE magic.cards update_target_cards
SET prefer_score_components = JSONB_BUILD_OBJECT(
    'illustration_count', (
        SELECT 
            23 * LN(1 + COUNT(*)) / LN(40)
        FROM magic.cards query_target_cards
        WHERE (
            query_target_cards.illustration_id = update_target_cards.illustration_id AND 
            query_target_cards.illustration_id IS NOT NULL AND
            query_target_cards.card_name = update_target_cards.card_name
        )
    ),
    'rarity', (
        SELECT 
            CASE 
                WHEN card_rarity_int = 0 THEN 16  -- common
                WHEN card_rarity_int = 1 THEN 16  -- uncommon
                WHEN card_rarity_int = 2 THEN 11  -- rare
                WHEN card_rarity_int = 3 THEN 0   -- mythic
                ELSE 0
            END
    ),
    'border', (
        SELECT 
            CASE 
                WHEN card_border = 'black' THEN 14
                WHEN card_border = 'white' THEN 0
                WHEN card_border = 'borderless' THEN 0
                ELSE 0
            END
    ),
    'frame', (
        SELECT 
            CASE 
                WHEN card_frame_data ? '2015' THEN 42
                WHEN card_frame_data ? '2003' THEN 30
                WHEN card_frame_data ? '1997' THEN 25
                WHEN card_frame_data ? '1993' THEN 10
                ELSE 0
            END
    ),
    'extended_art', (
        SELECT 
            CASE 
                WHEN card_frame_data ? 'Extendedart' THEN 12
                ELSE 0
            END
    ),
    'highres_scan', (
        SELECT 
            CASE 
                WHEN raw_card_blob ->> 'image_status' = 'highres_scan' THEN 16
                ELSE 0
            END
    ),
    'has_paper', (
        SELECT 
            CASE 
                WHEN raw_card_blob -> 'games' ? 'paper' THEN 6
                ELSE 0
            END
    ),
    'language', (
        SELECT 
            CASE 
                WHEN raw_card_blob ->> 'lang' = 'en' THEN 40
                ELSE 0
            END
    ),
    'legendary_frame', (
        SELECT 
            CASE 
                WHEN raw_card_blob -> 'frame_effects' ? 'legendary' THEN 5
                ELSE 0
            END
    ),
    'finish', (
        SELECT 
            CASE 
                WHEN raw_card_blob -> 'finishes' ? 'nonfoil' THEN 10
                WHEN raw_card_blob -> 'finishes' ? 'foil' THEN 5
                WHEN raw_card_blob -> 'finishes' ? 'etched' THEN 0
                ELSE 0
            END
    )
);


-- Update prefer_score to be the sum of all component values
UPDATE magic.cards 
SET prefer_score = (
    SELECT SUM(value::numeric)
    FROM jsonb_each(prefer_score_components)
);
