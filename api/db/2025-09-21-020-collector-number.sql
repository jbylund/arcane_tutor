-- Migration to add collector_number columns (text and numeric)
-- Date: 2025-09-21

-- Add collector_number text column for exact matching
ALTER TABLE magic.cards ADD COLUMN collector_number text;

-- Add collector_number_int column for numeric comparisons
ALTER TABLE magic.cards ADD COLUMN collector_number_int integer;

-- Populate collector_number from raw_card_blob data
-- Handle potential dirty data by extracting as text first
UPDATE magic.cards 
SET collector_number = raw_card_blob->>'collector_number'
WHERE raw_card_blob ? 'collector_number' 
AND raw_card_blob->>'collector_number' IS NOT NULL
AND TRIM(raw_card_blob->>'collector_number') != '';

-- Populate collector_number_int from collector_number where it's a valid integer
-- Use a function to safely cast to integer, returning NULL for non-numeric values
UPDATE magic.cards 
SET collector_number_int = CASE 
    WHEN collector_number ~ '^[0-9]+$' THEN collector_number::integer
    ELSE NULL
END
WHERE collector_number IS NOT NULL;

-- Add index on collector_number for exact text matching
CREATE INDEX idx_cards_collector_number ON magic.cards (collector_number) 
WHERE collector_number IS NOT NULL;

-- Add index on collector_number_int for numeric comparisons
CREATE INDEX idx_cards_collector_number_int ON magic.cards (collector_number_int) 
WHERE collector_number_int IS NOT NULL;

-- Add comments for documentation
COMMENT ON COLUMN magic.cards.collector_number IS 'Card collector number as text exactly as it appears on the card (can be numeric or contain letters like "123a")';
COMMENT ON COLUMN magic.cards.collector_number_int IS 'Card collector number as integer for numeric comparisons (NULL for non-numeric collector numbers)';