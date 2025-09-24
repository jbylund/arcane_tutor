-- Migration to add watermark column with hash index
-- Date: 2025-01-16

-- Add watermark column for card watermarks (guild symbols, set symbols, etc.)
ALTER TABLE magic.cards ADD COLUMN card_watermark text;

-- Populate card_watermark from raw_card_blob data
UPDATE magic.cards
SET card_watermark = lower(raw_card_blob->>'watermark')
WHERE raw_card_blob ? 'watermark'
AND raw_card_blob->>'watermark' IS NOT NULL
AND TRIM(raw_card_blob->>'watermark') != '';

-- Create hash index for efficient lookup
-- Hash indexes are perfect for equality searches on small value domains
CREATE INDEX idx_cards_watermark ON magic.cards USING HASH (card_watermark)
WHERE card_watermark IS NOT NULL;

-- Add CHECK constraint to ensure values are stored in lowercase
ALTER TABLE magic.cards ADD CONSTRAINT check_card_watermark_lowercase
CHECK (card_watermark IS NULL OR card_watermark = lower(card_watermark));

-- Add comment for documentation
COMMENT ON COLUMN magic.cards.card_watermark IS 'Card watermark (guild symbols, set symbols, etc.) - stored in lowercase';
