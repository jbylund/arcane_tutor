-- Add card_is_tags column to support is: search functionality
-- This column will store JSONB object with is: tags like {"creature": true, "spell": true}

ALTER TABLE magic.cards
ADD COLUMN card_is_tags jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Add constraint to ensure it's always a JSONB object
ALTER TABLE magic.cards
ADD CONSTRAINT card_is_tags_must_be_object CHECK (jsonb_typeof(card_is_tags) = 'object');

-- Add GIN index for efficient searching on card_is_tags
CREATE INDEX idx_cards_is_tags_gin ON magic.cards USING gin (card_is_tags);