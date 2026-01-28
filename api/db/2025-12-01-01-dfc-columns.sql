-- Migration: Add Double-Faced Card (DFC) support columns
-- This adds face_idx and face_name columns to support storing one row per face per printing

-- Add face_idx column (1 = front face, 2 = back face)
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS face_idx integer NOT NULL DEFAULT 1;

-- Add face_name column (individual face name, e.g., "Hound Tamer" for a DFC)
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS face_name text;

-- Update existing cards to set face_name = card_name (for single-faced cards)
UPDATE magic.cards SET face_name = card_name WHERE face_name IS NULL;

-- Now make face_name NOT NULL
ALTER TABLE magic.cards ALTER COLUMN face_name SET NOT NULL;

-- Drop the old unique index on scryfall_id (if exists) and create new one that includes face_idx
DROP INDEX IF EXISTS magic.idx_cards_scryfall_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_scryfall_id_face ON magic.cards USING btree (scryfall_id, face_idx);

-- Add index on face_name for searching by individual face names
CREATE INDEX IF NOT EXISTS idx_cards_face_name ON magic.cards USING btree (face_name);
CREATE INDEX IF NOT EXISTS idx_cards_face_name_trgm ON magic.cards USING gin (face_name magic.gin_trgm_ops) WHERE (face_name IS NOT NULL);
