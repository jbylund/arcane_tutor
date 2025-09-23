-- Add frame and frame_effects columns to support frame: search
-- Based on Scryfall API fields:
-- - "frame": "2015" (frame version string)
-- - "frame_effects": ["showcase", "enchantment"] (array of frame effects)

ALTER TABLE magic.cards 
ADD COLUMN card_frame text,
ADD COLUMN card_frame_effects jsonb DEFAULT '[]'::jsonb;

-- Add constraints for the new frame_effects JSONB array column
ALTER TABLE magic.cards 
ADD CONSTRAINT card_frame_effects_must_be_array CHECK (jsonb_typeof(card_frame_effects) = 'array');

-- Add constraint to ensure frame_effects array elements are strings if not empty
ALTER TABLE magic.cards 
ADD CONSTRAINT card_frame_effects_strings_only CHECK (
    card_frame_effects = '[]'::jsonb OR
    NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(card_frame_effects) AS element
        WHERE jsonb_typeof(element) != 'string'
    )
);

-- Add indexes for efficient frame searching
CREATE INDEX idx_cards_frame ON magic.cards (card_frame) WHERE card_frame IS NOT NULL;
CREATE INDEX idx_cards_frame_effects_gin ON magic.cards USING GIN (card_frame_effects);