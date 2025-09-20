-- Migration to add collector_number columns (text and numeric)
-- Date: 2025-09-21

-- Add collector_number text column for exact matching
ALTER TABLE magic.cards ADD COLUMN collector_number text;

-- Add collector_number_int column for numeric comparisons
ALTER TABLE magic.cards ADD COLUMN collector_number_int integer;

-- Create function to extract numeric portion from collector numbers
-- Examples: "123" -> 123, "123a" -> 123, "123b" -> 123, "abc" -> NULL
CREATE OR REPLACE FUNCTION magic.extract_collector_number_int(collector_number_text text)
RETURNS integer AS $$
BEGIN
    -- Extract numeric characters and convert to integer
    -- Returns NULL if no numeric characters found
    DECLARE
        numeric_part text;
    BEGIN
        -- Use regexp_replace to remove all non-numeric characters
        numeric_part := regexp_replace(collector_number_text, '[^0-9]', '', 'g');
        
        -- Return NULL if no numeric characters remain or if empty
        IF numeric_part = '' OR numeric_part IS NULL THEN
            RETURN NULL;
        END IF;
        
        -- Cast to integer, handle potential overflow
        RETURN numeric_part::integer;
    EXCEPTION
        WHEN OTHERS THEN
            -- Return NULL for any casting errors (e.g., number too large)
            RETURN NULL;
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Populate collector_number from raw_card_blob data
-- Handle potential dirty data by extracting as text first
UPDATE magic.cards 
SET collector_number = raw_card_blob->>'collector_number'
WHERE raw_card_blob ? 'collector_number' 
AND raw_card_blob->>'collector_number' IS NOT NULL
AND TRIM(raw_card_blob->>'collector_number') != '';

-- Populate collector_number_int using the extraction function
UPDATE magic.cards 
SET collector_number_int = magic.extract_collector_number_int(collector_number)
WHERE collector_number IS NOT NULL;

-- Add index on collector_number for exact text matching
CREATE INDEX idx_cards_collector_number ON magic.cards (collector_number) 
WHERE collector_number IS NOT NULL;

-- Add index on collector_number_int for numeric comparisons
CREATE INDEX idx_cards_collector_number_int ON magic.cards (collector_number_int) 
WHERE collector_number_int IS NOT NULL;

-- Add comments for documentation
COMMENT ON COLUMN magic.cards.collector_number IS 'Card collector number as text exactly as it appears on the card (can be numeric or contain letters like "123a")';
COMMENT ON COLUMN magic.cards.collector_number_int IS 'Card collector number as integer extracted from text for numeric comparisons (e.g., "123a" -> 123, NULL for non-numeric)';
COMMENT ON FUNCTION magic.extract_collector_number_int(text) IS 'Extract numeric portion from collector number text for sorting and comparison purposes';