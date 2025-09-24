-- Migration to add layout and border columns with hash indexes
-- Date: 2025-09-24

-- Add layout column for card layout (normal, split, flip, transform, etc.)
ALTER TABLE magic.cards ADD COLUMN card_layout text;

-- Add border column for border color (black, white, borderless, silver, gold)
ALTER TABLE magic.cards ADD COLUMN card_border text;

-- Populate card_layout from raw_card_blob data
UPDATE magic.cards 
SET card_layout = lower(raw_card_blob->>'layout')
WHERE raw_card_blob ? 'layout' 
AND raw_card_blob->>'layout' IS NOT NULL
AND TRIM(raw_card_blob->>'layout') != '';

-- Populate card_border from raw_card_blob data
UPDATE magic.cards 
SET card_border = lower(raw_card_blob->>'border_color')
WHERE raw_card_blob ? 'border_color' 
AND raw_card_blob->>'border_color' IS NOT NULL
AND TRIM(raw_card_blob->>'border_color') != '';

-- Create hash indexes for efficient lookup (as requested in issue)
-- Hash indexes are perfect for equality searches on small value domains
CREATE INDEX idx_cards_layout ON magic.cards USING HASH (card_layout) 
WHERE card_layout IS NOT NULL;

CREATE INDEX idx_cards_border ON magic.cards USING HASH (card_border) 
WHERE card_border IS NOT NULL;

-- Add comments for documentation
COMMENT ON COLUMN magic.cards.card_layout IS 'Card layout type (normal, split, flip, transform, etc.)';
COMMENT ON COLUMN magic.cards.card_border IS 'Card border color (black, white, borderless, silver, gold)';