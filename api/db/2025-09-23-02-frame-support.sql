-- Add frame data column to support frame: search
-- Based on Scryfall API fields:
-- - "frame": "2015" (frame version string) 
-- - "frame_effects": ["showcase", "legendary"] (array of frame effects)
-- Combined into single JSONB object: {"2015": true, "showcase": true, "legendary": true}

ALTER TABLE magic.cards 
ADD COLUMN card_frame_data jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Add constraints for the frame data JSONB object column
ALTER TABLE magic.cards 
ADD CONSTRAINT card_frame_data_must_be_object CHECK (jsonb_typeof(card_frame_data) = 'object');

-- Add index for efficient frame searching using JSONB operators
CREATE INDEX idx_cards_frame_data_gin ON magic.cards USING GIN (card_frame_data);