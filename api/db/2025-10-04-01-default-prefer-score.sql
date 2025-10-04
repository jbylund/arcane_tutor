-- Migration: Add default prefer score calculation
-- This migration adds a prefer_score column to the magic.cards table and populates it
-- based on various card attributes to implement the default prefer ordering

-- Add prefer_score column
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS prefer_score_components jsonb;

UPDATE magic.cards update_target_cards
SET prefer_score_components = JSONB_BUILD_OBJECT(
    'illustration_count', (
        SELECT 
            150 * LN(1 + COUNT(*)) / LN(40)
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
                WHEN card_rarity_int = 0 THEN 100
                WHEN card_rarity_int = 1 THEN 27
                WHEN card_rarity_int = 2 THEN 8
                WHEN card_rarity_int = 3 THEN 1
                ELSE 0
            END
    ),
    'border', (
        SELECT 
            CASE 
                WHEN card_border = 'black' THEN 100
                WHEN card_border = 'borderless' THEN 40
                WHEN card_border = 'white' THEN 20
                ELSE 0
            END
    ),
    'frame', (
        SELECT 
            CASE 
                WHEN card_frame_data ? '2015' THEN 100
                WHEN card_frame_data ? '2003' THEN 26
                ELSE 0
            END
    ),
    'extended_art', (
        SELECT 
            CASE 
                WHEN card_frame_data ? 'Extendedart' THEN 100
                ELSE 0
            END
    ),
    'set_type', (
        SELECT 
            CASE 
                WHEN raw_card_blob ->> 'set_type' = 'memorabilia' THEN 0
                WHEN raw_card_blob ->> 'set_type' = 'core' THEN 100
                ELSE 75
            END
    )
);



ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS prefer_score real;

-- Update prefer_score to be the sum of all component values
UPDATE magic.cards 
SET prefer_score = (
    SELECT SUM(value::numeric)
    FROM jsonb_each(prefer_score_components)
);

