WITH card_types AS (
    SELECT
        jsonb_array_elements_text(face_types) as type_name
    FROM
        magic.card_faces
    WHERE
        face_types IS NOT NULL
),
card_subtypes AS (
    SELECT
        jsonb_array_elements_text(face_subtypes) as subtype_name
    FROM
        magic.card_faces
    WHERE
        face_subtypes IS NOT NULL
),
card_types_and_subtypes AS (
    SELECT
        type_name
    FROM card_types
    UNION ALL
    SELECT
        subtype_name
    FROM card_subtypes
),
with_min_count AS (
    SELECT
        type_name,
        count(1) as num_occurrences
    FROM card_types_and_subtypes
    GROUP BY type_name
    HAVING count(1) >= 5
)
SELECT
    type_name AS t,
    num_occurrences AS n
FROM
    with_min_count
ORDER BY
    type_name
