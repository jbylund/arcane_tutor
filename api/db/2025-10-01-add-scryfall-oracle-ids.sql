-- Migration: Add scryfall_id and oracle_id columns to magic.cards table
-- Date: 2025-01-16
-- Description: Adds UUID columns for Scryfall and Oracle IDs, updates indexes

-- Add scryfall_id and oracle_id columns as UUID
ALTER TABLE magic.cards
ADD COLUMN scryfall_id UUID NOT NULL,
ADD COLUMN oracle_id UUID;

-- Drop the existing unique index on card_name
DROP INDEX IF EXISTS magic.idx_cards_name;

-- Create a new non-unique index on card_name
CREATE INDEX IF NOT EXISTS idx_cards_name ON magic.cards USING btree (card_name);

-- Create unique index on scryfall_id
CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_scryfall_id ON magic.cards USING btree (scryfall_id) WHERE (scryfall_id IS NOT NULL);

-- Create index on oracle_id (not unique as multiple printings can share same oracle_id)
CREATE INDEX IF NOT EXISTS idx_cards_oracle_id ON magic.cards USING btree (oracle_id) WHERE (oracle_id IS NOT NULL);

-- Add comments for the new columns
COMMENT ON COLUMN magic.cards.scryfall_id IS 'Unique Scryfall ID for this specific card printing - UUID format';
COMMENT ON COLUMN magic.cards.oracle_id IS 'Oracle ID shared by all printings of the same card - UUID format';
