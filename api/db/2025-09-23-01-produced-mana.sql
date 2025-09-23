-- Migration to add produced_mana column for mana production searches
-- Date: 2025-09-23

-- Add produced_mana column for storing mana that cards can produce
-- This will store color codes similar to card_colors format: {"G": true, "R": true}
ALTER TABLE magic.cards ADD COLUMN produced_mana jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Add constraint to ensure produced_mana is an object
ALTER TABLE magic.cards ADD CONSTRAINT produced_mana_must_be_object 
    CHECK (jsonb_typeof(produced_mana) = 'object');

-- Add constraint to ensure only valid mana colors are stored
ALTER TABLE magic.cards ADD CONSTRAINT produced_mana_valid_colors 
    CHECK (produced_mana <@ '{"W": true, "U": true, "B": true, "R": true, "G": true, "C": true}'::jsonb);

-- Populate produced_mana from raw_card_blob data
-- Convert array format ["G", "R"] to object format {"G": true, "R": true}
UPDATE magic.cards 
SET produced_mana = (
    SELECT jsonb_object_agg(color, true)
    FROM jsonb_array_elements_text(raw_card_blob->'produced_mana') AS color
)
WHERE raw_card_blob ? 'produced_mana' 
AND jsonb_typeof(raw_card_blob->'produced_mana') = 'array'
AND jsonb_array_length(raw_card_blob->'produced_mana') > 0;

-- Create index on produced_mana for efficient searching
CREATE INDEX idx_cards_produced_mana ON magic.cards USING GIN (produced_mana) 
WHERE produced_mana != '{}'::jsonb;

-- Add comment for documentation
COMMENT ON COLUMN magic.cards.produced_mana IS 'Mana colors that this card can produce stored as object with color codes as keys (e.g., {"G": true, "R": true})';