DROP SCHEMA IF EXISTS magic CASCADE;

CREATE SCHEMA IF NOT EXISTS magic;

-- Function to check if JSONB array is sorted alphabetically
CREATE OR REPLACE FUNCTION is_sorted_alphabetically(arr jsonb)
RETURNS boolean AS $$
BEGIN
    RETURN arr = (
        SELECT jsonb_agg(value ORDER BY value)
        FROM jsonb_array_elements_text(arr)
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to check if all elements in JSONB array are INITCAP
CREATE OR REPLACE FUNCTION all_elements_initcap(arr jsonb)
RETURNS boolean AS $$
BEGIN
    RETURN NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(arr) AS element
        WHERE element != initcap(element)
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION json_array_to_array(jsonbin jsonb)
RETURNS text[] as
$$
  SELECT array_agg(el) FROM jsonb_array_elements_text(jsonbin) el;
$$ LANGUAGE sql IMMUTABLE;

CREATE TABLE IF NOT EXISTS magic.cards (
    card_name text NOT NULL,
    cmc integer, -- can be null for weird things
    mana_cost_text text,
    mana_cost_jsonb jsonb,
    raw_card_blob jsonb NOT NULL,
    card_types jsonb NOT NULL, -- list of strings (e.g. ["Creature", "Artifact"])
    card_subtypes jsonb, -- list of strings (e.g. ["Bird", "Knight"])
    card_colors jsonb NOT NULL, -- list of strings (e.g. ["White", "Blue"])

    -- creature only attributes - will be null a large fraction of the time
    creature_power integer,
    creature_power_text text,
    creature_toughness integer,
    creature_toughness_text text,

    -- constraints
    CONSTRAINT card_types_must_be_array CHECK (jsonb_typeof(card_types) = 'array'),
    CONSTRAINT card_subtypes_must_be_array CHECK (jsonb_typeof(card_subtypes) = 'array'),
    CONSTRAINT card_colors_must_be_array CHECK (jsonb_typeof(card_colors) = 'array'),
    CONSTRAINT raw_card_is_object CHECK (jsonb_typeof(raw_card_blob) = 'object'),
    CONSTRAINT card_colors_valid_colors CHECK (
        card_colors <@ '["W", "U", "B", "R", "G"]'::jsonb
    ),
    CONSTRAINT card_colors_alphabetical CHECK (is_sorted_alphabetically(card_colors)),
    CONSTRAINT card_types_initcap CHECK (all_elements_initcap(card_types)),
    CONSTRAINT card_subtypes_initcap CHECK (all_elements_initcap(card_subtypes)),
    CONSTRAINT card_colors_initcap CHECK (all_elements_initcap(card_colors)),
    
    -- Creature-only attribute constraints
    CONSTRAINT creature_attributes_null_for_non_creatures CHECK (
        (card_types ?| Array['Creature']) OR
        (card_subtypes ?| Array['Vehicle','Spacecraft']) OR
        (
            creature_power IS NULL AND 
            creature_power_text IS NULL AND 
            creature_toughness IS NULL AND 
            creature_toughness_text IS NULL
        )
    )
);

-- Partial indexes for major card types
CREATE INDEX idx_cards_artifacts ON magic.cards (card_name) WHERE card_types @> '["Artifact"]';
CREATE INDEX idx_cards_creatures ON magic.cards (card_name) WHERE card_types @> '["Creature"]';
CREATE INDEX idx_cards_enchantments ON magic.cards (card_name) WHERE card_types @> '["Enchantment"]';
CREATE INDEX idx_cards_instants ON magic.cards (card_name) WHERE card_types @> '["Instant"]';
CREATE INDEX idx_cards_lands ON magic.cards (card_name) WHERE card_types @> '["Land"]';
CREATE INDEX idx_cards_sorceries ON magic.cards (card_name) WHERE card_types @> '["Sorcery"]';

CREATE UNIQUE INDEX idx_cards_name ON magic.cards (card_name);

/*
INSERT INTO magic.cards (
    card_name, 
    cmc, 
    mana_cost_text, 
    raw_card_blob, 
    card_types, 
    card_colors
) VALUES (
    'Boros Charm',
    2,
    '{R}{W}',
    '{"name": "Boros Charm"}'::jsonb,
    '["Instant"]'::jsonb,
    '["white", "red"]'::jsonb  -- This should FAIL - should be ["red", "white"]
);
*/