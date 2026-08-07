-- Keywords are now stored lowercase (api/card_processing.py) so `keyword:` can reach the 131
-- keywords Scryfall does not spell in Title Case -- "First strike", "Doctor's companion", etc.
-- A bulk reimport would also rewrite these rows, but this runs in seconds and lets the query-side
-- change deploy without a window where every `keyword:` returns nothing.
UPDATE magic.cards
SET card_keywords = (
    SELECT jsonb_object_agg(lower(key), value)
    FROM jsonb_each(card_keywords)
)
WHERE card_keywords <> '{}'::jsonb
  AND card_keywords <> (
      SELECT jsonb_object_agg(lower(key), value)
      FROM jsonb_each(card_keywords)
  );
