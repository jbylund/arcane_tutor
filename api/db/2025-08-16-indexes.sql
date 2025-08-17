CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_cards_card_name_trgm
   ON magic.cards
   USING gin (card_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_cards_creature_power
   ON magic.cards
   USING btree (creature_power)
   WHERE creature_power IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cards_creature_toughness
   ON magic.cards
   USING btree (creature_toughness)
   WHERE creature_toughness IS NOT NULL;
