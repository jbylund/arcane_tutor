SELECT
    child_tag,
    parent_tag
FROM
    magic.tag_relationships
ORDER BY
    child_tag,
    parent_tag