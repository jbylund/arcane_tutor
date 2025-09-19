INSERT INTO magic.cards
(
    card_name,               -- 1
    cmc,                     -- 2
    mana_cost_text,          -- 3
    mana_cost_jsonb,         -- 4
    card_types,              -- 5
    card_subtypes,           -- 6
    card_colors,             -- 7
    card_color_identity,     -- 8
    card_keywords,           -- 9
    creature_power,          -- 10
    creature_power_text,     -- 11
    creature_toughness,      -- 12
    creature_toughness_text, -- 13
    edhrec_rank,             -- 14
    price_usd,               -- 15
    price_eur,               -- 16
    price_tix,               -- 17
    oracle_text,             -- 18
    raw_card_blob            -- 19
)
SELECT
    card_blob->>'name' AS card_name, -- 1
    (card_blob->>'cmc')::float::integer AS cmc, -- 2
    card_blob->>'mana_cost' AS mana_cost_text, -- 3
    card_blob->'mana_cost' AS mana_cost_jsonb, -- 4
    card_blob->'card_types' AS card_types, -- 5
    card_blob->'card_subtypes' AS card_subtypes, -- 6
    card_blob->'card_colors' AS card_colors, -- 7
    card_blob->'card_color_identity' AS card_color_identity, -- 8
    card_blob->'card_keywords' AS card_keywords, -- 9
    (card_blob->>'power_numeric')::integer AS creature_power, -- 10
    card_blob->>'power' AS creature_power_text, -- 11
    (card_blob->>'toughness_numeric')::integer AS creature_toughness, -- 12
    card_blob->>'toughness' AS creature_toughness_text, -- 13
    (card_blob->>'edhrec_rank')::integer AS edhrec_rank, -- 14
    (card_blob->>'price_usd')::real AS price_usd, -- 15
    (card_blob->>'price_eur')::real AS price_eur, -- 16
    (card_blob->>'price_tix')::real AS price_tix, -- 17
    card_blob->>'oracle_text' AS oracle_text, -- 18
    card_blob AS raw_card_blob -- 19
FROM
    {staging_table_name}
ON CONFLICT (card_name) DO NOTHING