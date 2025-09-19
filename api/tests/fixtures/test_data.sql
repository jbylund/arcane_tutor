-- Minimal test data for integration tests

-- Insert some test cards
INSERT INTO magic.cards (
    card_name, cmc, mana_cost_text, mana_cost_jsonb, raw_card_blob,
    card_types, card_subtypes, card_colors, card_color_identity, card_keywords,
    oracle_text, creature_power, creature_toughness, card_oracle_tags
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
    NULL,
    '{"burn": true}'
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
    4,
    '{"flying": true, "vigilance": true}'
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
    NULL,
    '{"mana-acceleration": true}'
) ON CONFLICT (card_name) DO NOTHING;

-- Insert test tags
INSERT INTO magic.tags (tag) VALUES 
('flying'),
('vigilance'),
('burn'),
('mana-acceleration')
ON CONFLICT (tag) DO NOTHING;