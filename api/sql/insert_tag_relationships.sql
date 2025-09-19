INSERT INTO magic.tag_relationships
    (child_tag, parent_tag)
VALUES
    (%(child_tag)s, %(parent_tag)s)
ON CONFLICT (child_tag, parent_tag)
DO NOTHING