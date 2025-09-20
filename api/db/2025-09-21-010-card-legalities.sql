-- Add legality data column to the cards table
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_legalities jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Add comment for the new column
COMMENT ON COLUMN magic.cards.card_legalities IS 'Card legality status in different formats stored as JSONB object, e.g. {"standard": "legal", "modern": "banned", "legacy": "restricted"}';

-- Add constraint to ensure proper JSONB object structure
ALTER TABLE magic.cards ADD CONSTRAINT card_legalities_must_be_object CHECK (jsonb_typeof(card_legalities) = 'object');

-- Populate legalities column from existing raw card blob data
UPDATE magic.cards SET
    card_legalities = COALESCE(raw_card_blob->'legalities', '{}'::jsonb)
WHERE card_legalities = '{}'::jsonb AND raw_card_blob->'legalities' IS NOT NULL;

-- Index for legality searches - GIN index for JSONB operations
CREATE INDEX IF NOT EXISTS idx_cards_legalities ON magic.cards USING GIN (card_legalities);