-- Add card_tags field to existing magic.cards table
ALTER TABLE magic.cards
ADD COLUMN card_tags jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Add constraint to ensure card_tags is a proper object
ALTER TABLE magic.cards
ADD CONSTRAINT card_tags_must_be_object CHECK (jsonb_typeof(card_tags) = 'object');

-- Create table for tag hierarchy
CREATE TABLE IF NOT EXISTS magic.card_tags (
    tag text NOT NULL,
    parent_tag text,

    -- constraints
    CONSTRAINT card_tags_pkey PRIMARY KEY (tag),
    CONSTRAINT card_tags_parent_fkey FOREIGN KEY (parent_tag) REFERENCES magic.card_tags(tag) ON DELETE SET NULL
);

-- Create index for efficient parent tag lookups
CREATE INDEX idx_card_tags_parent ON magic.card_tags (parent_tag);
