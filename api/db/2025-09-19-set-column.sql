-- Add set code column to the cards table
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_set_code text;

-- Add comment for the new column
COMMENT ON COLUMN magic.cards.card_set_code IS 'Set code (e.g. "iko", "thb") - will be null for cards without set information';

-- Populate set code column from existing raw card blob data
UPDATE magic.cards SET
    card_set_code = raw_card_blob->>'set'
WHERE raw_card_blob->>'set' IS NOT NULL;

-- Hash index for set code performance
CREATE INDEX IF NOT EXISTS idx_cards_set_code ON magic.cards USING HASH (card_set_code) WHERE card_set_code IS NOT NULL;