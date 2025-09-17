-- Minimal test schema for integration tests
-- Based on the main schema but simplified for testing

DROP SCHEMA IF EXISTS magic CASCADE;
CREATE SCHEMA IF NOT EXISTS magic;

-- Essential functions needed for the schema
CREATE OR REPLACE FUNCTION is_sorted_alphabetically(arr jsonb)
RETURNS boolean AS $$
BEGIN
    RETURN arr = (
        SELECT jsonb_agg(value ORDER BY value)
        FROM jsonb_array_elements_text(arr)
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION all_elements_initcap(arr jsonb)
RETURNS boolean AS $$
BEGIN
    RETURN NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(arr) AS element
        WHERE element != initcap(element)
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION json_array_to_array(jsonbin jsonb)
RETURNS text[] as
$$
  SELECT array_agg(el) FROM jsonb_array_elements_text(jsonbin) el;
$$ LANGUAGE sql IMMUTABLE;

-- Essential tables for testing
CREATE TABLE IF NOT EXISTS magic.cards (
    card_name text NOT NULL,
    cmc integer,
    mana_cost_text text,
    mana_cost_jsonb jsonb,
    raw_card_blob jsonb NOT NULL,
    card_types jsonb NOT NULL,
    card_subtypes jsonb,
    card_colors jsonb NOT NULL,
    card_color_identity jsonb NOT NULL,
    card_keywords jsonb NOT NULL,
    oracle_text text,
    edhrec_rank integer,
    creature_power integer,
    creature_power_text text,
    creature_toughness integer,
    creature_toughness_text text,
    PRIMARY KEY (card_name)
);

-- Tags table for testing tagging functionality
CREATE TABLE IF NOT EXISTS magic.tags (
    tag text PRIMARY KEY,
    description text
);

-- Card tags junction table
CREATE TABLE IF NOT EXISTS magic.card_tags (
    card_name text NOT NULL,
    tag text NOT NULL,
    PRIMARY KEY (card_name, tag),
    FOREIGN KEY (card_name) REFERENCES magic.cards(card_name),
    FOREIGN KEY (tag) REFERENCES magic.tags(tag)
);

-- Migrations table to track schema versions
CREATE TABLE IF NOT EXISTS migrations (
    file_name text not null,
    file_sha256 text not null,
    date_applied timestamp default now(),
    file_contents text not null,
    UNIQUE (file_name)
);

-- Insert test migration record
INSERT INTO migrations (file_name, file_sha256, file_contents) 
VALUES ('test_schema.sql', 'test123', 'Test schema for integration tests')
ON CONFLICT (file_name) DO NOTHING;