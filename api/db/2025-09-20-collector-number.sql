-- Add collector number column to the cards table
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS collector_number text;

-- Add comment for the new column
COMMENT ON COLUMN magic.cards.collector_number IS 'Collector number (e.g. "123", "123a", "123★") - will be null for cards without collector number information';

-- Populate collector number column from existing raw card blob data
UPDATE magic.cards SET
    collector_number = raw_card_blob->>'collector_number'
WHERE raw_card_blob->>'collector_number' IS NOT NULL;

-- Index for collector number performance (using btree for range queries and exact matches)
CREATE INDEX IF NOT EXISTS idx_cards_collector_number ON magic.cards (collector_number) WHERE collector_number IS NOT NULL;