-- Migration to add card rarity columns and conversion function
-- Date: 2025-09-20

-- Add card rarity columns
ALTER TABLE magic.cards ADD COLUMN card_rarity_text text;
ALTER TABLE magic.cards ADD COLUMN card_rarity_int integer;

-- Create function to convert rarity text to integer
CREATE OR REPLACE FUNCTION magic.rarity_text_to_int(rarity_text text)
RETURNS integer AS $$
BEGIN
    RETURN CASE LOWER(TRIM(rarity_text))
        WHEN 'common' THEN 0
        WHEN 'uncommon' THEN 1
        WHEN 'rare' THEN 2
        WHEN 'mythic' THEN 3
        WHEN 'special' THEN 4
        WHEN 'bonus' THEN 5
        ELSE -1
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Create function to convert rarity integer to text
CREATE OR REPLACE FUNCTION magic.rarity_int_to_text(rarity_int integer)
RETURNS text AS $$
BEGIN
    RETURN CASE rarity_int
        WHEN 0 THEN 'common'
        WHEN 1 THEN 'uncommon'
        WHEN 2 THEN 'rare'
        WHEN 3 THEN 'mythic'
        WHEN 4 THEN 'special'
        WHEN 5 THEN 'bonus'
        ELSE NULL
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Create trigger to automatically sync card_rarity_int when card_rarity_text is updated
CREATE OR REPLACE FUNCTION magic.sync_card_rarity_int()
RETURNS trigger AS $$
BEGIN
    NEW.card_rarity_int = magic.rarity_text_to_int(NEW.card_rarity_text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sync_card_rarity_int_trigger
    BEFORE INSERT OR UPDATE OF card_rarity_text ON magic.cards
    FOR EACH ROW
    EXECUTE FUNCTION magic.sync_card_rarity_int();

-- Add comments for documentation
COMMENT ON COLUMN magic.cards.card_rarity_text IS 'Card rarity as text: common, uncommon, rare, mythic, special, bonus';
COMMENT ON COLUMN magic.cards.card_rarity_int IS 'Card rarity as integer for efficient ordering and comparison: common=0, uncommon=1, rare=2, mythic=3, special=4, bonus=5';
COMMENT ON FUNCTION magic.rarity_text_to_int(text) IS 'Convert rarity text to integer for ordering and comparison';
COMMENT ON FUNCTION magic.rarity_int_to_text(integer) IS 'Convert rarity integer back to text';