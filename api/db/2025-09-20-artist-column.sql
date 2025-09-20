-- Add artist column to the cards table
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_artist text;

-- Add comment for the new column
COMMENT ON COLUMN magic.cards.card_artist IS 'Artist name for the card artwork - will be null for cards without artist information';

-- Populate artist column from existing raw card blob data
UPDATE magic.cards SET
    card_artist = raw_card_blob->>'artist'
WHERE raw_card_blob->>'artist' IS NOT NULL;

-- Install trigram extension if not already present
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Trigram index for artist text search performance (GIN index for ILIKE queries)
CREATE INDEX IF NOT EXISTS idx_cards_artist_trgm ON magic.cards USING GIN (card_artist gin_trgm_ops) WHERE card_artist IS NOT NULL;