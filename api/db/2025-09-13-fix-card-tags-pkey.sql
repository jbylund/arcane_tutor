-- Fix card_tags primary key to allow multiple parents per tag
-- This migration addresses the issue where cost-reducer-artifact can't have multiple parents
-- (synergy-artifact and cost-reducer) due to the unique constraint on tag column

BEGIN;

-- Drop the existing primary key constraint
ALTER TABLE magic.card_tags DROP CONSTRAINT card_tags_pkey;

-- Add a new composite primary key that allows a tag to have multiple parents
ALTER TABLE magic.card_tags 
ADD CONSTRAINT card_tags_pkey PRIMARY KEY (tag, parent_tag);

-- Add a unique constraint on tag where parent_tag is NULL to prevent duplicate root tags
-- This ensures that root tags (tags without parents) remain unique
CREATE UNIQUE INDEX idx_card_tags_unique_root 
ON magic.card_tags (tag) 
WHERE parent_tag IS NULL;

COMMIT;