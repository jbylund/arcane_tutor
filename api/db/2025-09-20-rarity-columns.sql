-- Add card rarity columns to support rarity search functionality
-- This includes both text and numeric representations for flexible querying

ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_rarity_text text;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_rarity_numeric integer;

-- Add comments for the new columns
COMMENT ON COLUMN magic.cards.card_rarity_text IS 'Card rarity as text (common, uncommon, rare, mythic) - extracted from raw card blob';
COMMENT ON COLUMN magic.cards.card_rarity_numeric IS 'Card rarity as numeric value for ordering: 1=common, 2=uncommon, 3=rare, 4=mythic';

-- Create a function to convert rarity text to numeric value
CREATE OR REPLACE FUNCTION rarity_to_numeric(rarity_text text)
RETURNS integer AS $$
BEGIN
    RETURN CASE LOWER(TRIM(rarity_text))
        WHEN 'common' THEN 1
        WHEN 'uncommon' THEN 2
        WHEN 'rare' THEN 3
        WHEN 'mythic' THEN 4
        WHEN 'mythic rare' THEN 4  -- Handle both variants
        WHEN 'special' THEN 5      -- Handle special rarities
        WHEN 'bonus' THEN 6        -- Handle bonus rarities
        ELSE NULL  -- Unknown rarities will be NULL
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Populate rarity columns from existing raw card blob data
-- Extract rarity from the JSON blob and convert to both text and numeric
UPDATE magic.cards SET
    card_rarity_text = raw_card_blob->>'rarity',
    card_rarity_numeric = rarity_to_numeric(raw_card_blob->>'rarity')
WHERE raw_card_blob->>'rarity' IS NOT NULL;

-- Create indexes for rarity searches
CREATE INDEX IF NOT EXISTS idx_cards_rarity_text ON magic.cards (card_rarity_text) WHERE card_rarity_text IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cards_rarity_numeric ON magic.cards (card_rarity_numeric) WHERE card_rarity_numeric IS NOT NULL;

-- Add constraint to ensure numeric rarity corresponds to valid text values
ALTER TABLE magic.cards ADD CONSTRAINT check_rarity_consistency 
CHECK (
    (card_rarity_text IS NULL AND card_rarity_numeric IS NULL) OR
    (card_rarity_text IS NOT NULL AND card_rarity_numeric = rarity_to_numeric(card_rarity_text))
);