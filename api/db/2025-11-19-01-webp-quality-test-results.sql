-- Migration: Add webp_quality_test_results table
-- This table stores user feedback from WebP quality testing

CREATE TABLE IF NOT EXISTS magic.webp_quality_test_results (
    id SERIAL PRIMARY KEY,
    card_name text NOT NULL,
    scryfall_id UUID NOT NULL,
    image_width integer NOT NULL,
    test_quality integer NOT NULL,
    picked_quality integer NOT NULL,
    correct boolean NOT NULL,
    created_at timestamp DEFAULT now(),
    CONSTRAINT valid_image_width CHECK (image_width IN (280, 388, 538, 745)),
    CONSTRAINT valid_test_quality CHECK (test_quality >= 30 AND test_quality <= 80 AND test_quality % 5 = 0),
    CONSTRAINT valid_picked_quality CHECK (picked_quality IN (85, test_quality))
);

CREATE INDEX IF NOT EXISTS idx_webp_quality_test_results_image_width ON magic.webp_quality_test_results (image_width);
CREATE INDEX IF NOT EXISTS idx_webp_quality_test_results_test_quality ON magic.webp_quality_test_results (image_width, test_quality);
CREATE INDEX IF NOT EXISTS idx_webp_quality_test_results_scryfall_id ON magic.webp_quality_test_results (scryfall_id);

COMMENT ON TABLE magic.webp_quality_test_results IS 'Stores user feedback from WebP quality testing to determine optimal compression settings';
COMMENT ON COLUMN magic.webp_quality_test_results.card_name IS 'Name of the card being tested';
COMMENT ON COLUMN magic.webp_quality_test_results.scryfall_id IS 'Scryfall UUID of the card';
COMMENT ON COLUMN magic.webp_quality_test_results.image_width IS 'Image width in pixels (280, 388, 538, or 745)';
COMMENT ON COLUMN magic.webp_quality_test_results.test_quality IS 'Test quality level (30-80 in steps of 5)';
COMMENT ON COLUMN magic.webp_quality_test_results.picked_quality IS 'Quality level the user selected (either 85 or test_quality)';
COMMENT ON COLUMN magic.webp_quality_test_results.correct IS 'Whether the user correctly identified the higher quality (85%) image';
