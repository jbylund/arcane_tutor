CREATE TABLE magic.cards ();

CREATE TABLE magic.card_printings ();

CREATE TABLE magic.card_faces (
    card_face_id uuid PRIMARY KEY,

    -- Face properties
    card_face_name text NOT NULL,
    mana_cost text NOT NULL DEFAULT '', -- Empty string if no cost
    cmc integer, -- Mana value of this particular face (for reversible cards)
    type_line text,
    oracle_text text,
    power text, -- Can be numeric or * for variable power
    toughness text, -- Can be numeric or * for variable toughness
    loyalty text, -- For planeswalkers
    defense text, -- For battles
);

CREATE TABLE magic.card_face_printings (
    card_face_printing_id uuid PRIMARY KEY,
    card_face_id uuid NOT NULL REFERENCES magic.card_faces(card_face_id),

    illustration_id uuid PRIMARY KEY,
    card_printing_id uuid NOT NULL, -- References card_printings table
    face_index integer NOT NULL, -- 0 for first face, 1 for second face, etc.
    
    -- Colors and indicators
    colors jsonb, -- Face colors as JSONB object
    color_indicator jsonb, -- Color indicator as JSONB object
    
    -- Face characteristics
    layout text, -- Layout of this card face (for reversible cards)
    watermark text, -- Watermark on this particular card face
    
    -- Artist information
    artist text,
    artist_id uuid,
    
    -- Flavor and text
    flavor_text text,
    
    -- Image information
    image_uris jsonb, -- Object providing URIs to imagery for this face
    
    -- Raw data from API
    raw_face_blob jsonb NOT NULL,
    
    -- Constraints
    CONSTRAINT check_face_index_non_negative CHECK (face_index >= 0),
    CONSTRAINT check_mana_cost_not_null CHECK (mana_cost IS NOT NULL),
    CONSTRAINT check_object_is_card_face CHECK (object = 'card_face'),
    CONSTRAINT check_colors_is_object CHECK (colors IS NULL OR jsonb_typeof(colors) = 'object'),
    CONSTRAINT check_color_indicator_is_object CHECK (color_indicator IS NULL OR jsonb_typeof(color_indicator) = 'object'),
    CONSTRAINT check_image_uris_is_object CHECK (image_uris IS NULL OR jsonb_typeof(image_uris) = 'object'),
    CONSTRAINT check_raw_face_is_object CHECK (jsonb_typeof(raw_face_blob) = 'object'),
    CONSTRAINT check_colors_valid CHECK (colors IS NULL OR colors <@ '{"B": true, "C": true, "G": true, "R": true, "U": true, "W": true}'::jsonb),
    CONSTRAINT check_color_indicator_valid CHECK (color_indicator IS NULL OR color_indicator <@ '{"B": true, "C": true, "G": true, "R": true, "U": true, "W": true}'::jsonb)
);

-- Comments for card_face_printings table
COMMENT ON TABLE magic.card_face_printings IS 'Individual card face printings from Scryfall API - represents each face of multiface cards';
COMMENT ON COLUMN magic.card_face_printings.illustration_id IS 'Unique identifier for the card face artwork that remains consistent across reprints';
COMMENT ON COLUMN magic.card_face_printings.card_printing_id IS 'Reference to the parent card printing this face belongs to';
COMMENT ON COLUMN magic.card_face_printings.face_index IS 'Index of this face within the card (0 for first face, 1 for second face, etc.)';
COMMENT ON COLUMN magic.card_face_printings.oracle_id IS 'Oracle ID of this particular face (for reversible cards)';
COMMENT ON COLUMN magic.card_face_printings.object IS 'Content type for this object, always "card_face"';
COMMENT ON COLUMN magic.card_face_printings.name IS 'Name of this particular face';
COMMENT ON COLUMN magic.card_face_printings.mana_cost IS 'Mana cost for this face (empty string if absent)';
COMMENT ON COLUMN magic.card_face_printings.cmc IS 'Mana value of this particular face (for reversible cards)';
COMMENT ON COLUMN magic.card_face_printings.type_line IS 'Type line of this particular face (for reversible cards)';
COMMENT ON COLUMN magic.card_face_printings.oracle_text IS 'Oracle text for this face';
COMMENT ON COLUMN magic.card_face_printings.power IS 'Power of this face (can be numeric or * for variable)';
COMMENT ON COLUMN magic.card_face_printings.toughness IS 'Toughness of this face (can be numeric or * for variable)';
COMMENT ON COLUMN magic.card_face_printings.loyalty IS 'Loyalty of this face (for planeswalkers)';
COMMENT ON COLUMN magic.card_face_printings.defense IS 'Defense of this face (for battles)';
COMMENT ON COLUMN magic.card_face_printings.colors IS 'Face colors as JSONB object with color codes as keys';
COMMENT ON COLUMN magic.card_face_printings.color_indicator IS 'Color indicator as JSONB object with color codes as keys';
COMMENT ON COLUMN magic.card_face_printings.layout IS 'Layout of this card face (for reversible cards)';
COMMENT ON COLUMN magic.card_face_printings.watermark IS 'Watermark on this particular card face';
COMMENT ON COLUMN magic.card_face_printings.artist IS 'Name of the illustrator of this card face';
COMMENT ON COLUMN magic.card_face_printings.artist_id IS 'ID of the illustrator of this card face';
COMMENT ON COLUMN magic.card_face_printings.flavor_text IS 'Flavor text printed on this face';
COMMENT ON COLUMN magic.card_face_printings.printed_name IS 'Localized name printed on this face';
COMMENT ON COLUMN magic.card_face_printings.printed_text IS 'Localized text printed on this face';
COMMENT ON COLUMN magic.card_face_printings.printed_type_line IS 'Localized type line printed on this face';
COMMENT ON COLUMN magic.card_face_printings.image_uris IS 'Object providing URIs to imagery for this face';
COMMENT ON COLUMN magic.card_face_printings.raw_face_blob IS 'Complete raw JSON data from Scryfall API for this face';

CREATE TABLE magic.card_set_types (
    set_type text PRIMARY KEY
);

INSERT INTO magic.card_set_types (set_type) VALUES (
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
    ('vanguard')
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
    card_count integer NOT NULL DEFAULT 0,
    printed_size integer,
    
    -- Set characteristics
    digital boolean NOT NULL DEFAULT false,
    foil_only boolean NOT NULL DEFAULT false,
    nonfoil_only boolean NOT NULL DEFAULT false,
    
    -- URIs and links
    scryfall_uri text NOT NULL,
    uri text NOT NULL,
    icon_svg_uri text NOT NULL,
    search_uri text NOT NULL,
    
    -- Raw data from API
    raw_set_blob jsonb NOT NULL,
    
    -- Constraints
    CONSTRAINT check_code_length CHECK (length(code) >= 3 AND length(code) <= 6),
    CONSTRAINT check_positive_card_count CHECK (card_count >= 0),
    CONSTRAINT check_positive_printed_size CHECK (printed_size IS NULL OR printed_size > 0),
    CONSTRAINT raw_set_is_object CHECK (jsonb_typeof(raw_set_blob) = 'object')
);

-- Indexes for card_sets table
CREATE INDEX IF NOT EXISTS idx_card_sets_code ON magic.card_sets USING hash (code);

-- Comments for card_sets table
COMMENT ON TABLE magic.card_sets IS 'Magic: The Gathering card sets from Scryfall API';

COMMENT ON COLUMN magic.card_sets.arena_code IS 'Unique code for this set on Arena, may differ from regular code';
COMMENT ON COLUMN magic.card_sets.block IS 'Block or group name for this set, if any';
COMMENT ON COLUMN magic.card_sets.block_code IS 'Block code for this set, if any';
COMMENT ON COLUMN magic.card_sets.card_count IS 'Number of cards in this set';
COMMENT ON COLUMN magic.card_sets.code IS 'Unique three to six-letter code for this set (e.g., "zen", "iko")';
COMMENT ON COLUMN magic.card_sets.digital IS 'True if this set was only released in a video game';
COMMENT ON COLUMN magic.card_sets.foil_only IS 'True if this set contains only foil cards';
COMMENT ON COLUMN magic.card_sets.icon_svg_uri IS 'URI to an SVG file for this set''s icon on Scryfall''s CDN';
COMMENT ON COLUMN magic.card_sets.id IS 'Unique UUID for this set on Scryfall';
COMMENT ON COLUMN magic.card_sets.mtgo_code IS 'Unique code for this set on MTGO, may differ from regular code';
COMMENT ON COLUMN magic.card_sets.name IS 'English name of the set';
COMMENT ON COLUMN magic.card_sets.nonfoil_only IS 'True if this set contains only nonfoil cards';
COMMENT ON COLUMN magic.card_sets.parent_set_code IS 'Set code for the parent set, if any (promo and token sets often have parent sets)';
COMMENT ON COLUMN magic.card_sets.printed_size IS 'Denominator for the set''s printed collector numbers';
COMMENT ON COLUMN magic.card_sets.raw_set_blob IS 'Complete raw JSON data from Scryfall API for this set';
COMMENT ON COLUMN magic.card_sets.released_at IS 'Date the set was released or first card was printed (GMT-8 Pacific time)';
COMMENT ON COLUMN magic.card_sets.scryfall_uri IS 'Link to this set''s permapage on Scryfall''s website';
COMMENT ON COLUMN magic.card_sets.search_uri IS 'Scryfall API URI to begin paginating over cards in this set';
COMMENT ON COLUMN magic.card_sets.set_type IS 'Computer-readable classification for this set (core, expansion, masters, etc.)';
COMMENT ON COLUMN magic.card_sets.tcgplayer_id IS 'TCGplayer API groupId for this set';
COMMENT ON COLUMN magic.card_sets.uri IS 'Link to this set object on Scryfall''s API';

CREATE TABLE magic.artworks ();
