INSERT INTO magic.tags (tag)
VALUES (%(tag)s)
ON CONFLICT (tag) DO NOTHING