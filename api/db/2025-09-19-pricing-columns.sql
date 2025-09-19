-- Add pricing data columns to the cards table
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS price_usd real;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS price_eur real;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS price_tix real;

-- Add comments for the new columns
COMMENT ON COLUMN magic.cards.price_usd IS 'Price in US Dollars - will be null for cards without pricing information';
COMMENT ON COLUMN magic.cards.price_eur IS 'Price in Euros - will be null for cards without pricing information';
COMMENT ON COLUMN magic.cards.price_tix IS 'Price in MTGO Tickets - will be null for cards without pricing information';

-- Populate pricing columns from existing raw card blob data
UPDATE magic.cards SET
    price_usd = (raw_card_blob->'prices'->>'usd')::real,
    price_eur = (raw_card_blob->'prices'->>'eur')::real,
    price_tix = (raw_card_blob->'prices'->>'tix')::real
WHERE raw_card_blob->'prices' IS NOT NULL;

-- Indexes for pricing data performance
CREATE INDEX IF NOT EXISTS idx_cards_price_usd ON magic.cards (price_usd) WHERE price_usd IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cards_price_eur ON magic.cards (price_eur) WHERE price_eur IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cards_price_tix ON magic.cards (price_tix) WHERE price_tix IS NOT NULL;