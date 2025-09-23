-- Migration to add flavor_text column for flavor text search support
-- Date: 2025-01-23

-- Add flavor_text column to cards table  
ALTER TABLE magic.cards ADD COLUMN flavor_text text;

-- Populate flavor_text from raw_card_blob data
UPDATE magic.cards 
SET flavor_text = raw_card_blob->>'flavor_text'
WHERE raw_card_blob ? 'flavor_text' 
AND raw_card_blob->>'flavor_text' IS NOT NULL
AND TRIM(raw_card_blob->>'flavor_text') != '';

-- Add index for flavor text searches (similar to oracle_text)
CREATE INDEX idx_cards_flavor_text ON magic.cards USING gin(to_tsvector('english', flavor_text)) 
WHERE flavor_text IS NOT NULL;

-- Add comments for documentation
COMMENT ON COLUMN magic.cards.flavor_text IS 'Card flavor text for flavor text search (flavor:)';