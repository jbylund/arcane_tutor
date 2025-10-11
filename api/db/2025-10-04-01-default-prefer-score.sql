-- Migration: Add default prefer score calculation
-- This migration adds prefer_score columns to the magic.cards table
-- based on various card attributes to implement the default prefer ordering
-- Run the backfill_prefer_scores.sql script to populate the scores

-- Add prefer_score columns
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS prefer_score_components jsonb;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS prefer_score real;

-- Create index for efficient sorting by prefer_score
CREATE INDEX IF NOT EXISTS idx_cards_prefer_score ON magic.cards (prefer_score DESC NULLS LAST);

