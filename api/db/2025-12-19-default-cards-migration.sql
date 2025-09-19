-- Migration to support default_cards instead of oracle_cards
-- This changes the schema to handle non-unique card names and use set + collector_number as unique identifier

-- Drop the existing unique constraint on card_name
DROP INDEX IF EXISTS idx_cards_name;

-- Add new columns for the unique identifier
ALTER TABLE magic.cards 
ADD COLUMN IF NOT EXISTS card_set text,
ADD COLUMN IF NOT EXISTS collector_number text,
ADD COLUMN IF NOT EXISTS scryfall_id text;

-- Create new unique constraint on set + collector_number
CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_set_collector ON magic.cards (card_set, collector_number);

-- Create index on scryfall_id for fast lookups
CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_scryfall_id ON magic.cards (scryfall_id);

-- Create index on card_name for search performance (but not unique anymore)
CREATE INDEX IF NOT EXISTS idx_cards_name_search ON magic.cards (card_name);

-- Create index on oracle_id for grouping unique cards
CREATE INDEX IF NOT EXISTS idx_cards_oracle_id ON magic.cards ((raw_card_blob->>'oracle_id'));

-- Add constraints to ensure the new fields are not null
ALTER TABLE magic.cards 
ADD CONSTRAINT card_set_not_null CHECK (card_set IS NOT NULL),
ADD CONSTRAINT collector_number_not_null CHECK (collector_number IS NOT NULL),
ADD CONSTRAINT scryfall_id_not_null CHECK (scryfall_id IS NOT NULL);