-- Migration: Add default prefer score calculation
-- This migration adds a prefer_score column to the magic.cards table and populates it
-- based on various card attributes to implement the default prefer ordering

-- Add prefer_score column
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS prefer_score real;

-- Create a function to calculate the prefer score for a card
CREATE OR REPLACE FUNCTION magic.calculate_prefer_score(
    p_card_border text,
    p_card_frame_data jsonb,
    p_illustration_id uuid,
    p_card_rarity_int integer
) RETURNS real
    LANGUAGE plpgsql IMMUTABLE
AS $$
DECLARE
    v_score real := 0;
    v_border_score real := 0;
    v_frame_score real := 0;
    v_artwork_score real := 0;
    v_rarity_score real := 0;
    v_extended_art_score real := 0;
    v_illustration_count integer;
BEGIN
    -- Border scoring: silver/gold (0) < white (20) < black (100)
    v_border_score := CASE LOWER(COALESCE(p_card_border, ''))
        WHEN 'black' THEN 100
        WHEN 'white' THEN 20
        WHEN 'borderless' THEN 20  -- Treat borderless similar to white
        WHEN 'silver' THEN 0
        WHEN 'gold' THEN 0
        ELSE 0
    END;

    -- Frame scoring: other frames (0) < 2003 (50) < 2015 (100)
    -- Frame data is stored as JSONB with frame versions as keys
    v_frame_score := CASE
        WHEN p_card_frame_data ? '2015' THEN 100
        WHEN p_card_frame_data ? '2003' THEN 50
        WHEN p_card_frame_data ? '1997' THEN 25
        WHEN p_card_frame_data ? '1993' THEN 10
        ELSE 0
    END;

    -- Artwork popularity scoring based on illustration_id count
    -- Get the count of how many times this illustration has been printed
    IF p_illustration_id IS NOT NULL THEN
        SELECT COUNT(*) INTO v_illustration_count
        FROM magic.cards
        WHERE illustration_id = p_illustration_id;
        
        -- Scale using logarithmic function: min(100, log(count) / log(40) * 100)
        -- 40+ printings = 100 points, logarithmic scaling for better distribution
        -- Handle count = 0 or 1 cases (ln(1) = 0)
        IF v_illustration_count > 1 THEN
            v_artwork_score := LEAST(100, (LN(v_illustration_count) / LN(40)) * 100);
        ELSE
            v_artwork_score := 0;
        END IF;
    END IF;

    -- Rarity scoring: prefer lower rarity (common is most preferred)
    -- Proportional to booster pack frequency
    -- Common (~11 per pack), Uncommon (~3 per pack), Rare (~1 per pack), Mythic (~1/8 per pack)
    v_rarity_score := CASE p_card_rarity_int
        WHEN 0 THEN 100  -- common (most preferred)
        WHEN 1 THEN 27   -- uncommon
        WHEN 2 THEN 8    -- rare
        WHEN 3 THEN 1    -- mythic (least preferred)
        WHEN 4 THEN 0    -- special
        WHEN 5 THEN 0    -- bonus
        ELSE 0
    END;

    -- Extended art frame effect scoring
    -- Check if card has Extendedart frame effect (titlecased in our data)
    IF p_card_frame_data ? 'Extendedart' THEN
        v_extended_art_score := 100;
    END IF;

    -- Calculate total score
    v_score := v_border_score + v_frame_score + v_artwork_score + v_rarity_score + v_extended_art_score;

    RETURN v_score;
END;
$$;

COMMENT ON FUNCTION magic.calculate_prefer_score(text, jsonb, uuid, integer) IS 
    'Calculate the default prefer score for a card based on border, frame, artwork popularity, rarity, and extended art';

-- Populate the prefer_score column for all existing cards
UPDATE magic.cards
SET prefer_score = magic.calculate_prefer_score(
    card_border,
    card_frame_data,
    illustration_id,
    card_rarity_int
);

-- Create an index on prefer_score for efficient ordering
CREATE INDEX IF NOT EXISTS idx_cards_prefer_score ON magic.cards USING btree (prefer_score DESC NULLS LAST);

COMMENT ON COLUMN magic.cards.prefer_score IS 
    'Default prefer score calculated from border, frame, artwork popularity, rarity, and extended art. Higher scores are more preferred.';

-- Create a trigger function to automatically update prefer_score on insert/update
CREATE OR REPLACE FUNCTION magic.update_prefer_score_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.prefer_score := magic.calculate_prefer_score(
        NEW.card_border,
        NEW.card_frame_data,
        NEW.illustration_id,
        NEW.card_rarity_int
    );
    RETURN NEW;
END;
$$;

-- Create trigger to automatically calculate prefer_score on insert or update
DROP TRIGGER IF EXISTS trg_update_prefer_score ON magic.cards;
CREATE TRIGGER trg_update_prefer_score
    BEFORE INSERT OR UPDATE OF card_border, card_frame_data, illustration_id, card_rarity_int
    ON magic.cards
    FOR EACH ROW
    EXECUTE FUNCTION magic.update_prefer_score_trigger();

COMMENT ON TRIGGER trg_update_prefer_score ON magic.cards IS
    'Automatically calculates and updates prefer_score when card attributes change';
