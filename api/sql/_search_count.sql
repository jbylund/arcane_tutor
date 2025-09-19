SELECT
    COUNT(1) AS total_cards
FROM
    magic.cards AS card
WHERE
    {where_clause}