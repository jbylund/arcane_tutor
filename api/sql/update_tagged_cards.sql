UPDATE magic.cards
SET card_oracle_tags = card_oracle_tags || %(new_tag)s::jsonb
WHERE card_name = ANY(%(card_names)s)