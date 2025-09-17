-- Minimal test data for integration tests

-- Insert some test cards
INSERT INTO magic.cards (
    card_name, cmc, mana_cost_text, mana_cost_jsonb, raw_card_blob,
    card_types, card_subtypes, card_colors, card_color_identity, card_keywords,
    oracle_text, creature_power, creature_toughness
) VALUES 
(
    'Lightning Bolt', 
    1, 
    '{R}', 
    '{"R": 1}',
    '{"name": "Lightning Bolt", "type": "Instant"}',
    '["Instant"]',
    '[]',
    '{"R": true}',
    '{"R": true}',
    '{}',
    'Lightning Bolt deals 3 damage to any target.',
    NULL,
    NULL
),
(
    'Serra Angel',
    5,
    '{3}{W}{W}',
    '{"3": 3, "W": 2}',
    '{"name": "Serra Angel", "type": "Creature"}',
    '["Creature"]',
    '["Angel"]',
    '{"W": true}',
    '{"W": true}',
    '{"Flying": true, "Vigilance": true}',
    'Flying, vigilance',
    4,
    4
),
(
    'Black Lotus',
    0,
    '{0}',
    '{}',
    '{"name": "Black Lotus", "type": "Artifact"}',
    '["Artifact"]',
    '[]',
    '{}',
    '{}',
    '{}',
    '{T}, Sacrifice Black Lotus: Add three mana of any one color.',
    NULL,
    NULL
) ON CONFLICT (card_name) DO NOTHING;

-- Insert test tags
INSERT INTO magic.tags (tag, description) VALUES 
('flying', 'Creature has flying ability'),
('vigilance', 'Creature has vigilance ability'),
('burn', 'Direct damage spell'),
('mana-acceleration', 'Provides extra mana')
ON CONFLICT (tag) DO NOTHING;

-- Insert card-tag relationships
INSERT INTO magic.card_tags (card_name, tag) VALUES 
('Serra Angel', 'flying'),
('Serra Angel', 'vigilance'),
('Lightning Bolt', 'burn'),
('Black Lotus', 'mana-acceleration')
ON CONFLICT (card_name, tag) DO NOTHING;