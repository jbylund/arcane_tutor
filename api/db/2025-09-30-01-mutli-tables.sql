BEGIN;
DROP SCHEMA IF EXISTS magic CASCADE;
CREATE SCHEMA magic;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA magic;

CREATE TABLE magic.valid_rarities (
    card_rarity_int integer NOT NULL,
    card_rarity_text text NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_valid_rarities_card_rarity_int_card_rarity_text_key ON magic.valid_rarities (card_rarity_int, card_rarity_text);
CREATE UNIQUE INDEX IF NOT EXISTS idx_valid_rarities_card_rarity_text ON magic.valid_rarities (card_rarity_text);
CREATE UNIQUE INDEX IF NOT EXISTS idx_valid_rarities_card_rarity_int ON magic.valid_rarities (card_rarity_int);

INSERT INTO magic.valid_rarities (card_rarity_int, card_rarity_text) VALUES
    (0, 'common'),
    (1, 'uncommon'),
    (2, 'rare'),
    (3, 'mythic'),
    (4, 'special'),
    (5, 'bonus');


CREATE OR REPLACE FUNCTION magic.extract_collector_number_int(collector_number_text text) RETURNS integer
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    -- Extract first match of digits and convert to integer
    -- Returns NULL if no numeric characters found
    RETURN substring(collector_number_text from '[0-9]+')::integer;
EXCEPTION
    WHEN OTHERS THEN
        -- Return NULL for any casting errors (e.g., number too large)
        RETURN NULL;
END;
$$;

CREATE FUNCTION magic.rarity_text_to_int(rarity_text text) RETURNS integer
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    RETURN card_rarity_int FROM magic.valid_rarities WHERE card_rarity_text = rarity_text;
END;
$$;

CREATE FUNCTION magic.rarity_int_to_text(rarity_int integer) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    RETURN card_rarity_text FROM magic.valid_rarities WHERE card_rarity_int = rarity_int;
END;
$$;

CREATE TABLE magic.card_set_types (
    set_type text PRIMARY KEY
);

INSERT INTO magic.card_set_types (set_type) VALUES
    ('alchemy'),
    ('archenemy'),
    ('arsenal'),
    ('box'),
    ('commander'),
    ('core'),
    ('draft_innovation'),
    ('duel_deck'),
    ('eternal'),
    ('expansion'),
    ('from_the_vault'),
    ('funny'),
    ('masterpiece'),
    ('masters'),
    ('memorabilia'),
    ('minigame'),
    ('planechase'),
    ('premium_deck'),
    ('promo'),
    ('spellbook'),
    ('starter'),
    ('token'),
    ('treasure_chest'),
    ('vanguard');


CREATE TABLE magic.artists (
    artist_id uuid PRIMARY KEY,
    artist_name text NOT NULL
);

CREATE TABLE magic.illustrations (
    illustration_id uuid PRIMARY KEY
);

CREATE TABLE magic.illustration_artists (
    illustration_id uuid NOT NULL REFERENCES magic.illustrations(illustration_id),
    artist_id uuid NOT NULL REFERENCES magic.artists(artist_id)
);

CREATE TABLE magic.cards (
    card_id uuid PRIMARY KEY,
    card_name text NOT NULL,
    edhrec_rank integer,
    card_color_identity jsonb NOT NULL,
    card_oracle_tags jsonb NOT NULL,
    card_legalities jsonb NOT NULL,
    keywords jsonb NOT NULL,
    -- card has faces
    -- card has printings
    -- TODO: these could be stricter - i.e. for colors we can require it be a subset of valid colors
    CONSTRAINT check_color_identity_is_object CHECK (jsonb_typeof(card_color_identity) = 'object'),
    CONSTRAINT check_oracle_tags_is_object CHECK (jsonb_typeof(card_oracle_tags) = 'object'),
    CONSTRAINT check_legalities_is_object CHECK (jsonb_typeof(card_legalities) = 'object'),
    CONSTRAINT check_keywords_is_object CHECK (jsonb_typeof(keywords) = 'object')
);

CREATE TABLE magic.card_sets (
    -- Primary key and identifiers
    set_id uuid PRIMARY KEY,
    set_code text NOT NULL UNIQUE,
    mtgo_code text,
    arena_code text,
    tcgplayer_id integer,

    -- Set information
    set_name text NOT NULL,
    set_type text NOT NULL REFERENCES magic.card_set_types(set_type),
    released_at date,
    block_code text,
    set_block text,
    parent_set_code text,

    -- Set statistics
    -- card_count integer NOT NULL DEFAULT 0,
    printed_size integer,

    -- Set characteristics
    -- digital boolean NOT NULL DEFAULT false,
    -- foil_only boolean NOT NULL DEFAULT false,
    -- nonfoil_only boolean NOT NULL DEFAULT false,

    -- URIs and links
    -- scryfall_uri text NOT NULL,
    uri text NOT NULL,
    -- icon_svg_uri text NOT NULL,
    search_uri text NOT NULL,

    -- Raw data from API
    -- raw_set_blob jsonb NOT NULL,

    -- Constraints
    CONSTRAINT check_code_length CHECK (length(set_code) >= 3 AND length(set_code) <= 6),
    -- CONSTRAINT check_positive_card_count CHECK (card_count >= 0),
    CONSTRAINT check_positive_printed_size CHECK (printed_size IS NULL OR printed_size > 0)
    -- CONSTRAINT raw_set_is_object CHECK (jsonb_typeof(raw_set_blob) = 'object')
);

-- Indexes for card_sets table
CREATE INDEX IF NOT EXISTS idx_card_sets_code ON magic.card_sets USING hash (set_code);


CREATE TABLE magic.card_printings (
    card_printing_id uuid PRIMARY KEY,
    card_id uuid NOT NULL REFERENCES magic.cards(card_id),
    set_code text NOT NULL REFERENCES magic.card_sets(set_code),
    collector_number_text text NOT NULL,
    collector_number_int integer NOT NULL,
    rarity_text text NOT NULL, -- rarity: common, uncommon, rare, mythic, special, bonus
    rarity_int integer NOT NULL, -- integer representation of rarity
    border_color text NOT NULL, -- border color: black, white, borderless, gold...
    frame_bag jsonb NOT NULL, -- frame version and frame effects
    -- does everything else belong to either the face or the printing?
    CONSTRAINT check_collector_number_int_non_negative CHECK (0 < collector_number_int),
    CONSTRAINT check_collector_int_correct CHECK (collector_number_int = magic.extract_collector_number_int(collector_number_text)),
    CONSTRAINT check_rarity_pair FOREIGN KEY (rarity_text, rarity_int) REFERENCES magic.valid_rarities(card_rarity_text, card_rarity_int),
    CONSTRAINT check_border_color_lowercase CHECK (border_color = lower(border_color)),
    CONSTRAINT check_frame_bag_is_object CHECK (jsonb_typeof(frame_bag) = 'object')
);

CREATE TABLE magic.prices (
    card_printing_id uuid NOT NULL REFERENCES magic.card_printings(card_printing_id),
    price_usd real,
    price_usd_foil real,
    price_usd_etched real,
    price_eur real,
    price_eur_foil real,
    price_tix real,
    PRIMARY KEY (card_printing_id)
);

CREATE TABLE magic.card_faces (
    card_face_id uuid PRIMARY KEY,
    card_id uuid NOT NULL REFERENCES magic.cards(card_id),

    -- Face properties
    card_face_name text NOT NULL,
    face_index integer NOT NULL, -- 0 for first face, 1 for second face, etc.
    mana_cost_text text NOT NULL, -- Empty string if no cost
    mana_cost_jsonb jsonb,
    colors jsonb NOT NULL, -- Face colors as JSONB object
    cmc integer, -- Mana value of this particular face (for reversible cards)
    type_line text,
    face_types jsonb,
    face_subtypes jsonb,
    face_produced_mana jsonb,

    oracle_text text,
    power_text text, -- Can be numeric or * for variable power
    power_int integer, -- Integer representation of power
    toughness_text text, -- Can be numeric or * for variable toughness
    toughness_int integer, -- Integer representation of toughness
    loyalty_text text, -- For planeswalkers
    loyalty_int integer, -- Integer representation of loyalty
    defense_text text, -- For battles
    defense_int integer, -- Integer representation of defense

    CONSTRAINT check_colors_valid CHECK (jsonb_typeof(colors) = 'object' AND colors <@ '{"B": true, "C": true, "G": true, "R": true, "U": true, "W": true}'::jsonb),
    CONSTRAINT check_face_index_non_negative CHECK (face_index >= 0),
    CONSTRAINT check_mana_cost_not_null CHECK (mana_cost_text IS NOT NULL),

    CONSTRAINT check_mana_cost_jsonb_is_object CHECK (jsonb_typeof(mana_cost_jsonb) = 'object'),
    CONSTRAINT check_produced_mana_is_object CHECK (jsonb_typeof(face_produced_mana) = 'object'),
    CONSTRAINT check_subtypes_is_array CHECK (jsonb_typeof(face_subtypes) = 'array'),
    CONSTRAINT check_types_is_array CHECK (jsonb_typeof(face_types) = 'array')
);

CREATE TABLE magic.card_face_printings (
    card_face_printing_id uuid PRIMARY KEY,

    card_face_id uuid NOT NULL REFERENCES magic.card_faces(card_face_id),
    illustration_id uuid NOT NULL REFERENCES magic.illustrations(illustration_id),
    card_printing_id uuid NOT NULL REFERENCES magic.card_printings(card_printing_id),
    layout text, -- Layout of this card face (for reversible cards)
    watermark text, -- Watermark on this particular card face
    flavor_text text,
    image_uris jsonb, -- Object providing URIs to imagery for this face

    -- Constraints
    CONSTRAINT check_image_uris_is_object CHECK (image_uris IS NULL OR jsonb_typeof(image_uris) = 'object'),
    CONSTRAINT check_watermark_lowercase CHECK (watermark = lower(watermark)),
    CONSTRAINT check_layout_lowercase CHECK (layout = lower(layout))
);

-- Comments for card_sets table
COMMENT ON TABLE magic.card_sets IS 'Magic: The Gathering card sets from Scryfall API';

CREATE TABLE magic.raw_cards (
    scryfall_id uuid PRIMARY KEY,
    raw_card_blob jsonb NOT NULL,
    CONSTRAINT check_raw_card_is_object CHECK (jsonb_typeof(raw_card_blob) = 'object')
);

COMMIT;
