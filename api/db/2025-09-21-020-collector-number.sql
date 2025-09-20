-- Migration to add collector_number column
-- Date: 2025-09-21

-- Add collector_number column to cards table
ALTER TABLE magic.cards ADD COLUMN collector_number text;

-- Populate collector_number from raw_card_blob data
-- Handle potential dirty data by extracting as text first
UPDATE magic.cards 
SET collector_number = raw_card_blob->>'collector_number'
WHERE raw_card_blob ? 'collector_number' 
AND raw_card_blob->>'collector_number' IS NOT NULL
AND TRIM(raw_card_blob->>'collector_number') != '';

-- Add index on collector_number for performance
-- Use a partial index to exclude NULL values
CREATE INDEX idx_cards_collector_number ON magic.cards (collector_number) 
WHERE collector_number IS NOT NULL;

-- Add comment for documentation
COMMENT ON COLUMN magic.cards.collector_number IS 'Card collector number as it appears on the card (can be numeric or contain letters like "123a")';