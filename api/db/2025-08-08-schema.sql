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
    card_colors jsonb NOT NULL, -- object of color codes, e.g. {"R": true, "G": true}
    card_color_identity jsonb NOT NULL, -- object of color identity codes, e.g. {"R": true, "G": true}
    card_keywords jsonb NOT NULL, -- object of keywords, e.g. {"Trample": true, "Flying": true}
    oracle_text text, -- card oracle text for searching

    edhrec_rank integer,

    -- pricing data - will be null for cards without pricing information
    price_usd real,
    price_eur real,
    price_tix real,

    -- creature only attributes - will be null a large fraction of the time
    creature_power integer,
    creature_power_text text,
    creature_toughness integer,
    creature_toughness_text text,

    card_oracle_tags jsonb NOT NULL DEFAULT '{}'::jsonb,


    -- constraints
    CONSTRAINT card_types_must_be_array CHECK (jsonb_typeof(card_types) = 'array'),
    CONSTRAINT card_subtypes_must_be_array CHECK (jsonb_typeof(card_subtypes) = 'array'),

    CONSTRAINT raw_card_is_object CHECK (jsonb_typeof(raw_card_blob) = 'object'),

    CONSTRAINT card_colors_must_be_object CHECK (jsonb_typeof(card_colors) = 'object'),
    CONSTRAINT card_colors_valid_colors CHECK (
        card_colors <@ '{"W": true, "U": true, "B": true, "R": true, "G": true, "C": true}'::jsonb
    ),
    CONSTRAINT card_color_identity_must_be_object CHECK (jsonb_typeof(card_color_identity) = 'object'),
    CONSTRAINT card_color_identity_valid_colors CHECK (
        card_color_identity <@ '{"W": true, "U": true, "B": true, "R": true, "G": true, "C": true}'::jsonb
    ),
    CONSTRAINT card_keywords_must_be_object CHECK (jsonb_typeof(card_keywords) = 'object'),

    CONSTRAINT card_oracle_tags_must_be_object CHECK (jsonb_typeof(card_oracle_tags) = 'object'),

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

/*
-- Partial indexes for major card types
CREATE INDEX idx_cards_artifacts ON magic.cards (card_name) WHERE card_types @> '["Artifact"]';
CREATE INDEX idx_cards_creatures ON magic.cards (card_name) WHERE card_types @> '["Creature"]';
CREATE INDEX idx_cards_enchantments ON magic.cards (card_name) WHERE card_types @> '["Enchantment"]';
CREATE INDEX idx_cards_instants ON magic.cards (card_name) WHERE card_types @> '["Instant"]';
CREATE INDEX idx_cards_lands ON magic.cards (card_name) WHERE card_types @> '["Land"]';
CREATE INDEX idx_cards_sorceries ON magic.cards (card_name) WHERE card_types @> '["Sorcery"]';
*/

CREATE UNIQUE INDEX idx_cards_name ON magic.cards (card_name);

-- Indexes for pricing data performance
CREATE INDEX idx_cards_price_usd ON magic.cards (price_usd) WHERE price_usd IS NOT NULL;
CREATE INDEX idx_cards_price_eur ON magic.cards (price_eur) WHERE price_eur IS NOT NULL;
CREATE INDEX idx_cards_price_tix ON magic.cards (price_tix) WHERE price_tix IS NOT NULL;
