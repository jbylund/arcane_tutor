-- create table for tag definitions
CREATE TABLE IF NOT EXISTS magic.tags (
    tag text NOT NULL,
    -- constraints
    CONSTRAINT tags_pkey PRIMARY KEY (tag)
);

-- Create table for tag hierarchy
CREATE TABLE IF NOT EXISTS magic.tag_relationships (
    child_tag text NOT NULL,
    parent_tag text NOT NULL,

    -- constraints
    PRIMARY KEY (child_tag, parent_tag),
    FOREIGN KEY (child_tag) REFERENCES magic.tags(tag) ON DELETE CASCADE,
    FOREIGN KEY (parent_tag) REFERENCES magic.tags(tag) ON DELETE CASCADE,
    CONSTRAINT no_self_reference CHECK (child_tag != parent_tag)
);


-- 3. Function to prevent circular references
CREATE OR REPLACE FUNCTION check_circular_reference()
RETURNS TRIGGER AS $$
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
$$ LANGUAGE plpgsql;



-- 4. Trigger to prevent circular references
CREATE TRIGGER prevent_circular_references
    BEFORE INSERT OR UPDATE ON magic.tag_relationships
    FOR EACH ROW
    EXECUTE FUNCTION check_circular_reference();


-- 5. Indexes for efficient queries
CREATE INDEX idx_tag_relationships_child ON magic.tag_relationships (child_tag);
CREATE INDEX idx_tag_relationships_parent ON magic.tag_relationships (parent_tag);


-- 6. View for root tags (tags that don't appear as children)
CREATE OR REPLACE VIEW magic.root_tags AS
SELECT
    t.tag
FROM
    magic.tags t
WHERE t.tag NOT IN (
    SELECT DISTINCT child_tag
    FROM magic.tag_relationships
    WHERE child_tag IS NOT NULL
)
ORDER BY t.tag;


-- 7. View for leaf tags (tags that don't appear as parents)
CREATE OR REPLACE VIEW magic.leaf_tags AS
SELECT
    t.tag
FROM
    magic.tags t
WHERE
    t.tag NOT IN (
    SELECT DISTINCT parent_tag
    FROM magic.tag_relationships
    WHERE parent_tag IS NOT NULL
)
ORDER BY t.tag;


-- 8. Function to get all ancestors of a tag
CREATE OR REPLACE FUNCTION get_tag_ancestors(target_tag text)
RETURNS TABLE(tag text, level integer) AS $$
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
$$ LANGUAGE plpgsql;


-- 9. Function to get all descendants of a tag
CREATE OR REPLACE FUNCTION get_tag_descendants(target_tag text)
RETURNS TABLE(tag text, level integer) AS $$
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
$$ LANGUAGE plpgsql;
