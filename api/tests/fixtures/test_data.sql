-- Minimal test data for integration tests

-- Insert some test cards
INSERT INTO magic.cards (
    card_name, cmc, mana_cost_text, mana_cost_jsonb, raw_card_blob,
    card_types, card_subtypes, card_colors, card_color_identity, card_keywords,
    oracle_text, creature_power, creature_toughness, card_oracle_tags, collector_number, collector_number_int
) VALUES 
(
    'Lightning Bolt', 
    1, 
    '{R}', 
    '{"R": 1}',
    '{"name": "Lightning Bolt", "type": "Instant", "collector_number": "123"}',
    '["Instant"]',
    '[]',
    '{"R": true}',
    '{"R": true}',
    '{}',
    'Lightning Bolt deals 3 damage to any target.',
    NULL,
    NULL,
    '{"burn": true}',
    '123',
    123
),
(
    'Serra Angel',
    5,
    '{3}{W}{W}',
    '{"3": 3, "W": 2}',
    '{"name": "Serra Angel", "type": "Creature", "collector_number": "45a"}',
    '["Creature"]',
    '["Angel"]',
    '{"W": true}',
    '{"W": true}',
    '{"Flying": true, "Vigilance": true}',
    'Flying, vigilance',
    4,
    4,
    '{"flying": true, "vigilance": true}',
    '45a',
    NULL
),
(
    'Black Lotus',
    0,
    '{0}',
    '{}',
    '{"name": "Black Lotus", "type": "Artifact", "collector_number": "1"}',
    '["Artifact"]',
    '[]',
    '{}',
    '{}',
    '{}',
    '{T}, Sacrifice Black Lotus: Add three mana of any one color.',
    NULL,
    NULL,
    '{"mana-acceleration": true}',
    '1',
    1
) ON CONFLICT (card_name) DO NOTHING;

-- Insert test tags
INSERT INTO magic.tags (tag) VALUES 
('flying'),
('vigilance'),
('burn'),
('mana-acceleration')
ON CONFLICT (tag) DO NOTHING;