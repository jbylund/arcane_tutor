SELECT
    *
FROM
    magic.cards
WHERE
    (%(min_name)s::text IS NULL OR %(min_name)s::text < card_name) AND
    (%(max_name)s::text IS NULL OR card_name < %(max_name)s::text)
ORDER BY
    card_name
LIMIT
    %(limit)s