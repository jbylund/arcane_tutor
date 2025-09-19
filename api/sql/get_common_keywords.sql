WITH card_keywords AS (
    SELECT
        jsonb_object_keys(card_keywords) as keyword_name
    FROM
        magic.cards
    WHERE
        card_keywords IS NOT NULL
        AND jsonb_typeof(card_keywords) = 'object'
),
with_min_count AS (
    SELECT
        keyword_name,
        count(1) as num_occurrences
    FROM card_keywords
    GROUP BY keyword_name
    HAVING count(1) >= 5
)
SELECT
    keyword_name AS k,
    num_occurrences AS n
FROM
    with_min_count
ORDER BY
    keyword_name