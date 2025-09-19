SELECT
    card_name AS name,
    mana_cost_text AS mana_cost,
    oracle_text AS oracle_text,
    raw_card_blob->>'set_name' AS set_name,
    raw_card_blob->>'type_line' AS type_line,
    raw_card_blob->'image_uris'->>'small' AS image_small,
    raw_card_blob->'image_uris'->>'normal' AS image_normal,
    raw_card_blob->'image_uris'->>'large' AS image_large
FROM
    magic.cards AS card
WHERE
    {where_clause}
ORDER BY
    {sql_orderby} {sql_direction} NULLS LAST,
    edhrec_rank ASC NULLS LAST
LIMIT
    {limit}