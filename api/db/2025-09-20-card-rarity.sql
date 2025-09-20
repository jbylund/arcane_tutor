-- Migration to add card rarity columns with lookup table approach
-- Date: 2025-09-20

-- Create valid_rarities lookup table
CREATE TABLE magic.valid_rarities (
    card_rarity_int integer PRIMARY KEY,
    card_rarity_text text NOT NULL UNIQUE,
    -- Add unique constraint on both columns for foreign key reference
    UNIQUE (card_rarity_int, card_rarity_text)
);

-- Insert valid rarity values
INSERT INTO magic.valid_rarities (card_rarity_int, card_rarity_text) VALUES
    (0, 'common'),
    (1, 'uncommon'),
    (2, 'rare'),
    (3, 'mythic'),
    (4, 'special'),
    (5, 'bonus');

-- Create function to convert rarity text to integer (for application use)
CREATE OR REPLACE FUNCTION magic.rarity_text_to_int(rarity_text text)
RETURNS integer AS $$
BEGIN
    RETURN CASE LOWER(TRIM(rarity_text))
        WHEN 'common' THEN 0
        WHEN 'uncommon' THEN 1
        WHEN 'rare' THEN 2
        WHEN 'mythic' THEN 3
        WHEN 'special' THEN 4
        WHEN 'bonus' THEN 5
        ELSE -1
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Create function to convert rarity integer to text (for application use)
CREATE OR REPLACE FUNCTION magic.rarity_int_to_text(rarity_int integer)
RETURNS text AS $$
BEGIN
    RETURN CASE rarity_int
        WHEN 0 THEN 'common'
        WHEN 1 THEN 'uncommon'
        WHEN 2 THEN 'rare'
        WHEN 3 THEN 'mythic'
        WHEN 4 THEN 'special'
        WHEN 5 THEN 'bonus'
        ELSE NULL
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Add card rarity columns to cards table
ALTER TABLE magic.cards ADD COLUMN card_rarity_text text;
ALTER TABLE magic.cards ADD COLUMN card_rarity_int integer;

-- Populate card_rarity_text from raw_card_blob data
UPDATE magic.cards 
SET card_rarity_text = LOWER(raw_card_blob->>'rarity')
WHERE raw_card_blob ? 'rarity' 
AND raw_card_blob->>'rarity' IS NOT NULL;

-- Populate card_rarity_int using the conversion function
UPDATE magic.cards 
SET card_rarity_int = magic.rarity_text_to_int(card_rarity_text)
WHERE card_rarity_text IS NOT NULL;

-- Add foreign key constraint to ensure data integrity
ALTER TABLE magic.cards 
ADD CONSTRAINT fk_cards_rarity 
FOREIGN KEY (card_rarity_int, card_rarity_text) 
REFERENCES magic.valid_rarities (card_rarity_int, card_rarity_text);

-- Add comments for documentation
COMMENT ON TABLE magic.valid_rarities IS 'Lookup table for valid card rarities with integer and text representations';
COMMENT ON COLUMN magic.cards.card_rarity_text IS 'Card rarity as text: common, uncommon, rare, mythic, special, bonus';
COMMENT ON COLUMN magic.cards.card_rarity_int IS 'Card rarity as integer for efficient ordering and comparison: common=0, uncommon=1, rare=2, mythic=3, special=4, bonus=5';
COMMENT ON FUNCTION magic.rarity_text_to_int(text) IS 'Convert rarity text to integer for ordering and comparison';
COMMENT ON FUNCTION magic.rarity_int_to_text(integer) IS 'Convert rarity integer back to text';