-- Migration to fix NULL card_subtypes issue
-- Instead of handling NULL in query logic, ensure column is never NULL

-- Update existing NULL values to empty arrays
UPDATE magic.cards 
SET card_subtypes = '[]'::jsonb 
WHERE card_subtypes IS NULL;

-- Alter the column to be NOT NULL with default empty array
ALTER TABLE magic.cards 
ALTER COLUMN card_subtypes SET NOT NULL,
ALTER COLUMN card_subtypes SET DEFAULT '[]'::jsonb;