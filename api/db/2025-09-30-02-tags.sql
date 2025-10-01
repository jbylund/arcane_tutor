SET SEARCH_PATH TO magic;


CREATE FUNCTION magic.check_circular_reference() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Check if adding this relationship would create a cycle
    IF EXISTS (
        WITH RECURSIVE hierarchy AS (
            SELECT NEW.parent_tag as tag, 1 as depth
            UNION ALL
            SELECT tr.parent_tag, h.depth + 1
            FROM magic.tag_relationships tr
            JOIN hierarchy h ON tr.child_tag = h.tag
            WHERE h.depth < 100 -- prevent infinite recursion
        )
        SELECT 1 FROM hierarchy WHERE tag = NEW.child_tag
    ) THEN
        RAISE EXCEPTION 'Circular reference detected: % -> %', NEW.child_tag, NEW.parent_tag;
    END IF;

    RETURN NEW;
END;
$$;


CREATE FUNCTION magic.get_tag_ancestors(target_tag text) RETURNS TABLE(tag text, level integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE ancestors AS (
        -- Base case: the tag itself
        SELECT target_tag as tag, 0 as level
        UNION ALL
        -- Recursive case: parent tags
        SELECT tr.parent_tag, a.level + 1
        FROM magic.tag_relationships tr
        JOIN ancestors a ON tr.child_tag = a.tag
        WHERE a.level < 100 -- prevent infinite recursion
    )
    SELECT a.tag, a.level
    FROM ancestors a
    WHERE a.tag != target_tag -- exclude the tag itself
    ORDER BY a.level;
END;
$$;


CREATE FUNCTION magic.get_tag_descendants(target_tag text) RETURNS TABLE(tag text, level integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE descendants AS (
        -- Base case: the tag itself
        SELECT target_tag as tag, 0 as level
        UNION ALL
        -- Recursive case: child tags
        SELECT tr.child_tag, d.level + 1
        FROM magic.tag_relationships tr
        JOIN descendants d ON tr.parent_tag = d.tag
        WHERE d.level < 100 -- prevent infinite recursion
    )
    SELECT d.tag, d.level
    FROM descendants d
    WHERE d.tag != target_tag -- exclude the tag itself
    ORDER BY d.level;
END;
$$;


CREATE TABLE magic.tags (
    tag text NOT NULL,
    PRIMARY KEY (tag)
);

CREATE TABLE magic.tag_relationships (
    child_tag text NOT NULL REFERENCES magic.tags(tag),
    parent_tag text NOT NULL REFERENCES magic.tags(tag),
    PRIMARY KEY (child_tag, parent_tag),
    CONSTRAINT no_self_reference CHECK ((child_tag <> parent_tag))
);

CREATE INDEX IF NOT EXISTS idx_tag_relationships_child ON magic.tag_relationships USING btree (child_tag);
CREATE INDEX IF NOT EXISTS idx_tag_relationships_parent ON magic.tag_relationships USING btree (parent_tag);

CREATE VIEW magic.leaf_tags AS
 SELECT tag
   FROM magic.tags t
  WHERE (NOT (tag IN ( SELECT DISTINCT tag_relationships.parent_tag
           FROM magic.tag_relationships
          WHERE (tag_relationships.parent_tag IS NOT NULL))))
  ORDER BY tag;

CREATE VIEW magic.root_tags AS
 SELECT tag
   FROM magic.tags t
  WHERE (NOT (tag IN ( SELECT DISTINCT tag_relationships.child_tag
           FROM magic.tag_relationships
          WHERE (tag_relationships.child_tag IS NOT NULL))))
  ORDER BY tag;

CREATE TRIGGER prevent_circular_references BEFORE INSERT OR UPDATE ON magic.tag_relationships FOR EACH ROW EXECUTE FUNCTION magic.check_circular_reference();
